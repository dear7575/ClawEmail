# ClawEmail Web Manager

基于 `claw.163.com` 的 **子邮箱批量管理 / 实时收发** 一体化前后端。
通过 Web UI 验证码登录 Claw，自动派生 Dashboard Cookie 与 API Key，为每个子邮箱维持长连接监听，新邮件实时入库并经 SSE 推送给前端，可在线发件、回复、删除（远端 + 本地双删）、下载附件。

仓库结构：

```text
backend/
  app/                   FastAPI 后端（SQLite + Claw/Duck/Sub2/Telegram 集成）
    api/                 HTTP 路由，保持旧版 /api/* 响应结构兼容
    core/                配置、日志、数据库会话
    repositories/        SQLite 数据访问层
    services/            业务服务与外部接口封装
    main.py              FastAPI 应用入口，可直接由 IDE 启动
  requirements.txt       后端运行依赖
frontend/
  app/                   Next.js App Router 前端
    App.tsx              主界面：登录、邮箱、邮件、Duck、Sub2、通知、设置
    api.ts               前端调用层（统一 X-Admin-Password / ?token=）
    components/          收件箱、邮箱、弹窗等页面组件
  next.config.mjs        /api/* 转发到 FastAPI
scripts/
  docker-entrypoint.sh   Docker 单镜像启动脚本
```

## 1. 功能矩阵

| 模块 | 能力 | 实现位置 |
|---|---|---|
| Claw 绑定 | 邮箱 + 验证码两步登录；自动取 `auth/me` / `workspaces` / `mailboxes` / `api-keys`；写入 SQLite | `backend/app/api/claw_auth.py`、`backend/app/services/claw_auth.py` |
| Claw 邮箱 | 创建（前缀 `^[a-z0-9]{1,32}$`）、列表、`?sync=true` 与远端做差量同步、删除（拒绝删主邮箱） | `backend/app/api/mailboxes.py`、`backend/app/services/claw_dashboard.py` |
| 通讯规则 | 同步并保存 `commLevel` / `extReceiveType` / `extSendType`；邮箱页可配置个人 / 内部 / 外部通信范围 | `backend/app/api/mailboxes.py`、`frontend/app/components/CommunicationRulesDrawer.tsx` |
| 实时收件 | 每个 `active` 邮箱一条监听状态；邮件入库为 `mails` + `attachments`；SSE `event: mail` 推送 | `backend/app/services/listeners.py`、`backend/app/services/sse.py` |
| 收件同步 | `GET /api/mails?sync=true`：远端 INBOX `id` 列表 → 删本地多余、补本地缺失 | `backend/app/api/mails.py`、`backend/app/services/mails.py` |
| 邮件详情 | 返回行 + 解析后的原始 JSON + 附件元数据 | `backend/app/api/mails.py` |
| 删信 | 远端删除 + 本地行删除 | `backend/app/services/claw_mail.py`、`backend/app/api/mails.py` |
| 发件 | 仅允许 `from` 是本地已管理邮箱 | `backend/app/api/send.py` |
| 回信 | 基于本地 `mailId` 反查 `provider_mail_id` 调远端接口 | `backend/app/api/send.py` |
| 附件下载 | 不缓存原始字节，按需流式拉取 | `backend/app/api/mails.py` |
| 监听器诊断 | `/api/listeners` 输出 `email/connected/retry`；前端有侧栏摘要 + 抽屉详情 | `backend/app/api/events.py`、`frontend/app/components/ListenersDrawer.tsx` |
| Duck 邮箱 | 保存 DuckDuckGo Email Protection Bearer Token；调用非官方邮箱生成接口；记录已生成 `@duck.com` 邮箱、备注和预期转发目标 | `backend/app/api/duck.py`、`backend/app/services/duck.py`、`frontend/app/App.tsx` |
| 消息通知 | 配置 Telegram Bot Token / Chat ID；在“消息通知”页面手动发送文本 | `backend/app/api/telegram.py`、`backend/app/services/telegram.py` |
| 账号推送 | 粘贴 `temp/test.json` 结构，转换为 Sub2API 导入数据；按系统设置里的默认 OpenAI 分组推送 | `backend/app/api/sub2.py`、`backend/app/services/sub2.py` |
| 前端体验 | 中英双语、暗亮主题、拖拽栏宽（侧边栏 / 邮件列表）、登录态 localStorage 记忆 | `frontend/app/i18n.tsx`、`frontend/app/hooks.ts` |

## 2. Claw 验证码登录链

不收集任何 Claw 密码。`POST /api/auth/claw/verify-code` 内部串联以下接口：

