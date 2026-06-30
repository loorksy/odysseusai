# ShadowRealm v2 — Master Checklist

> Last updated: 2026-06-29
> Branch: `shadowrealm-v2`
> **90 / 129 components complete**

Legend: ✅ Shipped · 🔄 In Progress · ⬜ Queued

---

## Block 1 — Foundation & Core (C1–C18) ✅

- [x] C1  `core/config_loader.py` — Multi-source config (env / YAML / TOML / CLI)
- [x] C2  `core/structured_logger.py` — JSON structured logging with levels + context fields
- [x] C3  `core/error_handler.py` — Typed exception hierarchy + global handler + recovery hooks
- [x] C4  `core/event_bus.py` — Pub/sub event bus with sync + async dispatch
- [x] C5  `core/plugin_manager.py` — Plugin discovery, load, enable/disable lifecycle
- [x] C6  `core/service_registry.py` — Service locator with lazy init + health tracking
- [x] C7  `core/cli_framework.py` — Arg parsing, subcommands, help generation
- [x] C8  `core/env_validator.py` — Required env var validation with typed coercion
- [x] C9  `core/task_runner.py` — Sync/async task execution with timeout + cancellation
- [x] C10 `core/feature_flags.py` — Runtime feature toggles with rollout % support
- [x] C11 `core/health_probe.py` — Liveness + readiness probes for all registered services
- [x] C12 `core/rate_limiter_core.py` — Token bucket rate limiter (core, no HTTP dependency)
- [x] C13 `core/circuit_breaker.py` — Trip/half-open/close state machine for external calls
- [x] C14 `core/retry_policy.py` — Exponential backoff + jitter + max-attempts policy
- [x] C15 `core/dependency_injector.py` — Constructor injection container with scope management
- [x] C16 `core/signal_handler.py` — SIGTERM/SIGINT graceful shutdown sequencer
- [x] C17 `core/process_manager.py` — Subprocess lifecycle: start, monitor, restart, stop
- [x] C18 `core/startup_sequencer.py` — Ordered boot graph with dependency-aware init

---

## Block 2 — Data & Storage (C19–C36) ✅

- [x] C19 `core/database_manager.py` — SQLite / Postgres abstraction with connection pooling
- [x] C20 `core/migration_engine.py` — Schema version tracking + up/down migration runner
- [x] C21 `core/model_registry.py` — Data model definitions + schema validation
- [x] C22 `core/l1_memory_cache.py` — In-process LRU cache
- [x] C23 `core/l2_disk_cache.py` — Disk-backed persistent cache with TTL
- [x] C24 `core/l3_distributed_cache.py` — Redis-compatible distributed cache adapter
- [x] C25 `core/file_store.py` — Local file read/write/list/delete with path safety
- [x] C26 `core/blob_store.py` — Binary large object store (local + S3-compatible)
- [x] C27 `core/document_store.py` — Schema-flexible JSON document store with indexing
- [x] C28 `core/graph_store.py` — Node/edge graph store with traversal queries
- [x] C29 `core/time_series_store.py` — Append-only time-series with windowed aggregation
- [x] C30 `core/data_validator.py` — Schema-based validation with error path reporting
- [x] C31 `core/serialiser.py` — JSON / MessagePack / Pickle serialisation with type safety
- [x] C32 `core/query_builder.py` — Composable SQL/NoSQL query builder
- [x] C33 `core/connection_pool.py` — Generic connection pool with health eviction
- [x] C34 `core/schema_registry.py` — Versioned schema storage + compatibility checks
- [x] C35 `core/data_masker.py` — PII field masking + redaction for logs and exports
- [x] C36 `core/backup_manager.py` — Scheduled + on-demand backup with rotation

---

## Block 3 — Security & Auth (C37–C54) ✅

