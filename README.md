# ClawEmail Web Manager

基于 `claw.163.com` 的 **子邮箱批量管理 / 实时收发** 一体化前后端。
通过 Web UI 验证码登录 Claw，自动派生 Dashboard Cookie 与 API Key，为每个子邮箱维持长连接监听，新邮件实时入库并经 SSE 推送给前端，可在线发件、回复、删除（远端 + 本地双删）、下载附件。

仓库结构：

```text
backend/
  app/                   FastAPI 后端（SQLite + Claw/Duck/Telegram 集成）
    api/                 HTTP 路由，保持旧版 /api/* 响应结构兼容
    core/                配置、日志、数据库会话
    repositories/        SQLite 数据访问层
    services/            业务服务与外部接口封装
    main.py              FastAPI 应用入口，可直接由 IDE 启动
  requirements.txt       后端生产运行依赖
  requirements-dev.txt   后端开发与测试依赖
frontend/
  app/                   Next.js App Router 前端
    App.tsx              主界面：登录、邮箱、邮件、Duck、通知、设置
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
app_settings   key/value，存 Claw 凭据、监听设置、系统代理与 Telegram 配置
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

`.env` 只保留启动配置和本地数据库路径。Claw、系统代理、Telegram 等业务配置通过 Web 的“系统设置”写入 SQLite，不再放到 `.env`。

## 8. 本地运行

本地开发默认由前端目录的 `npm run dev` 同时启动 FastAPI 后端和 Next.js 前端。后端默认监听 `127.0.0.1:8000`，前端默认监听 `0.0.0.0:3001`，前端通过 `BACKEND_URL` 将 `/api/*` 转发到后端；`/health` 由前后端各自本地响应，避免健康检查依赖代理链路。

```powershell
cd backend
pip install -r requirements-dev.txt
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

如果容器无法直连 DuckDuckGo 或 Telegram，可以在 Web 的“系统设置”里填写系统代理地址。

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
- `PATCH /api/duck/addresses/:id/openai-password`
- `GET /api/telegram/settings`
- `PUT /api/telegram/settings`
- `POST /api/telegram/send`

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

## 致谢与来源

本项目最初 fork 自 [WangXingFan/ClawEmail](https://github.com/WangXingFan/ClawEmail)，后续已独立维护，并在此基础上进行了功能扩展、架构调整和前后端迁移。

感谢原作者提供的项目基础，也感谢 [Linux.do](https://linux.do) 社区。
