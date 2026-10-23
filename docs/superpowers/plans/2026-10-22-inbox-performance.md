# 收件管理响应优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan task-by-task with focused verification.

**Goal:** 减少邮件列表分页查询与传输开销，并保证刷新状态覆盖完整请求周期；同时只删除经引用审计确认未使用的直接依赖。

**Architecture:** SQLite 列表查询使用摘要字段、有限长度正文预览和覆盖筛选/排序的索引，详情接口保持完整数据。前端完全依赖服务端搜索结果，并用 ref 对刷新请求加锁。依赖通过源码、配置、脚本和测试引用核实，不做推测性移除。

**Tech Stack:** FastAPI, SQLAlchemy, SQLite, pytest, Next.js, React, TypeScript, npm.

---

### Task 1: 测试本地邮件列表性能与兼容语义

**Files:**
- Modify: `backend/tests/test_mailboxes_and_mails.py`
- Modify: `backend/tests/test_mail_repository.py` (only if this file exists; otherwise add repository tests to the existing mail service test module)

- [ ] 增加测试：分页响应不包含完整 HTML/原始 JSON，正文预览不超过约定长度。
- [ ] 增加测试：关键字仍按完整正文匹配，分页 `total` 仍是完整匹配总数。
- [ ] 增加测试：新库及旧库初始化后存在邮箱/连接/排序复合索引。
- [ ] 从 `backend` 运行新增测试，确认失败原因对应缺少投影/索引，而非测试设置错误。

### Task 2: 实现轻量列表查询

**Files:**
- Modify: `backend/app/db/mail_repository.py`
- Modify: `frontend/app/App.tsx`

- [ ] 将列表 `SELECT *` 改为显式选择摘要字段；正文返回固定长度预览，HTML 以空值占位维持响应类型兼容。
- [ ] 为邮箱、连接与 `created_at/id` 降序查询添加幂等复合索引；保留现有数据和详情查询。
- [ ] 删除 `loadMails` 对服务端分页结果的二次关键字过滤，直接采用服务端 `items` 和 `total`。
- [ ] 运行 Task 1 的后端测试，确认通过。

### Task 3: 保证刷新请求状态准确

**Files:**
- Modify: `frontend/app/App.tsx`
- Modify: `frontend/app/components/TablePager.tsx` only if the delayed-response check finds a rendering/interaction gap

- [ ] 为手动刷新加入 ref 级互斥锁，确保重复/并发调用不会让较早请求提前清除 busy 状态。
- [ ] 使用延迟响应验证：请求 pending 时按钮保持禁用且图标持续旋转；成功或失败 settle 后恢复。
- [ ] 运行 `npm run typecheck` 和 `npm run build`（串行）。

### Task 4: 复核依赖与完整验证

**Files:**
- Review: `frontend/package.json`, `backend/requirements.txt`, `backend/requirements-dev.txt`
- Modify manifests/lockfiles only if a direct dependency has no use in source, configuration, scripts, or tests.

- [ ] 复核每项直接依赖。当前静态搜索找到所有依赖的代码/配置/测试引用；若确认无未使用依赖，保持 manifests 和 lockfile 不变。
- [ ] 从 `backend` 运行 `python -m pytest -q`。
- [ ] 从 `frontend` 串行运行 `npm run typecheck && npm run build`。
- [ ] 在仓库根目录运行 `git diff --check`，检查暂存范围，明确排除 `tmp/`。
- [ ] 提交已确认的 README 整理、设计/计划文档和代码修改，并推送至已核实的 `origin/main`。
