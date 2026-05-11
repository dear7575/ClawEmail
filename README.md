# ClawEmail Web Manager

基于 `claw.163.com` 的 **子邮箱批量管理 / 实时收发** 一体化前后端。
通过 Web UI 验证码登录 Claw，自动派生 Dashboard Cookie 与 API Key，为每个子邮箱维持长连接监听，新邮件实时入库并经 SSE 推送给前端，可在线发件、回复、删除（远端 + 本地双删）、下载附件。

仓库结构：

```text
src/
  server/                Fastify 5 后端（SQLite + Claw SDK + Dashboard 内部接口）
    config.ts            环境变量解析（zod）
    db.ts                better-sqlite3 schema 与 DAO
    runtime-config.ts    运行时凭据（优先读 SQLite，再回退 .env）
    claw-dashboard.ts    Claw Dashboard 内部 HTTP 接口封装
    claw-mail.ts         @clawemail/node-sdk 客户端池 + 发件/回信/删信/列表
    listener-manager.ts  每邮箱 WS 长连接 + 指数退避重连
    sse.ts               SSE 广播总线
    routes/              auth / mailboxes / mails / send / events
    index.ts             Fastify 启动 + 静态托管前端
  web/                   Vite 7 + React 19 单页应用
    src/App.tsx          主壳：登录/路由/连接卡/工具栏
    src/api.ts           前端调用层（统一 X-Admin-Password / ?token=）
    src/i18n.tsx         中英双语 + 暗亮主题
    src/components/      InboxView / MailboxesView / ComposeDrawer / ListenersDrawer / ListenersView
    src/hooks.ts         可拖拽栏宽（localStorage 持久化）
```

## 1. 功能矩阵

| 模块 | 能力 | 实现位置 |
|---|---|---|
| Claw 绑定 | 邮箱 + 验证码两步登录；自动取 `auth/me` / `workspaces` / `mailboxes` / `api-keys`；写入 SQLite | `routes/claw-auth.ts`、`runtime-config.ts` |
| Claw 邮箱 | 创建（前缀 `^[a-z0-9]{1,32}$`）、列表、`?sync=true` 与远端做差量同步、删除（拒绝删主邮箱） | `routes/mailboxes.ts`、`claw-dashboard.ts` |
| 通讯规则 | 同步并保存 `commLevel` / `extReceiveType` / `extSendType`；邮箱页可配置个人 / 内部 / 外部通信范围 | `routes/mailboxes.ts`、`CommunicationRulesDrawer.tsx` |
| 实时收件 | 每个 `active` 邮箱一条 WS 监听；落库为 `mails` + `attachments`；SSE `event: mail` 推送 | `listener-manager.ts`、`sse.ts` |
| 收件同步 | `GET /api/mails?sync=true`：远端 INBOX `id` 列表 → 删本地多余、补本地缺失 | `routes/mails.ts` |
| 邮件详情 | 返回行 + 解析后的原始 JSON + 附件元数据 | `routes/mails.ts` |
| 删信 | SDK `moveMessages([id], "Trash")` 远端删除 + 本地行删除 | `claw-mail.ts`、`routes/mails.ts` |
| 发件 | 仅允许 `from` 是本地已管理邮箱 | `routes/send.ts` |
| 回信 | 基于本地 `mailId` 反查 `provider_mail_id` 调 SDK | `routes/send.ts` |
| 附件下载 | 不缓存原始字节，按需经 SDK 流式拉取 | `routes/mails.ts` |
| 监听器诊断 | `/api/listeners` 输出 `email/connected/retry`；前端有侧栏摘要 + 抽屉详情 | `routes/events.ts`、`ListenersDrawer.tsx` |
| Duck 邮箱 | 保存 DuckDuckGo Email Protection Bearer Token；调用非官方邮箱生成接口；记录已生成 `@duck.com` 邮箱、备注和预期转发目标 | `routes/duck.ts`、`duck-email.ts`、`App.tsx` |
| 消息通知 | 配置 Telegram Bot Token / Chat ID；在“消息通知”页面手动发送文本 | `routes/telegram.ts`、`telegram.ts`、`App.tsx` |
| 账号推送 | 粘贴 `temp/test.json` 结构，转换为 Sub2API 导入数据；获取 OpenAI 分组并按所选分组推送 | `routes/sub2.ts`、`sub2.ts`、`App.tsx` |
| 前端体验 | 中英双语、暗亮主题、拖拽栏宽（侧边栏 / 邮件列表）、登录态 localStorage 记忆 | `i18n.tsx`、`hooks.ts` |

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

