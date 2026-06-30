# Hermes Framework → ShadowRealm Mapping

This document cross-references each concept from the Hermes agentic
framework guide against what ShadowRealm already has, what is partially
covered, and what needs to be added as new work items.

---

## Concept-by-Concept Analysis

### Phase 1 — Core Architecture

| # | Hermes Concept | ShadowRealm Status | Notes |
|---|---|---|---|
| 1 | Agent vs. Chatbot (tool execution) | ✅ **Covered** | `companion/` agent layer + plugin tool system already executes tasks |
| 2 | Labrador Framework (persistent companion context) | ⚠️ **Partial** | `UserStore` (C76) + `UserPreferenceStore` (C78) handle identity; **missing: `soul.md`-style identity blueprint per agent** |
| 3 | One Brain, 22 Mouths (multi-channel frontend) | ⚠️ **Partial** | Web UI exists; Telegram/Discord/Slack adapters live in `integrations/` but are incomplete — **needs unified channel router** |
| 4 | Local vs. VPS hosting | ✅ **Covered** | Docker, `install-service.sh`, local launcher, VPS-ready docker-compose all present |
| 5 | OAuth vs. API key auth | ✅ **Covered** | `.env.example` covers API key injection; OAuth flow needed for user-facing integrations |

### Phase 2 — Intelligence & Memory

| # | Hermes Concept | ShadowRealm Status | Notes |
|---|---|---|---|
| 6 | Model agnosticism (swap LLM backends) | ⚠️ **Partial** | Provider config exists in `.env`; **needs `ModelRouter` class with dynamic swap + cost-aware routing** |
| 7 | Local offline engines (Ollama) | ⚠️ **Partial** | Ollama referenced in config but no dedicated `OllamaAdapter` with fallback detection |
| 8 | Compounding memory vaults (`memory.md` + SQLite) | ⚠️ **Partial** | `EventStore` (C70) + `FullTextIndex` (C73) provide the SQLite layer; **missing: `MemoryVault` that fuses markdown + DB into a unified context-injection interface** |
| 9 | Identity Blueprint (`soul.md`) | ❌ **Missing** | No concept of a per-agent character/persona config file. **New: `soul.md` loader + `AgentIdentity` class** |

### Phase 3 — Advanced Integrations

| # | Hermes Concept | ShadowRealm Status | Notes |
|---|---|---|---|
| 10 | Secure tool integrations (env-var injection) | ✅ **Covered** | All secrets via env; `.env.example` documents every key |
| 11 | Direct machine action (OS execution) | ⚠️ **Partial** | `companion/` has tool stubs; **needs sandboxed `OSActionExecutor` with permission gates** |
| 12 | MCP (Model Context Protocol) | ✅ **Covered** | `mcp_servers/` directory exists with server stubs |
| 13 | Skill routines + Pantheon (persona profiles) | ⚠️ **Partial** | `skills/` directory exists; **missing: `PantheonRouter` that routes tasks to named persona/model combos** |

### Phase 4 — Operational Controls

| # | Hermes Concept | ShadowRealm Status | Notes |
|---|---|---|---|
| 14 | Six operational switches (`/q`, `/background`, `/reset`, `/compress`, `/model`, `/stop`) | ❌ **Missing** | No inline command parser. **New: `CommandParser` + handler dispatch in the chat layer** |
| 15 | Principle of Least Access | ✅ **Covered** | `THREAT_MODEL.md` + `SECURITY.md` document this; permission system in plugin layer |
| 16 | Goal budgets (N-turn constraint loops) | ❌ **Missing** | No turn-budget or goal-scoped context window. **New: `GoalBudget` class wrapping session with turn counter + auto-compress** |
| 17 | Sub-agent parallelization | ⚠️ **Partial** | `WorkerPool` (C66) provides thread parallelism; **missing: `SubAgentOrchestrator` that spins isolated context windows per branch** |

### Phase 5 — Complete Ecosystem

| # | Hermes Concept | ShadowRealm Status | Notes |
|---|---|---|---|
| 18 | Heartbeats & cron schedules | ✅ **Covered** | `JobScheduler` (C65) with cron + interval support |
| 19 | Token overhead mitigation (`/compress`) | ❌ **Missing** | No context compression/summarisation step. **New: `ContextCompressor` using LLM summarisation** |
| 20 | AI dashboarding | ⚠️ **Partial** | Basic web UI exists; **needs spend tracking panel, goal monitor, source file browser** |
| 21 | Unified memory (GitHub-synced context) | ⚠️ **Partial** | GitHub integration in `integrations/`; **needs `MemorySyncAgent` that pushes `memory.md` + snapshots to repo** |

---

## New Work Items (Hermes-Inspired)

The following are **net-new** components derived from this analysis,
grouped into sprints to append to the master plan.

### Sprint H1 — Agent Identity Layer
- **C109** `core/soul_loader.py` — Load and validate `soul.md` persona blueprints per agent
- **C110** `core/agent_identity.py` — Runtime identity object injected into every LLM call
- **C111** `core/pantheon_router.py` — Route tasks to named persona+model combos based on cost/complexity

### Sprint H2 — Memory Vault
- **C112** `core/memory_vault.py` — Unified markdown + SQLite memory store with context-injection API
- **C113** `core/context_compressor.py` — LLM-based conversation summarisation + `/compress` command handler
- **C114** `core/memory_sync_agent.py` — Push `memory.md` + snapshots to GitHub repo on schedule

### Sprint H3 — Operational Controls
- **C115** `core/command_parser.py` — Inline `/q /background /reset /compress /model /stop` command parser
- **C116** `core/goal_budget.py` — N-turn constraint loop with auto-compress at budget exhaustion
- **C117** `core/sub_agent_orchestrator.py` — Parallel sub-agent execution with isolated context windows

### Sprint H4 — Model & Channel Routing
- **C118** `core/model_router.py` — Dynamic LLM backend swap with cost-aware routing + Ollama fallback
- **C119** `core/channel_router.py` — Unified multi-channel frontend adapter (Telegram, Discord, Slack, WhatsApp, Matrix)
- **C120** `core/os_action_executor.py` — Sandboxed OS action execution with permission gates and audit log

---

## Summary

| Status | Count |
|---|---|
| ✅ Already covered | 8 |
| ⚠️ Partially covered | 9 |
| ❌ Net-new (Hermes-inspired) | 4 core gaps → 12 new components (C109–C120) |

These 12 new components extend the master plan from **108 → 120 total components**.
