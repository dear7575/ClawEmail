# OpenAI OAuth Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Remove Duck-bound OpenAI OAuth login and token storage, including historical token JSON, while preserving Duck and OpenAI-password workflows.

**Architecture:** Remove the OAuth-only login service and its API/frontend clients. Remove OAuth fields from Duck models and repository writes, and migrate existing SQLite tables to drop the OAuth JSON column. Keep the separate password endpoint and password editing/copying behavior unchanged.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite, Pydantic, Next.js, React, TypeScript, pytest.

---

## File Map

- Modify `backend/app/db/duck_repository.py` for fresh schema and idempotent old-database migration.
- Modify `backend/app/services/duck.py` to remove OAuth JSON model/service behavior while retaining password behavior.
- Modify `backend/app/api/duck.py` to remove OAuth-only routes and imports.
- Delete `backend/app/services/openai_auth.py` and OAuth-only tests after confirming no other production references.
- Modify `backend/tests/test_duck.py`; delete OAuth-only login/service tests if no remaining behavior is covered there.
- Modify `frontend/app/App.tsx` and `frontend/app/api.ts` to remove OAuth action, status, JSON editor and client contracts, preserving password controls.
- Update/add tests around schema migration, retained Duck/password functionality, and API route removal.

## Task 1: Prove migration and contract behavior

**Files:**
- Test: `backend/tests/test_duck.py`
- Modify: `backend/app/db/duck_repository.py`

- [x] Add a regression test that constructs a legacy `duck_addresses` table containing `openai_auth_json` and a sentinel value, initializes the repository, then verifies the column and value are gone while address row, password, Sub2 fields, and raw JSON remain.
- [x] Add/adjust a fresh-schema test proving `openai_auth_json` is absent and schema initialization is repeatable.
- [x] Run the focused tests and confirm the legacy migration test fails before implementation.
- [x] Change fresh table creation to omit `openai_auth_json`; migrate existing SQLite tables by checking `PRAGMA table_info` and dropping the legacy column in a transaction. Keep all other columns and indexes intact.
- [x] Run the focused migration tests and verify they pass.

Run: `python -m pytest -q tests/test_duck.py`

## Task 2: Remove backend OAuth surface

**Files:**
- Modify: `backend/app/services/duck.py`
- Modify: `backend/app/api/duck.py`
- Delete: `backend/app/services/openai_auth.py`
- Modify/Delete: `backend/tests/test_duck.py`, `backend/tests/test_openai_login.py`, `backend/tests/test_openai_auth.py`

- [x] Remove OAuth JSON from the Duck update model, public address serialization, and repository writes; preserve password handling.
- [x] Remove the OAuth login and OAuth JSON read/update routes; keep the password read route and password update path.
- [x] Search production references before deleting the dedicated `openai_auth.py` module; remove its tests only when they exclusively cover removed behavior.
- [x] Add API tests that prove removed routes are absent and existing password/address APIs remain functional.
- [x] Run Duck and adjacent focused backend tests.

Run: `python -m pytest -q tests/test_duck.py`

## Task 3: Remove frontend OAuth affordances

**Files:**
- Modify: `frontend/app/App.tsx`
- Modify: `frontend/app/api.ts`

- [x] Remove OAuth-only busy state, login handler, API types/functions, list action, and authorization status indicator.
- [x] Remove OAuth JSON state, editor loading/saving/validation and field controls.
- [x] Preserve the OpenAI password column, copy action, editor input and password endpoint update flow.
- [x] Search for all remaining OAuth feature identifiers and remove only references tied to this Duck workflow.
- [x] Run frontend typecheck and production build.

Run: `npm run typecheck`
Run: `npm run build`

## Task 4: Full verification

- [x] Run the complete backend test suite.
- [x] Run frontend typecheck and production build sequentially.
- [x] Run `git diff --check` and inspect `git status --short`; preserve pre-existing unrelated changes and untracked `tmp/`.
- [x] Confirm migration tests prove historical OAuth JSON is deleted and retained Duck/password data survives.

Run from `backend`: `python -m pytest -q`
Run from `frontend`: `npm run typecheck && npm run build`
Run from repository root: `git diff --check && git status --short`