`X-Admin-Password` 与 `query.token` 命中其一即放行（见 `src/server/index.ts: extractAdminPassword`）。

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

GET    /api/sub2/settings                 # { apiUrl, hasApiKey, apiKeyPreview }
PUT    /api/sub2/settings                 # { apiUrl?, apiKey? }
GET    /api/sub2/groups                   # 拉取 OpenAI active 分组供页面选择
POST   /api/sub2/convert                  # { input }，仅转换预览，不访问 Sub2API
POST   /api/sub2/push                     # { input, groupId }，转换后按所选分组推送
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

校验：所有入参经 zod 解析；失败返回 `400 {error:"invalid input", details:[...]}`。

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

“账号推送”页面接收 `temp/test.json` 结构的 ChatGPT 账号 JSON，转换为 Sub2API 的账号导入数据。转换时保持 `temp/toSub2.json` 或 `SUB2_PROXY_TEMPLATE_JSON` 中的 `proxies` 原样不变，并使用其中第一个 `proxy_key` 绑定账号。

推送流程：

1. 从“系统设置”读取 Sub2API 地址和 APIKey。
2. 调用 `GET /api/v1/admin/groups?page=1&page_size=1000&platform=openai&status=active` 获取 OpenAI 可用分组。
3. 页面选择目标分组。
4. 调用 `POST /api/v1/admin/accounts/data`，账号写入 `group_ids: [所选分组ID]`。

Sub2API 地址可以填写根地址，例如 `https://sub2.example.com`，也可以填写完整 `/api/v1/admin/accounts/data` 导入地址。Admin API Key 会按 Sub2API 约定放到 `x-api-key` 请求头；如果配置值以 `Bearer ` 开头，则按 JWT 令牌放到 `Authorization` 请求头。

## 6. 监听器与重连

`src/server/listener-manager.ts`：

- 启动条件：邮箱 `status === "active"` 且 `hasClawMailConfig()` 为真
- 退避序列：`[1, 2, 4, 8, 16, 30]` 秒
- `client.ws.onMessage` 收到 mailId → `client.mail.read({markRead:true})` → `saveMail` → SSE `mail` 广播
- `client.ws.onDisconnect` 触发 `scheduleReconnect`
- 删邮箱、断开 Claw 时会显式 `stopMailboxListener` 关闭 WS

`/api/listeners` 当前返回字段：`{ email, connected, retry }`。前端 `ListenersDrawer` 同时兼容了未来可能扩展的 `status / startedAt / lastEventAt / error` 字段。

## 7. 环境变量

```env
NODE_ENV=production
PORT=3000
ADMIN_PASSWORD=change-me

# 以下变量是"兜底值"，验证码登录成功后会被 SQLite 中的值覆盖
CLAW_API_KEY=
CLAW_DASHBOARD_COOKIE=
CLAW_WORKSPACE_ID=
CLAW_PARENT_MAILBOX_ID=
CLAW_ROOT_PREFIX=
CLAW_DOMAIN=claw.163.com

SYSTEM_PROXY_URL=
SYSTEM_REQUEST_TIMEOUT_MS=10000
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SUB2_API_URL=
SUB2_API_KEY=
SUB2_PROXY_TEMPLATE_JSON=

DATABASE_PATH=./data/app.db
```

读取顺序（`runtime-config.ts`）：`SQLite app_settings` → `process.env`，缺一则 API 报 `... is required; connect Claw first`。

## 8. 本地运行

应用监听端口由 `PORT` 环境变量控制，默认 **3000**（host `0.0.0.0`）。

```powershell
npm install
npm run build
npm start
# 默认 http://localhost:3000
# 改端口： $env:PORT=8080; npm start
```