- [x] C37 `core/jwt_auth.py` — JWT issue/verify/refresh with RS256
- [x] C38 `core/oauth2_provider.py` — OAuth2 authorization code + PKCE flow
- [x] C39 `core/rbac_engine.py` — Role-based access control with inheritance
- [x] C40 `core/http_rate_limiter.py` — Per-IP / per-user HTTP rate limiter
- [x] C41 `core/input_validator.py` — Sanitise + validate all external inputs
- [x] C42 `core/secret_manager.py` — Encrypted secret store + env injection
- [x] C43 `core/encryption.py` — AES-256-GCM encrypt/decrypt + key derivation
- [x] C44 `core/key_rotation.py` — Automatic key rotation with zero-downtime migration
- [x] C45 `core/hmac_signer.py` — HMAC-SHA256 request signing + verification
- [x] C46 `core/tls_context.py` — TLS context builder with cert pinning support
- [x] C47 `core/session_token_store.py` — Secure session token storage + invalidation
- [x] C48 `core/csrf_guard.py` — CSRF token generation + double-submit cookie check
- [x] C49 `core/ip_allowlist.py` — IP/CIDR allowlist + blocklist management
- [x] C50 `core/audit_trail.py` — Immutable audit record: who did what, when, why
- [x] C51 `core/content_policy.py` — Content moderation rules + violation reporting
- [x] C52 `core/threat_model.py` — Runtime threat surface evaluation
- [x] C53 `core/security_scanner.py` — Dependency + config vulnerability scanner
- [x] C54 `core/permission_matrix.py` — Static permission definition matrix

---

## Block 4 — Observability (C55–C63) ✅

- [x] C55 `core/log_aggregator.py` — Multi-source log collection + structured routing
- [x] C56 `core/trace_collector.py` — Distributed trace span collection (OpenTelemetry-compatible)
- [x] C57 `core/metrics_collector.py` — Counter/gauge/histogram metric collection
- [x] C58 `core/alert_manager.py` — Threshold + anomaly alert rules + notification routing
- [x] C59 `core/dashboard_api.py` — Metrics + health data API for UI dashboards
- [x] C60 `core/health_checker.py` — Deep health checks with dependency graph
- [x] C61 `core/metrics_registry.py` — Central metric definition + registration
- [x] C62 `core/span_exporter.py` — Export traces to Jaeger / Zipkin / OTLP
- [x] C63 `core/diagnostics_reporter.py` — System diagnostics snapshot + export

---

## Block 5 — Task & Job Queue (C64–C66) ✅

- [x] C64 `core/task_queue.py` — Priority queue + retry + dead-letter queue
- [x] C65 `core/job_scheduler.py` — Interval / cron / one-shot scheduler
- [x] C66 `core/worker_pool.py` — Thread pool with graceful drain + exponential backoff

---

## Block 6 — Networking & HTTP (C67–C69) ✅

- [x] C67 `core/http_client.py` — Retry + auth + timeout HTTP client
- [x] C68 `core/webhook_dispatcher.py` — HMAC-signed outbound webhook delivery
- [x] C69 `core/rate_limited_session.py` — Token bucket HTTP session

---

## Block 7 — Event Sourcing & Audit (C70–C72) ✅

- [x] C70 `core/event_store.py` — Append-only event log + snapshots
- [x] C71 `core/audit_logger.py` — Hash-chained immutable audit log
- [x] C72 `core/change_tracker.py` — Field-level diff + rollback

---

## Block 8 — Search & Indexing (C73–C75) ✅

- [x] C73 `core/full_text_index.py` — FTS5/BM25 full-text search
- [x] C74 `core/vector_search_index.py` — Cosine similarity vector search
- [x] C75 `core/search_router.py` — Hybrid RRF fusion of FTS + vector results

---

## Block 9 — User & Session (C76–C78) ✅

- [x] C76 `core/user_store.py` — PBKDF2-HMAC-SHA256 · roles · soft-state · profile blob
- [x] C77 `core/session_manager.py` — CSPRNG tokens · sliding TTL · device tracking
- [x] C78 `core/user_preference_store.py` — Namespaced prefs · typed get/set · change callbacks

