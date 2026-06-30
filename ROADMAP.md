# ShadowRealm v2 — Master Roadmap

> Last updated: 2026-06-29
> Branch: `shadowrealm-v2`
> Progress: **78 / 129 components shipped**

---

## Vision Statement

> **ShadowRealm is a local-first, self-healing, model-agnostic cognitive operating system that cleans human input, reasons explicitly, routes intelligently, executes safely, learns from every interaction, and compounds memory over time.**
>
> Sophisticated inside. Clean outside. Always explainable.

---

## Master Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        FRONT DOOR                            │
│   PromptNormalizer → IntentClassifier → ClarificationGate    │
│              → ReasoningEngine  (ReAct Loop)                 │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                      ROUTING LAYER                           │
│     PantheonRouter → ModelRouter → DomainModelRegistry       │
│            TokenBudgetManager  →  GoalBudget                 │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                    EXECUTION LAYER                           │
│   SubAgentOrchestrator → TaskQueue → WorkerPool              │
│   PluginManager → SkillTrainer → OSActionExecutor            │
│            ChannelRouter  →  MCP Servers                     │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                  MEMORY & LEARNING                           │
│   MemoryVault → ContextCompressor → MemorySyncAgent          │
│   SelfReflectionLoop → CommunitySkillLibrary                 │
│   EventStore → AuditLogger → VectorSearchIndex               │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                     OUTPUT LAYER                             │
│   Response + Reasoning Trace (collapsible panel / side chat) │
│   NotificationDispatcher → ChannelRouter → UI                │
└──────────────────────────────────────────────────────────────┘
```

---

## Progress Overview

| Track | IDs | Count | Status |
|---|---|---|---|
| Foundation | C1–C18 | 18 | ✅ Complete |
| Data & Storage | C19–C36 | 18 | ✅ Complete |
| Security & Auth | C37–C54 | 18 | ✅ Complete |
| Observability | C55–C63 | 9 | ✅ Complete |
| Task & Job Queue | C64–C66 | 3 | ✅ Complete |
| Networking & HTTP | C67–C69 | 3 | ✅ Complete |
| Event Sourcing & Audit | C70–C72 | 3 | ✅ Complete |
| Search & Indexing | C73–C75 | 3 | ✅ Complete |
| User & Session | C76–C78 | 3 | ✅ Complete |
| Notification & Messaging | C79–C81 | 3 | 🔄 Sprint 23 |
| Permission & Policy | C82–C84 | 3 | Queued |
| Plugin & Extension | C85–C87 | 3 | Queued |
| AI / LLM Integration | C88–C90 | 3 | Queued |
| Workflow Engine | C91–C93 | 3 | Queued |
| Data Pipeline | C94–C96 | 3 | Queued |
| Multimodal & Media | C97–C99 | 3 | Queued |
| Testing Infrastructure | C100–C102 | 3 | Queued |
| Deployment & DevOps | C103–C105 | 3 | Queued |
| Integration Adapters | C106–C108 | 3 | Queued |
| Agent Identity Layer | C109–C111 | 3 | Queued |
| Memory Vault | C112–C114 | 3 | Queued |
| Operational Controls | C115–C117 | 3 | Queued |
| Model & Channel Routing | C118–C120 | 3 | Queued |
| Input Intelligence | C121–C123 | 3 | Queued |
| Self-Healing & Learning | C124–C126 | 3 | Queued |
| Ecosystem & Portability | C127–C129 | 3 | Queued |
| **TOTAL** | **C1–C129** | **129** | **78 shipped** |

---

## Sprint History

### ✅ Sprints 1–6 — Foundation & Core (C1–C18)
Config loader, structured logger, error handler, event bus, plugin manager, service registry,
CLI framework, env validator, task runner, feature flags, health probe, rate limiter core,
circuit breaker, retry policy, dependency injector, signal handler, process manager, startup sequencer.

### ✅ Sprints 7–12 — Data & Storage (C19–C36)
Database manager, migration engine, model registry, L1 memory cache, L2 disk cache, L3 distributed cache,
file store, blob store, document store, graph store, time-series store, data validator,
serialiser, query builder, connection pool, schema registry, data masker, backup manager.

### ✅ Sprints 13–18 — Security & Auth (C37–C54)
JWT auth, OAuth2 provider, RBAC engine, request rate limiter, input validator, secret manager,
AES-256-GCM encryption, key rotation, HMAC signer, TLS context manager, session token store,
CSRF guard, IP allowlist, audit trail, content policy, threat model, security scanner, permission matrix.

### ✅ Sprints 16–17 — Observability (C55–C63)
Structured log aggregator, distributed trace collector, metrics collector, alert manager,
dashboard data API, health checker, metrics registry, span exporter, diagnostics reporter.

### ✅ Sprint 18 — Task & Job Queue (C64–C66)
TaskQueue (priority + retry + DLQ), JobScheduler (interval/cron/once), WorkerPool (thread drain + backoff).

### ✅ Sprint 19 — Networking & HTTP (C67–C69)
HTTPClient (retry + auth), WebhookDispatcher (HMAC-signed), RateLimitedSession (token bucket).

### ✅ Sprint 20 — Event Sourcing & Audit (C70–C72)
EventStore (append-only + snapshots), AuditLogger (hash-chained), ChangeTracker (field diff + rollback).

### ✅ Sprint 21 — Search & Indexing (C73–C75)
FullTextIndex (FTS5/BM25), VectorSearchIndex (cosine + numpy), SearchRouter (hybrid RRF fusion).

### ✅ Sprint 22 — User & Session (C76–C78)
UserStore (PBKDF2-HMAC-SHA256), SessionManager (CSPRNG + sliding TTL), UserPreferenceStore (namespaced + callbacks).

---

## Active & Queued Sprints

### 🔄 Sprint 23 — Notification & Messaging (C79–C81)
- **C79** `core/notification_dispatcher.py` — Multi-channel fanout · dedup window · pluggable adapters · async delivery
- **C80** `core/in_app_message_queue.py` — SQLite inbox · read/unread/archived · topic threads · TTL · badge count
- **C81** `core/email_composer.py` — Template registry · {{var}} substitution · SMTP + pluggable transport · dry-run

### Sprint 24 — Permission & Policy (C82–C84)
- **C82** `core/permission_manager.py` — Fine-grained permission graph (subject → action → resource)
- **C83** `core/policy_engine.py` — Declarative policy evaluation (allow/deny rules with conditions)
- **C84** `core/access_control_list.py` — Per-resource ACL with inheritance and override

### Sprint 25 — Plugin & Extension (C85–C87)
- **C85** `core/plugin_registry.py` — Versioned plugin manifest + dependency resolver
- **C86** `core/plugin_sandbox.py` — Isolated execution environment for untrusted plugins
- **C87** `core/extension_loader.py` — Hot-reload extensions without restart

### Sprint 26 — AI / LLM Integration (C88–C90)
- **C88** `core/llm_client.py` — Unified LLM API (OpenAI / Anthropic / Gemini / Ollama) with streaming
- **C89** `core/tool_registry.py` — OpenAI function-calling spec compatible tool definitions
- **C90** `core/llm_response_parser.py` — Structured output extraction + validation from LLM responses

### Sprint 27 — Workflow Engine (C91–C93)
- **C91** `core/workflow_definition.py` — Trigger → condition → action node graph schema (n8n-inspired)
- **C92** `core/workflow_executor.py` — DAG execution engine with branch, loop, parallel node types
- **C93** `core/workflow_registry.py` — Store, version, activate/deactivate named workflows

### Sprint 28 — Data Pipeline (C94–C96)
- **C94** `core/pipeline_builder.py` — Composable ETL step chain with typed I/O contracts
- **C95** `core/data_transformer.py` — Map/filter/reduce/join operations on structured data
- **C96** `core/pipeline_scheduler.py` — Schedule + monitor data pipelines with cron or event triggers

### Sprint 29 — Multimodal & Media (C97–C99)
- **C97** `core/media_processor.py` — Image/audio/video metadata extraction + format conversion
- **C98** `core/transcription_adapter.py` — Speech-to-text bridge (Whisper + pluggable backends)
- **C99** `core/vision_adapter.py` — Image understanding bridge (vision model API wrapper)

### Sprint 30 — Testing Infrastructure (C100–C102)
- **C100** `core/test_harness.py` — Agent behavior test runner with expected-output assertions
- **C101** `core/mock_tool_registry.py` — Deterministic mock tools for isolated agent testing
- **C102** `core/regression_tracker.py` — Track capability regressions across model/version changes

### Sprint 31 — Deployment & DevOps (C103–C105)
- **C103** `core/deployment_manager.py` — Blue/green deployment orchestration with rollback
- **C104** `core/config_drift_detector.py` — Detect + alert on config drift from baseline
- **C105** `core/release_gate.py` — Automated pre-release checks (tests, security scan, health probes)

### Sprint 32 — Integration Adapters (C106–C108)
- **C106** `integrations/github_adapter.py` — GitHub API: repos, issues, PRs, commits
- **C107** `integrations/calendar_adapter.py` — Calendar read/write (Google Calendar / iCal)
- **C108** `integrations/browser_adapter.py` — Headless browser automation (Playwright/Puppeteer bridge)

---

## Intelligence Extension Sprints (C109–C129)

> These sprints implement the cognitive OS layer — the features that separate ShadowRealm
> from a toolkit and make it a sovereign AI workspace.

### Sprint H1 — Agent Identity Layer (C109–C111)
*Inspired by: Hermes soul.md, IBM watsonx workspace scoping, MIT persona research*
- **C109** `core/soul_loader.py` — Parse + validate `soul.md` persona blueprints per agent
- **C110** `core/agent_identity.py` — Runtime identity object injected into every LLM prompt
- **C111** `core/pantheon_router.py` — Score + route tasks to best-fit persona/model combo (MIT reward model pattern)

### Sprint H2 — Memory Vault (C112–C114)
*Inspired by: Mem0 layered memory, Hermes memory.md, VectorSearchIndex (C74)*
- **C112** `core/memory_vault.py` — Unified markdown + SQLite + vector memory with context-injection API
- **C113** `core/context_compressor.py` — LLM-based summarisation + /compress trigger + auto-compress at budget
- **C114** `core/memory_sync_agent.py` — Scheduled export of memory state to GitHub (portable snapshot)

### Sprint H3 — Operational Controls (C115–C117)
*Inspired by: Hermes slash commands, AutoGen event-driven runtime, MIT SUPER-agent*
- **C115** `core/command_parser.py` — /q /background /reset /compress /model /stop inline parser
- **C116** `core/goal_budget.py` — N-turn constraint loop · auto-compress at exhaustion · power/economy/balanced modes
- **C117** `core/sub_agent_orchestrator.py` — Parallel sub-agents with isolated context windows + solvability scoring + result merge

### Sprint H4 — Model & Channel Routing (C118–C120)
*Inspired by: Google Gemini model-agnostic routing, Hermes 22-channel pattern, CodeMender sandboxing*
- **C118** `core/model_router.py` — Dynamic LLM swap with cost-aware routing + Ollama offline fallback
- **C119** `core/channel_router.py` — Unified adapter: Telegram, Discord, Slack, WhatsApp, Matrix, Web
- **C120** `core/os_action_executor.py` — Sandboxed OS execution · permission gates · full audit trail

### Sprint I1 — Input Intelligence (C121–C123)
*Inspired by: Query rewriting research, Google ReAct paper (Yao et al. 2023), IBM clarification gates*
- **C121** `core/prompt_normalizer.py` — Raw input → grammar fix → de-ambiguate → reconstructed clean query
- **C122** `core/intent_classifier.py` — Classify intent type → tool / skill / agent / model routing decision
- **C123** `core/reasoning_engine.py` — ReAct loop: Thought → Action → Observation → Thought + stored reasoning trace

### Sprint I2 — Self-Healing & Learning (C124–C126)
*Inspired by: Google CodeMender, AutoGen reflexion, Bloom's Taxonomy mastery model*
- **C124** `core/self_reflection_loop.py` — Error pattern detection → candidate fix generation → sandboxed validation → plugin registration
- **C125** `core/token_budget_manager.py` — Per-session token economy: power / balanced / economy modes, user-selectable
- **C126** `core/domain_model_registry.py` — Register + route to domain-specific models: law, science, psychology, physics, code

### Sprint I3 — Ecosystem & Portability (C127–C129)
*Inspired by: IBM watsonx workspace export, federated learning research, Bloom's mastery loop*
- **C127** `core/workspace_exporter.py` — Export full ShadowRealm context (agents, skills, memory, prefs) as portable ZIP
- **C128** `core/community_skill_library.py` — Opt-in anonymised skill submission + versioned community library
- **C129** `core/skill_trainer.py` — 3-stage mastery loop: Show (observe) → Practice (guided) → Demonstrate (independent + explain)

---

## Key Reference Sources

| Source | Principle Applied |
|---|---|
| MIT AI Agent Index 2025 | Autonomy levels, accountability per action, eval methodology |
| MIT SUPER-agent paper (arXiv:2410.02189) | Task decomposition, solvability scoring, reward model routing |
| ReAct paper — Yao et al. ICLR 2023 | Thought→Action→Observation loop, reasoning trace storage |
| IBM watsonx Orchestrate | Workspace isolation, role scoping, portable export |
| Google DeepMind CodeMender | Self-healing: detect → generate → validate → register |
| Google Gemini 2.5 reasoning traces | Explainable AI: visible reasoning panel alongside response |
| Microsoft AutoGen | Three-layer architecture: Core / AgentChat / Extensions |
| Mem0 | Layered memory: working / episodic / semantic |
| n8n / Zapier | Workflow engine: trigger → condition → action graph |
| Bloom's Taxonomy | Skill mastery: show → practice → demonstrate |
| Federated learning research | Community skill library: anonymised, opt-in, versioned |

---

## Design Principles

1. **Zero external deps for core** — every `core/` module runs on stdlib alone
2. **Least privilege** — all agents operate under explicit permission grants (MIT autonomy principle)
3. **Local-first** — full functionality without cloud; cloud features are strictly additive
4. **Compounding memory** — context stacks across sessions; nothing is lost silently
5. **Model agnostic** — swap any LLM backend without touching business logic
6. **Observable** — every action is logged, audited, and always traceable
7. **Always explainable** — every response carries a reasoning trace accessible on demand
8. **Self-healing** — errors feed the reflection loop; the system gets smarter from failures
9. **Clean interface** — sophisticated inside, minimal outside; power users go deep, casual users stay clean
10. **Portable** — any user's full workspace can be exported, versioned, and restored
