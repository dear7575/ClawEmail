# 收件管理分页与刷新优化设计

Date: 2026-10-22

## Goal

降低收件列表分页响应体与数据库排序开销，并确保手动刷新指示在同步请求实际结束前持续显示。手动刷新仍等待远端同步与列表响应完成，不改为后台刷新。

## Context

- `GET /api/mails` 的普通分页从 SQLite 读取，但仓储当前使用 `SELECT *`，会把邮件正文、HTML、原始 JSON 等详情字段一并返回。
- 列表查询按邮箱/连接筛选并按 `created_at, id` 倒序排序，当前索引未完整覆盖这些筛选与排序组合。
- 前端请求已把搜索关键字传给后端，但随后仍在页面中按返回的正文再次过滤，并可能用当前页条数覆盖总数。
- 手动刷新同步完整远端 INBOX；新邮件详情按顺序逐封读取。远端等待仍是刷新耗时的重要组成部分。
- 前端 `handleRefreshMails` 已在请求完成后通过 `finally` 清除 busy 状态。该问题需要用延迟响应验证实际渲染行为，并避免重复刷新请求。

## Design

### 列表查询

- 将列表查询改为明确选择列表所需字段，不再返回完整 HTML 和原始 JSON。
- `text` 仅返回有上限的预览；服务端仍对完整正文执行关键字筛选，详情接口继续返回完整内容。
- 前端移除对已分页结果的二次关键字过滤，使用后端返回的 items 和 total，避免正文预览截断造成误过滤或总数错误。
- 为邮箱、连接与 `created_at/id` 倒序列表查询添加幂等复合索引；保留详情接口与邮件数据格式。

### 刷新状态

- 保留同步刷新：按钮从触发开始持续显示 loading，直至同步及随后的列表请求 settle（成功或失败）。
- 用 ref 或等效的请求级锁防止因快速重复点击/并发调用重复触发昂贵的完整同步。
- 错误时仍显示既有错误反馈，并在请求结束后恢复按钮。

## Alternatives

1. 只调整列表返回列和索引：分页改善明显，刷新慢仍受远端请求影响。
2. 并发读取新邮件详情：潜在地缩短大批新邮件同步时间，但增加上游限流及重试压力，本次不采用。
3. 先返回本地缓存、后台同步：交互更快，但改变用户已确认的刷新语义，本次不采用。

## Compatibility and Failure Handling

- 不更改 API 路由、筛选规则、分页参数、邮件详情响应或全量快照对账安全策略。
- 关键字匹配继续由后端基于完整正文完成；SQL 搜索语义保持现状。
- 索引通过现有 `CREATE INDEX IF NOT EXISTS` 初始化路径添加，已有数据库无需手动迁移命令。
- 刷新失败继续展示缓存列表/同步错误；busy 状态无论成功失败都在请求完成后复位。

## Dependency Audit

- Audit direct dependencies in `frontend/package.json`, `backend/requirements.txt`, and `backend/requirements-dev.txt` against source, configuration, scripts, and tests before removal.
- Current audit found references for all direct dependencies, including shared class utilities, UI primitives, Tailwind/PostCSS, runtime integrations, and pytest async support. Do not remove dependencies unless a fresh repository-wide audit proves they are unused.
- No runtime memory-limit change is in scope; removing a package is not assumed to reduce application runtime memory.

## Validation

- Backend tests verify list results omit large HTML/raw payloads, text previews are bounded, keyword filtering and pagination totals remain correct, and indexes exist after new/existing database initialization.
- Frontend typecheck and production build pass; with a delayed refresh response, loading remains active and the button disabled until the request settles.
- Run the full backend suite and `git diff --check`.