---

## Block 10 — Notification & Messaging (C79–C81) ✅

- [x] C79 `core/notification_dispatcher.py` — Multi-channel fanout · dedup · pluggable adapters
- [x] C80 `core/in_app_message_queue.py` — SQLite inbox · read/unread/archived · TTL
- [x] C81 `core/email_composer.py` — Template registry · SMTP · pluggable transport · dry-run

---

## Block 11 — Permission & Policy (C82–C84) ✅

- [x] C82 `core/permission_manager.py` — Fine-grained permission graph
- [x] C83 `core/policy_engine.py` — Declarative allow/deny rule evaluation
- [x] C84 `core/access_control_list.py` — Per-resource ACL with inheritance

---

## Block 12 — Plugin & Extension (C85–C87) ✅

- [x] C85 `core/plugin_registry.py` — Versioned manifest + dependency resolver
- [x] C86 `core/plugin_sandbox.py` — Isolated execution for untrusted plugins
- [x] C87 `core/extension_loader.py` — Hot-reload without restart

---

## Block 13 — AI / LLM Integration (C88–C90) ✅

- [x] C88 `core/llm_client.py` — Unified API: OpenAI / Anthropic / Gemini / Ollama + streaming
- [x] C89 `core/tool_registry.py` — OpenAI function-calling spec tool definitions
- [x] C90 `core/llm_response_parser.py` — Structured output extraction + validation

---

## Block 14 — Workflow Engine (C91–C93) ⬜

- [ ] C91 `core/workflow_definition.py` — Trigger → condition → action node graph
- [ ] C92 `core/workflow_executor.py` — DAG execution: branch, loop, parallel nodes
- [ ] C93 `core/workflow_registry.py` — Store, version, activate/deactivate workflows

---

## Block 15 — Data Pipeline (C94–C96) ⬜

- [ ] C94 `core/pipeline_builder.py` — Composable ETL step chain
- [ ] C95 `core/data_transformer.py` — Map/filter/reduce/join operations
- [ ] C96 `core/pipeline_scheduler.py` — Schedule + monitor pipelines

---

## Block 16 — Multimodal & Media (C97–C99) ⬜

- [ ] C97 `core/media_processor.py` — Image/audio/video metadata + format conversion
- [ ] C98 `core/transcription_adapter.py` — Speech-to-text bridge (Whisper + backends)
- [ ] C99 `core/vision_adapter.py` — Image understanding (vision model API wrapper)

---

## Block 17 — Testing Infrastructure (C100–C102) ⬜

- [ ] C100 `core/test_harness.py` — Agent behavior test runner
- [ ] C101 `core/mock_tool_registry.py` — Deterministic mock tools for isolated testing
- [ ] C102 `core/regression_tracker.py` — Capability regression tracking across versions

---

## Block 18 — Deployment & DevOps (C103–C105) ⬜

- [ ] C103 `core/deployment_manager.py` — Blue/green deploy + rollback
- [ ] C104 `core/config_drift_detector.py` — Detect + alert on config drift
- [ ] C105 `core/release_gate.py` — Automated pre-release checks

---

## Block 19 — Integration Adapters (C106–C108) ⬜

- [ ] C106 `integrations/github_adapter.py` — GitHub API: repos, issues, PRs, commits
- [ ] C107 `integrations/calendar_adapter.py` — Calendar read/write
- [ ] C108 `integrations/browser_adapter.py` — Headless browser automation

---

## Block 20 — Agent Identity Layer (C109–C111) ⬜
*Principle: Every agent has a defined identity, purpose, and model tier. Source: IBM watsonx, Hermes soul.md*

- [ ] C109 `core/soul_loader.py` — Parse + validate soul.md persona blueprints
- [ ] C110 `core/agent_identity.py` — Runtime identity injected into every LLM call
- [ ] C111 `core/pantheon_router.py` — Score + route tasks to best-fit agent/model (MIT reward model)

