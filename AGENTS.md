# AGENTS.md — ShadowRealm

> **Extends:** `ShadowWalkerNC/.github/AGENTS.md` — all global rules apply unconditionally.
> **Auto-loaded by:** Claude Code · GitHub Copilot · OpenAI Codex · Cursor · Windsurf · Perplexity
> **Last updated:** 2026-06-29

---

## Project Identity

```
Project:      ShadowRealm
Tagline:      Self-hosted AI workspace — code, research, memory, agents in one IDE
Status:       In development (v2.0 build in progress)
Phase:        Sprint 1 complete → Sprint 2 starting (Token & Context Compression)
Branch:       shadowrealm-v2 → dev → main (on v2.0.0 tag)
Target score: 9 / 10
```

**What ShadowRealm is:**
A fully self-hosted AI workspace running on your own hardware. It is an IDE-style
interface (file tree / agent chat / preview+terminal) backed by a multi-framework
agent orchestration layer, a 5-tier memory system, a living skills engine, and a
self-healing pipeline. Think "AI-native VS Code that can think, remember, and fix
itself" — with no vendor lock-in and no data leaving your machine unless you choose.

---

## Tech Stack

```
Language:    Python 3.11 (primary backend) + vanilla JS (frontend)
Framework:   Flask (REST API) — no frontend framework, no build step
Database:    ChromaDB (vector / warm memory), SQLite (traces, skill registry)
Hosting:     Self-hosted — Docker Compose or native Python
AI Routing:  LiteLLM (provider abstraction over OpenAI, Anthropic, Google, Groq,
             Ollama, and any OpenAI-compatible endpoint)
Search:      SearXNG (self-hosted meta-search)
Shell:       Custom shell_routes.py — sandboxed bash execution backend
Agents:      LangGraph (research) · AutoGen (coding) · CrewAI (scheduled)
Memory:      mem0 over ChromaDB + Notion MCP + Obsidian vault
Skills:      Custom SkillsManager — CRUD, versioning, LLM-as-judge, self-edit,
             teacher escalation, nightly audit, auto-publish, slash-invoke
MCP Servers: email_server.py · memory_server.py · rag_server.py · image_gen_server.py
             (GitHub, Notion, filesystem, browser-use wired at onboarding)
Containers:  docker-compose.yml (app + ChromaDB + SearXNG + Open Hands)
```

---

## Repository Layout

```
app.py                  Flask entry point
launcher.py             Cross-platform native launcher
setup.sh                One-shot setup: OS detect, venv, .env, launch
docker-compose.yml      Full stack container definition
core/
  middleware.py         Security, CSP, internal tool token, rate limiting
src/
  service_health.py     Degraded-state health reporting (concurrent probes)
routes/
  skills_routes.py      Skills engine (77 KB) — already production-grade
  shell_routes.py       Shell execution backend (74 KB)
  codex_routes.py       Coding / codex pipeline (43 KB)
  mcp_routes.py         MCP protocol + server registration
  diagnostics_routes.py GET /api/diagnostics/services health endpoint
  webhook_routes.py     Webhook ingestion
  vault_routes.py       Secrets vault
  compare_routes.py     Multi-model compare
  research/             LangGraph research pipeline sub-module
static/                 All frontend assets (CSS, JS, images)
templates/              Jinja2 HTML templates
docs/
  V2_MASTER_PLAN.md     Single source of truth for the v2 build
  STATUS.md             Integration audit + live service status
```

---

## Active Agents (Personas)

These are named agent personas with defined skill sets, implemented in Sprint 7.
Until Sprint 7, the underlying capabilities exist but personas are not yet wired.

| Agent | Role | Core Skills |
|---|---|---|
| **ShadowCoder** | Coding & DevOps | code_write, code_review, test_run, deploy, git_ops |
| **ShadowResearcher** | Research & Knowledge | web_search, source_read, summarize, memory_write, report_gen |
| **ShadowOps** | Infrastructure & Ops | shell_exec, file_manage, service_monitor, cron_schedule |
| **ShadowMemory** | Memory & Knowledge Sync | memory_ingest, memory_compress, memory_retrieve, knowledge_sync |
| **ShadowCreative** | Content & Image | image_gen, image_edit, doc_write, content_draft |
| **Agent Orchestrator** | Router | Routes requests, manages multi-agent collaboration |

**Always-active agent roles for coding sessions:**
```
COHERENCE   — enforces one-commit-per-task, no unrelated changes
SECURITY    — blocks secrets in source, validates CSP, checks vault usage
DOCS        — keeps STATUS.md, AGENTS.md, and inline comments current
ENGINEER    — implements features per V2_MASTER_PLAN.md commit spec
ARCHITECT   — guards design rules (see below), flags violations before commit
```

---

## Design Rules — Never Break These