```http
POST https://claw.163.com/mailserv-claw-dashboard/p/v1/auth/email/send-code
POST https://claw.163.com/mailserv-claw-dashboard/p/v1/auth/email/verify-code   → Set-Cookie: CLAW_SESS
GET  https://claw.163.com/mailserv-claw-dashboard/api/v1/auth/me
GET  https://claw.163.com/mailserv-claw-dashboard/api/v1/workspaces
GET  https://claw.163.com/mailserv-claw-dashboard/api/v1/mailboxes?workspaceId=<id>
GET  https://claw.163.com/mailserv-claw-dashboard/api/v1/api-keys
```

落库（SQLite `app_settings` 表）：

```text
claw.apiKey
claw.dashboardCookie
claw.userEmail
claw.workspaceId / claw.workspaceName
claw.parentMailboxId
claw.rootPrefix
claw.domain
```

`workspace` 取 `status=active`，`apiKey` 取 `defaultFlag=1` 优先。
绑定成功后会先 `stopAllMailboxListeners()` + `resetMailClients()` 再用新凭据 `startAllMailboxListeners()`，避免旧连接残留。

## 3. Dashboard 内部接口（仅后端调用）

| 用途 | 方法 / 路径 |
|---|---|
| 列出工作区下的邮箱树 | `GET /api/v1/mailboxes?workspaceId=<id>` |
| 创建子邮箱 | `POST /api/v1/mailboxes`（`{prefix, displayName, mailboxType:"sub", workspaceId, parentMailboxId}`） |
| 配置通讯规则 | `POST /api/v1/mailboxes/comm-settings?id=<mailboxId>`（`{commLevel, extReceiveType?, extSendType?}`） |
| 删除邮箱 | `POST /api/v1/mailboxes/delete?id=<mailboxId>` |

返回壳为 `{code, message, success, result}`，由 `parseDashboardResponse` 统一解包。

## 4. 本项目 HTTP API

### 4.1 鉴权

所有 `/api/*` 必须带：

```http
X-Admin-Password: <ADMIN_PASSWORD>
```

浏览器无法自定义头的场景（SSE、附件 `<a href>`）改用：

```http
?token=<ADMIN_PASSWORD>
```

`X-Admin-Password` 与 `query.token` 命中其一即放行（见 `backend/app/main.py` 中的鉴权中间件）。

### 4.2 端点清单

```http
GET    /health
GET    /api/auth/claw/status
POST   /api/auth/claw/send-code
POST   /api/auth/claw/verify-code
POST   /api/auth/claw/refresh
POST   /api/auth/claw/logout

GET    /api/mailboxes                # 仅本地
GET    /api/mailboxes?sync=true      # 与 Claw 做差量同步后再返回
POST   /api/mailboxes                # { suffix }
POST   /api/mailboxes/:id/comm-settings      # { commLevel, extReceiveType?, extSendType? }
DELETE /api/mailboxes/:id

GET    /api/mails?mailbox=&limit=50&offset=0
GET    /api/mails?sync=true&mailbox=...      # 远端 INBOX 全量比对
GET    /api/mails/:id                        # 标记已读，返回详情 + 解析后 JSON + 附件元数据
DELETE /api/mails/:id                        # 远端移到 Trash + 本地删除
GET    /api/mails/:id/attachments/:partId    # 流式下载附件

POST   /api/send                              # { from, to[], cc?, bcc?, subject?, body?, html? }
POST   /api/reply                             # { mailId, body?, html?, toAll? }

GET    /api/events                            # SSE: event: mail
GET    /api/listeners
GET    /api/listener-settings                 # { logMode, reconnectMode }
PUT    /api/listener-settings                 # { logMode: quiet|lifecycle|verbose, reconnectMode: standard|slow }

GET    /api/system/network-settings           # { proxyUrl, timeoutMs }
PUT    /api/system/network-settings           # { proxyUrl?, timeoutMs? }

GET    /api/duck/accounts
POST   /api/duck/accounts              # { label, token }，token 可填 "Bearer xxx" 或 "xxx"
PATCH  /api/duck/accounts/:id          # { token }，替换已保存 Token，保留地址记录
DELETE /api/duck/accounts/:id          # 本地删除 Token 及其生成记录，不调用 DuckDuckGo 远端
GET    /api/duck/addresses?accountId=
POST   /api/duck/accounts/:id/addresses # { forwardingMailboxEmail?, note? }
PATCH  /api/duck/addresses/:id          # { forwardingMailboxEmail?, note? }
DELETE /api/duck/addresses/:id          # 删除本地生成记录，不调用 DuckDuckGo 远端

GET    /api/telegram/settings            # { enabled, chatId, hasBotToken, botTokenPreview }
PUT    /api/telegram/settings            # { enabled?, botToken?, chatId? }
POST   /api/telegram/send                # { text }，手动发送文本到 Telegram

GET    /api/sub2/settings                 # { apiUrl, hasApiKey, apiKeyPreview, defaultGroupId }
PUT    /api/sub2/settings                 # { apiUrl?, apiKey?, defaultGroupId? }
GET    /api/sub2/groups                   # 拉取 OpenAI active 分组供页面选择
POST   /api/sub2/convert                  # { input }，仅转换预览，不访问 Sub2API
POST   /api/sub2/push                     # { input, groupId? }，未传 groupId 时使用默认推送分组
```