---

## Block 21 — Memory Vault (C112–C114) ⬜
*Principle: Memory compounds across sessions. Working → episodic → semantic layers. Source: Mem0, Hermes*

- [ ] C112 `core/memory_vault.py` — Unified markdown + SQLite + vector memory with context-injection API
- [ ] C113 `core/context_compressor.py` — LLM summarisation + /compress trigger + budget auto-compress
- [ ] C114 `core/memory_sync_agent.py` — Scheduled GitHub snapshot of full memory state

---

## Block 22 — Operational Controls (C115–C117) ⬜
*Principle: User controls the agent runtime explicitly. Source: Hermes, AutoGen event-driven runtime*

- [ ] C115 `core/command_parser.py` — /q /background /reset /compress /model /stop parser
- [ ] C116 `core/goal_budget.py` — N-turn loops · power/balanced/economy modes · auto-compress
- [ ] C117 `core/sub_agent_orchestrator.py` — Parallel sub-agents · isolated contexts · solvability scoring

---

## Block 23 — Model & Channel Routing (C118–C120) ⬜
*Principle: One brain, many interfaces. Any model, any channel. Source: Hermes, Gemini, AutoGen*

- [ ] C118 `core/model_router.py` — Cost-aware dynamic LLM routing + Ollama offline fallback
- [ ] C119 `core/channel_router.py` — Telegram / Discord / Slack / WhatsApp / Matrix / Web adapter
- [ ] C120 `core/os_action_executor.py` — Sandboxed OS actions + permission gates + audit

---

## Block 24 — Input Intelligence (C121–C123) ⬜
*Principle: Clean input before routing. Reason before acting. Source: ReAct (Yao 2023), query rewriting research*

- [ ] C121 `core/prompt_normalizer.py` — Raw input → grammar fix → de-ambiguate → clean reconstructed query
- [ ] C122 `core/intent_classifier.py` — Intent type → tool / skill / agent / model routing decision
- [ ] C123 `core/reasoning_engine.py` — ReAct loop: Thought→Action→Observation + stored reasoning trace panel

---

## Block 25 — Self-Healing & Learning (C124–C126) ⬜
*Principle: Errors feed the reflection loop. The system gets smarter from failures. Source: CodeMender, Bloom's*

- [ ] C124 `core/self_reflection_loop.py` — Error detect → fix generate → sandbox validate → plugin register
- [ ] C125 `core/token_budget_manager.py` — Per-session token economy: power / balanced / economy modes
- [ ] C126 `core/domain_model_registry.py` — Route to domain models: law / science / psychology / physics / code

---

## Block 26 — Ecosystem & Portability (C127–C129) ⬜
*Principle: Everything is portable, sharable, and teachable. Source: IBM export, federated learning, Bloom's mastery*

- [ ] C127 `core/workspace_exporter.py` — Export full context (agents, skills, memory, prefs) as portable ZIP
- [ ] C128 `core/community_skill_library.py` — Opt-in anonymised skill submission + versioned community library
- [ ] C129 `core/skill_trainer.py` — 3-stage mastery: Show (observe) → Practice (guided) → Demonstrate (independent)

---

## Architecture Invariants

These rules must never be broken regardless of how the system grows:

1. `core/` modules — zero external dependencies, stdlib only
2. All agent actions — logged to `AuditLogger` (C71) before execution
3. All LLM calls — pass through `ReasoningEngine` (C123) ReAct loop
4. All user inputs — pass through `PromptNormalizer` (C121) before routing
5. All tool/agent dispatch — scored by `PantheonRouter` (C111) first
6. All memory writes — flow through `MemoryVault` (C112)
7. All external calls — governed by `PermissionManager` (C82) + `LeastPrivilege`
8. All errors — fed to `SelfReflectionLoop` (C124)
9. Every response — carries a `reasoning_trace` stored alongside it
10. Every workspace — exportable via `WorkspaceExporter` (C127) at any time