开发：

```powershell
npm run dev          # tsx 跑后端，监听 :3000（受 PORT 控制）
npm run dev:web      # Vite 跑前端，监听 :5173
npm run typecheck    # tsc --noEmit
```

`npm run build` = `vite build` 产出静态资源到 `dist/web` + `esbuild` 打包后端到 `dist/server/index.js`，`@clawemail/node-sdk`、`fastify`、`better-sqlite3` 等保持 external。

## 9. Docker 部署

服务器默认不构建源码，只拉取 GitHub Actions 发布到 GHCR 的镜像：

```text
ghcr.io/dear7575/clawemail:latest
```

容器内进程固定监听 `3000`，宿主端口由 `HOST_PORT` 控制；SQLite 数据挂载到 `/app/data`。

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
ADMIN_PASSWORD=change-me
HOST_PORT=3000
DATA_DIR=./data
SYSTEM_PROXY_URL=
SYSTEM_REQUEST_TIMEOUT_MS=10000
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SUB2_API_URL=
SUB2_API_KEY=
SUB2_PROXY_TEMPLATE_JSON=
```

如果容器无法直连 DuckDuckGo、Telegram 或 Sub2API，可以在 Web 的“系统设置”里填写系统代理地址，或在 `.env` 里设置 `SYSTEM_PROXY_URL`。代理地址支持 `http://host:port` / `https://host:port`，例如 Docker Desktop 场景常见为 `http://host.docker.internal:7890`。旧的 `DUCK_PROXY_URL` / `DUCK_REQUEST_TIMEOUT_MS` 仍作为兼容兜底值读取。

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
  -e ADMIN_PASSWORD=change-me \
  -e DATABASE_PATH=/app/data/app.db \
  -v $PWD/data:/app/data \
  ghcr.io/dear7575/clawemail:latest
```

`./data` 挂到 `/app/data` 持久化 SQLite。GitHub Actions 会在推送 `main` 后自动构建并推送 `latest` 镜像。

## 10. Cloudflare 无服务器部署

本仓库同时提供 Cloudflare Workers + Static Assets + D1 的部署入口：

```text
src/cloudflare/          Cloudflare Worker API（无 Fastify / better-sqlite3 / Node SDK）
migrations/0001_initial.sql
wrangler.toml
```

限制边界：

- Cloudflare 版不运行常驻邮箱 WebSocket 监听器；收件箱通过前端刷新或 `GET /api/mails?sync=true` 请求触发同步。
- D1 替代本地 SQLite 文件；不需要自建服务器或挂载磁盘。
- 附件仍然不入库，下载时从 Claw 远端按需转发。
- D1 表结构会在首次访问 `/api/*` 时自动初始化。

推荐部署方式：

1. 先 Fork 本仓库到自己的 GitHub 账号。
2. 进入 Cloudflare 控制台。
3. 进入 `Workers & Pages` → `Create application` → `Import a repository`。
4. 选择自己的 fork 仓库。
5. 将项目名称改为小写，例如 `clawemail`、`clawemail-cf`。
6. 按页面步骤一路下一步，直到部署完成。

本地 Wrangler 手动部署：

```powershell
npx wrangler login
npx wrangler d1 create clawemail
```

如果 Wrangler 提示是否把 D1 配置写入 `wrangler.toml`，选择 `Yes`。手动填写时应补上 `database_id`：

```toml
[[d1_databases]]
binding = "DB"
database_name = "clawemail"
database_id = "你的 D1 database_id"
```

设置管理密码并初始化 D1：

```powershell
npx wrangler secret put ADMIN_PASSWORD
npm run cf:migrate
```

部署：

```powershell
npm run cf:deploy
```

手动部署时也可以跳过 `npm run cf:migrate`，应用会在首次 API 请求时自动建表；保留该命令是为了需要显式执行迁移的场景。

Cloudflare 版入口与原 API 保持一致，前端仍调用同源 `/api/*`。如果需要回到原服务器版，继续使用 `npm run build && npm start` 或 Docker 部署即可。



## 致谢

感谢 [Linux.do](https://linux.do) 社区。
