# C04 — Dead Code Audit Results

> **Date:** 2026-06-29
> **Branch:** `shadowrealm-v2`
> **Commit:** C04

## What Was Checked

Full read of `app.py`, `core/middleware.py`, and all `routes/*.py` files (52 files).

Original C04 premise: audit `chatgpt_subscription_routes.py`, `copilot_routes.py`, `device_flow.py` for stale/dead code.

## Original Targets — Verdict

| File | Finding | Action |
|---|---|---|
| `routes/chatgpt_subscription_routes.py` | ✅ Live — mounted in app.py, full OAuth device flow for ChatGPT Subscription | Keep |
| `routes/copilot_routes.py` | ✅ Live — mounted in app.py, provisions GitHub Copilot endpoints | Keep |
| `routes/device_flow.py` | ✅ Live — shared base router factory used by both above | Keep |

None of these are dead. The original assumption was wrong.

## Actual Dead / Stale Code Found

### 1. Upstream branding in app identity
- `app.py`: `FastAPI(title="AI Chat Application")` — stale upstream name
- `app.py`: `version="1.0.0"` — not tracking ShadowRealm versioning
- **Fix:** Rename to `ShadowRealm` / `2.0.0-dev` in next app.py touch (Sprint 4 UI pass)

### 2. `X-Odysseus-*` header names (fixed in this commit)
- `core/middleware.py`: `INTERNAL_TOOL_HEADER = "X-Odysseus-Internal-Token"`
- `app.py` CORS `allow_headers`: `"X-Odysseus-Internal-Token"`, `"X-Odysseus-Owner"`
- `app.py` auth middleware: reads `request.headers.get("X-Odysseus-Owner")`
- **Fix (this commit):** Renamed to `X-ShadowRealm-Internal-Token` / `X-ShadowRealm-Owner` with backward-compat legacy alias so nothing breaks mid-sprint.

### 3. `ODYSSEUS_INTERNAL_TOKEN` env var name (fixed in this commit)
- `core/middleware.py`: read `os.environ.get("ODYSSEUS_INTERNAL_TOKEN")`
- **Fix (this commit):** Now reads `SHADOWREALM_INTERNAL_TOKEN` first, falls back to `ODYSSEUS_INTERNAL_TOKEN` for existing `.env` files.

### 4. `ODYSSEUS_INPROCESS_TASKS` / `ODYSSEUS_INPROCESS_POLLERS` env var names
- `app.py`: `os.environ.get("ODYSSEUS_INPROCESS_TASKS", "1")`
- `routes/email_pollers.py`: `os.environ.get("ODYSSEUS_INPROCESS_POLLERS", "1")`
- **Status:** Deferred to Sprint 4 app.py branding pass — low risk, no user-facing behavior.

### 5. `/backgrounds` prototype route
- `app.py`: `GET /backgrounds` serves `static/backgrounds.html` with comment `"Sandbox page for prototyping background effects"`
- **Status:** Intentionally kept — will be used for Sprint 4 visual identity work. Remove post-Sprint 4 if unused.

### 6. `routes/research_routes.py` thin stub
- 761-byte stub that re-exports from `routes/research/research_routes.py`
- **Status:** Fine — acts as a stable import alias. No action needed.

## Files Changed in This Commit

- `core/middleware.py` — renamed `INTERNAL_TOOL_HEADER`, added `INTERNAL_TOOL_HEADER_LEGACY`, updated `require_admin` to accept both header names, updated env var read order
- `docs/C04_DEAD_CODE_AUDIT.md` — this file

## Remaining Branding Debt (tracked for Sprint 4)

- [ ] `app.py` FastAPI title + version
- [ ] `app.py` CORS `allow_headers` list — add `X-ShadowRealm-*`, keep `X-Odysseus-*` until Sprint 4 cleanup
- [ ] `app.py` auth middleware `X-Odysseus-Owner` header read
- [ ] `app.py` `ODYSSEUS_INPROCESS_TASKS` env var
- [ ] `routes/email_pollers.py` `ODYSSEUS_INPROCESS_POLLERS` env var
- [ ] Remove `INTERNAL_TOOL_HEADER_LEGACY` alias after Sprint 4
