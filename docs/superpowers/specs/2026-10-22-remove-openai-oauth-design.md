# OpenAI OAuth Removal Design

Date: 2026-10-22

## Goal
Remove the OpenAI OAuth login and token-storage feature associated with Duck addresses. Keep Duck account/address management and the saved OpenAI password workflow intact.

## Scope
- Remove the Duck address list OAuth login action and OAuth authorization status indicator.
- Remove OAuth JSON controls from the Duck address editor while preserving password editing/copying.
- Remove the OpenAI login, OAuth JSON read, and OAuth credential update API paths and frontend clients.
- Remove OAuth fields from Duck address response and credential update models, repository writes, and service logic.
- Migrate existing SQLite databases to drop `duck_addresses.openai_auth_json`, permanently deleting stored values. Ensure migration works for both fresh and existing databases and is safe to run repeatedly.
- Remove the dedicated OpenAI OAuth automation service and its tests only if repository-wide references confirm it is exclusive to this feature.

## Compatibility and Data
After migration, historical OAuth JSON is irrecoverable. Existing Duck addresses, account references, notes, forwarding mailbox values, OpenAI passwords, Sub2 metadata, and raw Duck response data must remain unchanged. The existing OpenAI password endpoints and editor behavior remain available.

## Validation
- Add repository tests proving fresh schema omits the OAuth column and legacy schema migration removes the column and stored values without damaging retained columns or rows.
- Update API/service tests to assert the removed OAuth behavior is no longer exposed and that password/address behavior remains intact.
- Run the full backend test suite, frontend typecheck, production build, and `git diff --check`.