请求样例：

```jsonc
// POST /api/mailboxes
{ "suffix": "4" }

// POST /api/send
{
  "from": "vercel.4@claw.163.com",
  "to": ["target@example.com"],
  "cc": ["copy@example.com"],
  "subject": "hello",
  "body": "message body",
  "html": false
}

// POST /api/reply
{ "mailId": 123, "body": "reply body", "toAll": false, "html": false }
```

SSE 事件：

```text
event: mail
data: {"mailboxEmail":"vercel.4@claw.163.com","id":42,"providerMailId":"..."}
```

校验：所有入参经 Pydantic 解析；失败返回结构化 JSON 错误。

## 5. 数据持久化

SQLite 文件由 `DATABASE_PATH` 指定（默认 `./data/app.db`），开启 `journal_mode=WAL` + `foreign_keys=ON`。

```text
mailboxes      子邮箱：id / email(unique) / prefix / status / install_command / auth_url / comm_level ...
mails          邮件：mailbox_email + provider_mail_id 联合唯一，含 raw_json 全文和 read_at 已读时间
attachments    附件元数据：mail_id 外键 → mails.id（ON DELETE CASCADE）
app_settings   key/value，存 Claw 凭据、监听设置、系统代理、Telegram 与 Sub2API 配置
duck_accounts  DuckDuckGo Email Protection Token（仅后端使用，API 返回时脱敏）
duck_addresses 已生成的 Private Duck Address、备注、预期转发目标和原始响应
```

附件二进制**不入库**，下载时调 `client.mail.getAttachment` 流式回传给浏览器。

## 5.1 DuckDuckGo Email Protection 集成边界

Duck 邮箱是 DuckDuckGo Email Protection 的转发邮箱，官方没有提供稳定公开的第三方 API 文档；本项目只封装社区常用的邮箱生成请求：

```http
POST https://quack.duckduckgo.com/api/email/addresses
Authorization: Bearer <DDG_TOKEN>
```

成功响应通常为：

```json
{ "address": "example-private-address" }
```

本项目会规范化保存为 `example-private-address@duck.com`。Duck Token 属于敏感凭据，只保存在后端数据库中，前端只显示前后缀掩码。当前版本只支持**生成与本地记录**，不承诺远端停用、变更转发目标或读取 Duck 收件箱；Duck 邮箱没有独立收件箱，邮件会转发到你在 DuckDuckGo 里配置的真实邮箱。

## 5.2 Sub2API 账号推送

“账号推送”页面接收 `temp/test.json` 结构的 ChatGPT 账号 JSON，转换为 Sub2API 的账号导入数据。推送时优先使用 `SUB2_PROXY_TEMPLATE_JSON` 中的 `proxies`；未配置模板代理时，会从 Sub2API 当前可用代理列表获取一个 active 代理并绑定账号。`temp/toSub2.json` 只是示例输出格式，不作为运行时代理配置源。

推送流程：

1. 从“系统设置”读取 Sub2API 地址和 APIKey。
2. 调用 `GET /api/v1/admin/groups?page=1&page_size=1000&platform=openai&status=active` 获取 OpenAI 可用分组。
3. 在“系统设置”里保存默认推送分组。
4. 未配置 `SUB2_PROXY_TEMPLATE_JSON` 时，从 `GET /api/v1/admin/proxies?page=1&page_size=1&status=active&sort_by=id&sort_order=desc` 获取可用代理。
5. 调用 Sub2API 创建账号，账号写入 `group_ids: [默认分组ID]`。

Sub2API 地址可以填写根地址，例如 `https://sub2.example.com`，也可以填写完整 `/api/v1/admin/accounts/data` 导入地址。Admin API Key 会按 Sub2API 约定放到 `x-api-key` 请求头；如果配置值以 `Bearer ` 开头，则按 JWT 令牌放到 `Authorization` 请求头。