1. **One framework per pipeline type** — LangGraph=Research, AutoGen=Coding, CrewAI=Scheduled. Never mixed.
2. **One commit per task** — atomic, reversible, readable git history. Commit message = plan entry exactly.
3. **Never build on unstable ground** — each sprint must be fully stable before the next begins.
4. **CSS cleanup before any UI work** — Sprint 3 must be complete and merged before Sprint 4 starts.
5. **Skills engine is already running** — do not replace it, only extend it (`routes/skills_routes.py`).
6. **Token compression before memory expansion** — Sprint 2 must complete before Sprint 5 starts.
7. **Fix only the error** — PatchEngine constraint: never rewrite working code alongside broken code.
8. **Every MCP server is a skill** — existing servers auto-register at startup; never bypass this.
9. **All agents share one memory layer** — no siloed per-agent memory stores.
10. **Self-healing is L1–L3 for v2.0, L4–L5 for v2.1** — ship monitor → diagnose → patch-with-approval first.

---

## What Is Already Shipped (Do Not Rebuild)

A full read of `dev` confirmed these are production-grade before v2 planning:

- **Skills Engine** — CRUD, versioning, slash-invoke, search, URL import (`skills_routes.py`)
- **LLM-as-judge skill test runner** — scores, rewrites, escalates failing skills
- **Self-edit + retry on failing skills** — up to N retries before teacher escalation
- **Teacher model escalation** — routes hard failures to a stronger model
- **Necessity + redundancy checker** — prevents skill bloat
- **Retrieval precision auditor** — validates skill retrieval quality
- **Nightly scheduled skill audit** — cron-triggered, logs results
- **Auto-publish policy + confidence scoring** — gated skill promotion
- **Degraded-state health reporting** — `src/service_health.py` (bounded concurrent probes, secret scrubbing)
- **GET /api/diagnostics/services** — JSON `{overall, services[], timestamp}` health endpoint
- **Shell execution backend** — `routes/shell_routes.py` (sandboxed bash)
- **Codex / coding route** — `routes/codex_routes.py`
- **Webhook ingestion** — `routes/webhook_routes.py`
- **Secrets vault** — `routes/vault_routes.py`
- **MCP protocol + servers** — email, memory, RAG, image_gen auto-register
- **Security middleware** — CSP, internal tool token (`core/middleware.py`)
- **Multi-model compare** — `routes/compare_routes.py`
- **Research pipeline** — `routes/research/` (LangGraph-based)
- **/status UI page** — wired to `/api/diagnostics/services` (C06)

---

## Current Phase Context

```
Active sprint:      Sprint 1 — Foundation Stabilization (COMPLETE as of C08)
Next sprint:        Sprint 2 — Token & Context Compression

Sprint 2 goal:      Never hit a context wall. Agent mode works on small local models.
Sprint 2 commits:   C09 TokenCounter utility
                    C10 context size profiles (auto-detect model window)
                    C11 slim MCP tool injection (task-relevant tools only)
                    C12 auto-compaction at 80% context threshold
                    C13 LLMLingua compression middleware
                    C14 token usage panel (live tokens, compression savings, cost)

Definition of done: All 6 Sprint 2 commits merged to shadowrealm-v2.
                    Context window never exceeded in a standard coding session.
                    Token panel visible and updating live in the UI.

Next after Sprint 2: Sprint 3 — CSS & UI Architecture Cleanup (required before any
                    visual work; Sprint 4 is blocked until Sprint 3 is merged).
```

---

## Self-Healing Maturity Status

| Level | Capability | Status |
|---|---|---|
| L1 — Monitor | Detects failures, alerts human | ✅ Shipped |
| L2 — Diagnose | Classifies root cause, shows fix | ✅ Shipped |
| L3 — Patch with approval | Self-edit + teacher + human review | ✅ Shipped (review UI pending — Sprint 7B) |
| L4 — Auto-patch low-risk | Auto-commits known safe fixes | 🔲 v2.1 (Sprint 7B scaffold) |
| L5 — Reflect and improve | Proactively rewrites skills | 🔲 v2.1 (Sprint 7B scaffold) |

---

## Branch & Merge Strategy

```
main              ← stable releases only (v2.0.0 tag lands here at C94)
  └── dev         ← integration branch — sprint merges land here
        └── shadowrealm-v2  ← all active development
```

- All work goes to `shadowrealm-v2`.
- Merge `shadowrealm-v2` → `dev` at the end of each sprint.
- Merge `dev` → `main` only on the C94 v2.0.0 release tag.
- Never commit directly to `dev` or `main`.

---

## Quick Reference: Key Files for Common Tasks

| Task | File |
|---|---|
| Add / edit a skill | `routes/skills_routes.py` |
| Change health probe logic | `src/service_health.py` |
| Add a new API route | `routes/` + register in `app.py` |
| Change security / CSP policy | `core/middleware.py` |
| Update build plan | `docs/V2_MASTER_PLAN.md` |
| Update service status | `docs/STATUS.md` |
| Change .env variables | `.env.example` (never commit `.env`) |
| Docker service changes | `docker-compose.yml` |
| Initial setup / onboarding | `setup.sh` |

---

*Last updated: 2026-06-29 | Branch: shadowrealm-v2 | Extends: ShadowWalkerNC/.github/AGENTS.md*
*Source of truth: [docs/V2_MASTER_PLAN.md](https://github.com/ShadowWalkerNC/ShadowRealm/blob/shadowrealm-v2/docs/V2_MASTER_PLAN.md)*