## 5.3 OpenAI 手机号注册测试流程

该流程仅用于测试 OpenAI 手机号注册链路，不作为当前正式自动化注册能力。测试目标是从手机号入口直接完成注册/登录合并流程，而不是先用邮箱注册后再处理 `add-phone` 补手机号分支。

预期测试路径：

1. 打开 `https://auth.openai.com/log-in?usernameKind=phone_number`。
2. 通过 `https://hero-sms.com/stubs/handler_api.php` 获取临时手机号；`.env` 中使用 `HERO_SMS_API_KEY` 保存 HeroSMS API Key，代码和日志不得输出明文 Key。
3. HeroSMS 使用 SMS-Activate 兼容接口；OpenAI 服务 code 为 `dr`。当前测试可优先使用英国号码，HeroSMS country 为 `16`，OpenAI 页面国家码选择 `GB` / `+44`，输入手机号时去掉 `44` 国家码，只填本地号码。
4. 在 OpenAI 手机号入口提交号码后，进入密码页。该链路用于测试时把该步骤视为“创建/提交测试密码”步骤，测试密码可先使用 `6174`。
5. 提交密码后，如果进入短信验证码页，则轮询 HeroSMS `getStatusV2` 获取验证码，收到验证码后回填 OpenAI 页面。
6. 验证成功后继续沿用现有 OpenAI 授权 JSON 保存和 Sub2API 推送流程；下游仍需确认授权 JSON 是否包含 `email`，否则当前 `convert_openai_oauth_to_sub2` 会拒绝转换。

HeroSMS 激活单处理规则：

- 成功收到验证码并完成验证后，调用 `setStatus&id=<activationId>&status=6` 标记完成。
- 未进入短信验证码页、流程失败或放弃测试时，调用 `setStatus&id=<activationId>&status=8` 取消激活。
- 若 HeroSMS 返回 `EARLY_CANCEL_DENIED`，需要等待最小激活时间后重试取消，避免号码长期占用。
- 测试过程不得在文档、日志或前端界面展示完整手机号；只保留 `activationId`、国家、服务 code 和脱敏号码用于排查。

2026-05-15 的手工验证记录：OpenAI 登录页存在“使用电话号码继续”入口，提交英国 HeroSMS 号码后会进入 `/log-in/password`。测试提交 `6174` 时页面返回 `Incorrect phone number or password`，未进入短信验证码页；相关 HeroSMS 激活单已取消，余额恢复。后续如果继续测试，应按上面的流程重新取号并重点确认密码提交后的短信验证码页是否出现。

## 6. 监听器与重连

`backend/app/services/listeners.py` 维护邮箱监听状态快照，`backend/app/services/sse.py` 提供 SSE 广播总线。

`/api/listeners` 当前返回字段：`{ connectionId, email, status, connected, retry, error }`。前端 `ListenersDrawer` 会展示运行状态、重试次数和异常摘要。

## 7. 环境变量

```env
NODE_ENV=production
HOST=127.0.0.1
PORT=8000
FRONTEND_HOST=0.0.0.0
FRONTEND_PORT=3000
BACKEND_URL=http://127.0.0.1:8000
LOG_LEVEL=info
ADMIN_PASSWORD=admin@123456
HOST_PORT=3000
DATA_DIR=./data
DATABASE_PATH=./data/app.db
```

`.env` 只保留启动配置和本地数据库路径。Claw、系统代理、Telegram、Sub2API 等业务配置通过 Web 的“系统设置”写入 SQLite，不再放到 `.env`。

## 8. 本地运行

本地开发默认由前端目录的 `npm run dev` 同时启动 FastAPI 后端和 Next.js 前端。后端默认监听 `127.0.0.1:8000`，前端默认监听 `0.0.0.0:3001`，前端通过 `BACKEND_URL` 将 `/api/*` 转发到后端；`/health` 由前后端各自本地响应，避免健康检查依赖代理链路。

```powershell
cd backend
pip install -r requirements.txt
python app/main.py
```

```powershell
cd frontend
npm install
npm run dev
```

仅调试前端静态界面、不需要 API 时可运行：

```bash
cd frontend
npm run dev:frontend
```

默认访问地址为 `http://localhost:3001`。生产 Docker 镜像内会同时启动两个进程，并只对外暴露前端端口 `3000`。

## 9. Docker 部署

服务器默认不构建源码，只拉取 GitHub Actions 发布到 GHCR 的镜像：

```text
ghcr.io/dear7575/clawemail:latest
```

镜像内同时运行 FastAPI 后端和 Next.js 前端：后端只监听容器内 `127.0.0.1:8000`，前端监听 `0.0.0.0:3000` 并转发 `/api/*` 到后端。宿主端口由 `HOST_PORT` 控制，SQLite 数据挂载到 `/app/data`。

### 服务器部署

```bash
git clone https://github.com/dear7575/ClawEmail.git
cd ClawEmail
cp .env.example .env
# 修改 .env 中的 ADMIN_PASSWORD
docker compose pull
docker compose up -d
curl http://localhost:3000/health
```

常用配置：

```env
ADMIN_PASSWORD=admin@123456
HOST_PORT=3000
LOG_LEVEL=info
DATA_DIR=./data
HOST=127.0.0.1
PORT=8000
FRONTEND_HOST=0.0.0.0
FRONTEND_PORT=3000
BACKEND_URL=http://127.0.0.1:8000
DATABASE_PATH=./data/app.db
```

如果容器无法直连 DuckDuckGo、Telegram 或 Sub2API，可以在 Web 的“系统设置”里填写系统代理地址。代理地址支持 `http://host:port` / `https://host:port`，例如 Docker Desktop 场景常见为 `http://host.docker.internal:7890`。

## 10. Python + Next.js 工作区

当前仓库使用前后端分离结构：

- `backend`：FastAPI 后端，提供 `/api/*`、`/health`、SQLite 数据访问和外部服务集成。
- `frontend`：Next.js + Tailwind 前端，页面调用同源 `/api/*`，由 `app/api/[...path]` 服务端路由转发到后端；前端 `/health` 本地响应，用于容器前端健康检查。

已迁移到 FastAPI 的兼容接口：

- `GET /health`
- `GET /api/auth/claw/status`
- `GET /api/connections`
- `GET /api/connections/:id`
- `GET /api/listeners`
- `GET /api/listener-settings`
- `PUT /api/listener-settings`
- `POST /api/auth/claw/send-code`
- `POST /api/auth/claw/verify-code`
- `POST /api/auth/claw/refresh`
- `POST /api/auth/claw/logout`
- `POST /api/connections/send-code`
- `POST /api/connections/verify-code`
- `POST /api/connections/:id/refresh`
- `POST /api/connections/:id/logout`
- `GET /api/mailboxes`
- `GET /api/mailboxes?sync=true`
- `POST /api/mailboxes`
- `POST /api/mailboxes/:id/comm-settings`
- `DELETE /api/mailboxes/:id`
- `GET /api/mails`
- `GET /api/mails?sync=true`
- `GET /api/mails/:id`
- `GET /api/mails/:id/attachments/:partId`
- `DELETE /api/mails`
- `DELETE /api/mails/:id`
- `POST /api/send`
- `POST /api/reply`
- `GET /api/events`
- `GET /api/system/network-settings`
- `PUT /api/system/network-settings`
- `GET /api/duck/network-settings`
- `PUT /api/duck/network-settings`
- `GET /api/duck/accounts`
- `POST /api/duck/accounts`
- `PATCH /api/duck/accounts/:id`
- `DELETE /api/duck/accounts/:id`
- `GET /api/duck/addresses`
- `POST /api/duck/accounts/:id/addresses`
- `PATCH /api/duck/addresses/:id`
- `DELETE /api/duck/addresses/:id`
- `GET /api/duck/addresses/:id/openai-password`
- `GET /api/duck/addresses/:id/openai-auth-json`
- `PATCH /api/duck/addresses/:id/openai-credentials`
- `GET /api/telegram/settings`
- `PUT /api/telegram/settings`
- `POST /api/telegram/send`
- `GET /api/sub2/settings`
- `PUT /api/sub2/settings`
- `GET /api/sub2/groups`
- `POST /api/sub2/convert`
- `POST /api/sub2/push`
- `POST /api/openai/duck-push-sub2`

后端启动：

```bash
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

前端启动：

```bash
cd frontend
npm run dev
```

更新版本：

```bash
git pull
docker compose pull
docker compose up -d
```

### 本地构建镜像

只有需要在当前机器构建镜像时才使用覆盖文件：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

### 手动运行镜像

```bash
docker run -d --name clawemail \
  -p 3000:3000 \
  -e ADMIN_PASSWORD=admin@123456 \
  -e DATABASE_PATH=/app/data/app.db \
  -v $PWD/data:/app/data \
  ghcr.io/dear7575/clawemail:latest
```

`./data` 挂到 `/app/data` 持久化 SQLite。GitHub Actions 会在推送 `main` 后自动构建并推送 `latest` 镜像。

## 致谢

感谢 [Linux.do](https://linux.do) 社区。
