# ShadowRealm v2.0 — Master Build Plan

> **Branch:** `shadowrealm-v2` → merges to `dev` → merges to `main` on v2.0.0 tag
> **Target Score:** 9 / 10
> **Total Commits:** 107
> **Total Sprints:** 11
> **Estimated Duration:** 11–12 weeks solo
> **Last Updated:** 2026-06-29
> **Audit Date:** 2026-06-29 — Skills-First + Skill Factory architecture adopted

---

## North Star: Skills-First, Self-Improving Agent Platform

ShadowRealm's core thesis:
> **Models are commodities. The harness and skills around them are the differentiator.**

Every capability lives in a skill. Every skill is improvable. The platform teaches itself.

### The Four Pillars

1. **Progressive Disclosure** — Only a skill's `name + description` (~53 tokens) sits in context at all times. Full instructions load on-demand. No agent.md wall consuming 944+ tokens every turn.
2. **Skill Factory (meta-skill loop)** — A skill that writes skills. Walk through a workflow live → achieve success in context → tell the agent "write the skill" → feed failures back to `skill_refiner` → repeat until perfect.
3. **Trainable by Design** — You guide the agent through new workflows. The agent observes, annotates, and crystallizes those workflows into versioned procedural skills. Teach Mode is not a feature — it is the core interaction model.
4. **API Harnesses Everywhere** — Every capability exposes a `/api` endpoint. External tools, UIs, sub-agents, and CI pipelines can call any skill or agent action programmatically.

### The Self-Improving Loop

```
┌─────────────────────────────────────────────────────────────────┐
│                   SKILL FACTORY LOOP                            │
│                                                                 │
│  1. Human guides agent through workflow (Teach Mode / chat)     │
│  2. Agent executes steps live — context window holds trace      │
│  3. On success: POST /api/skills/generate                       │
│     → agent reviews own trace → writes skills/*.md file        │
│  4. On failure: trace + error → skill_refiner meta-skill        │
│     → skill_refiner patches the .md → SkillsManager updates    │
│  5. After 3–5 cycles: skill executes without human guidance     │
│  6. Nightly ReflectionEngine audits all traces → proposes      │
│     improvements → human approves → skills auto-update         │
└─────────────────────────────────────────────────────────────────┘
```

### Progressive Disclosure Contract

Every `skills/*.md` file MUST follow this structure:

```markdown
# skill_name
## Description
One sentence. This is all that loads into context by default.
## Trigger
When to invoke this skill (keywords or conditions).
## Instructions
Full step-by-step. Loaded only when skill is active.
## Examples
Optional. Loaded only on first invocation or low-confidence.
## Failure Modes
Known errors + fixes. Fed to skill_refiner on failure.
```

The `SkillRegistry` loads only `name + Description` at startup.
Full content is injected only when the skill is selected for the current task.

---

## What's Already Shipped (Pre-Plan)

A full read of `dev` revealed these were already production-grade before v2 planning began:

| System | File | Status |
|---|---|---|
| Skills Engine (CRUD, versioning, slash-invoke, search) | `routes/skills_routes.py` (77KB) | ✅ Shipped |
| LLM-as-judge skill test runner | `routes/skills_routes.py` | ✅ Shipped |
| Self-edit + retry on failing skills | `routes/skills_routes.py` | ✅ Shipped |
| Teacher model escalation | `routes/skills_routes.py` | ✅ Shipped |
| Necessity + redundancy checker | `routes/skills_routes.py` | ✅ Shipped |
| Retrieval precision auditor | `routes/skills_routes.py` | ✅ Shipped |
| Nightly scheduled skill audit | `routes/skills_routes.py` | ✅ Shipped |
| Auto-publish policy + confidence scoring | `routes/skills_routes.py` | ✅ Shipped |
| Degraded-state health reporting | `src/service_health.py` | ✅ Shipped |
| GET /api/diagnostics/services endpoint | `routes/diagnostics_routes.py` | ✅ Shipped |
| Shell execution backend | `routes/shell_routes.py` (74KB) | ✅ Shipped |
| Codex/coding route | `routes/codex_routes.py` (43KB) | ✅ Shipped |
| Webhook ingestion | `routes/webhook_routes.py` | ✅ Shipped |
| Secrets vault | `routes/vault_routes.py` | ✅ Shipped |
| MCP protocol + servers (email, memory, RAG, image_gen) | `routes/mcp_routes.py` | ✅ Shipped |
| Security middleware + CSP + internal tool token | `core/middleware.py` | ✅ Shipped |
| Multi-model compare | `routes/compare_routes.py` | ✅ Shipped |
| Research pipeline sub-module | `routes/research/` | ✅ Shipped |
| TokenCounter utility | `core/token_counter.py` | ✅ Shipped (Sprint 2 C09) |
| Context size profiles | `core/context_profiles.py` | ✅ Shipped (Sprint 2 C10) |
| Slim MCP tool injection | `core/tool_selector.py` | ✅ Shipped (Sprint 2 C11) |
| Auto-compaction middleware | `core/compaction_middleware.py` | ✅ Shipped (Sprint 2 C12) |
| Token panel API | `api/tokens.py` | ✅ Shipped (Sprint 2 C13) |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                      ShadowRealm UI                              │
│   IDE layout: file tree / agent chat / preview+terminal          │
│   + Token Panel  + Skill Library  + Agent Cards  + Teach Mode    │
└────────────────────────┬─────────────────────────────────────────┘
                         │ MCP Protocol + REST /api harnesses
┌────────────────────────▼─────────────────────────────────────────┐
│               Agent Orchestration Layer                          │
│                                                                  │
│  ┌───────────────┐  ┌─────────────────┐  ┌──────────────────┐   │
│  │  LangGraph    │  │   AutoGen       │  │     CrewAI       │   │
│  │  (Research)   │  │   (Coding)      │  │  (Scheduled)     │   │
│  └───────┬───────┘  └────────┬────────┘  └────────┬─────────┘   │
│          └──────────────────┬┘───────────────────┘              │
│                         Task Router                             │
│                    (classifies → dispatches)                    │
└────────────────────────┬─────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│           Skills Engine  ✅ ALREADY RUNNING                      │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            Progressive Disclosure Layer                  │   │
│  │  SkillRegistry: loads name+description only (~53 tok)    │   │
│  │  ToolSelector: injects only task-relevant tools          │   │
│  │  CompactionMiddleware: 80% threshold → auto-summarize    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Skill Factory Loop                          │   │
│  │  skill_creator → writes skills from live traces          │   │
│  │  skill_refiner → patches skills from failure logs        │   │
│  │  Teach Mode   → human-guided workflow → crystallize      │   │
│  │  ReflectionEngine → nightly audit → improvement queue   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  LLM-as-judge │ Self-edit+retry │ Teacher escalation │ Audit    │
│  Necessity check │ Retrieval audit │ Auto-publish policy        │
└────────────────────────┬─────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│                   Execution Layer                                │
│  Open Hands Runtime │ MCP Servers │ Shell ✅ │ Codex ✅          │
│  browser-use        │ xterm.js    │ filesystem MCP               │
└────────────────────────┬─────────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│                 5-Tier Memory Layer                              │
│  Hot (context) → Warm (ChromaDB ✅) → Cool (Notion/Obsidian)    │
│  → Episodic (summaries) → Procedural (skills ✅)                │
└──────────────────────────────────────────────────────────────────┘
```

---

## Design Rules (Never Break These)

1. **Skills-first** — Every capability lives in a `skills/*.md` file following the progressive disclosure contract.
2. **No agent.md wall** — Full instructions never sit in context permanently. Name + description only.
3. **Train by doing** — Walk through workflows live. The agent writes the skill from its own trace.
4. **Failures are inputs** — Every error routes to `skill_refiner`. Skills get patched, not humans.
5. **API harness for everything** — Every skill and agent action exposes a `/api` endpoint.
6. **One framework per pipeline type** — LangGraph=Research, AutoGen=Coding, CrewAI=Scheduled. Never mixed.
7. **One commit per task** — atomic, reversible, readable git history.
8. **Never build on unstable ground** — each sprint must be stable before the next begins.
9. **Skills engine is already running** — do not replace it, extend it.
10. **Token compression before memory expansion** — Sprint 2 before Sprint 5.
11. **Fix only the error** — PatchEngine constraint; never rewrite working code alongside broken code.
12. **Every MCP server is a skill** — existing `email_server.py`, `memory_server.py`, `rag_server.py`, `image_gen_server.py` auto-register at startup.
13. **All agents read/write the same memory layer** — no siloed memory per agent.
14. **Self-healing is L1→L3 for v2.0, L4→L5 for v2.1** — ship monitor+diagnose+patch-with-approval first.

---

## Self-Healing Maturity Ladder

| Level | Capability | Status |
|---|---|---|
| L1 — Monitor | Detects failures, alerts human | ✅ Shipped |
| L2 — Diagnose | Classifies root cause, shows fix | ✅ Shipped |
| L3 — Patch with approval | Self-edit + teacher escalation + human review | ✅ Shipped (needs review UI) |
| L4 — Auto-patch low-risk | Auto-commits known safe error type fixes | 🔲 v2.1 scaffold in Sprint 7B |
| L5 — Reflect and improve | Proactively rewrites skills to be better | 🔲 v2.1 scaffold in Sprint 7B |

---

## Skill Factory Maturity Ladder

| Level | Capability | Status |
|---|---|---|
| SF1 — Manual skill write | Human writes skills/*.md by hand | ✅ Always available |
| SF2 — Trace-to-skill | Agent reviews own trace, writes skill via skill_creator | ✅ Sprint 3 C15 |
| SF3 — Failure refinement | skill_refiner patches skill from error log automatically | ✅ Sprint 3 C16 |
| SF4 — Teach Mode crystallize | Human-guided workflow → auto-crystallized procedural skill | 🔲 Sprint 7 |
| SF5 — Nightly self-improvement | ReflectionEngine proposes skill rewrites from 24h traces | 🔲 Sprint 7B |
| SF6 — Autonomous skill evolution | Agent autonomously evolves skills, human reviews diffs | 🔲 v2.1 |

---

## Complete Commit Checklist

### SPRINT 1 — Foundation Stabilization
> Goal: Any developer can clone and run in under 10 minutes.
> Dependency: None. Start here.

- [x] **C01** `fix: audit all integrations — docs/STATUS.md` ✅ DONE
- [x] **C02** `docs: update .env.example — ShadowRealm branding + quick-reference` ✅ DONE
- [x] **C03** `feat: degraded-state reporting` ✅ ALREADY SHIPPED
- [x] **C04** `fix: dead code pass — removed ChatGPT Subscription device-flow stub` ✅ DONE
- [x] **C05** `feat: GET /api/diagnostics/services health endpoint` ✅ ALREADY SHIPPED
- [x] **C06** `feat: /status UI page — wire to existing /api/diagnostics/services` ✅ DONE
- [x] **C07** `feat: setup.sh — OS-detect, dep install, .env builder, launch` ✅ DONE
- [x] **C08** `docs: update AGENTS.md — full ShadowRealm identity, stack, active agents, skills-first philosophy` ✅ DONE

---

### SPRINT 2 — Token & Context Compression
> Goal: Never hit a context wall. Agent mode works on small local models.
> Dependency: Sprint 1 complete.

- [x] **C09** `feat: TokenCounter utility — tracks usage per session, per skill, per MCP call` ✅ DONE
- [x] **C10** `feat: context size profiles — auto-detect model window, select small/medium/large profile` ✅ DONE
- [x] **C11** `feat: slim MCP tool injection — only surface tools relevant to current task` ✅ DONE
- [x] **C12** `feat: auto-compaction at 80% context threshold — summarize via FastAPI middleware` ✅ DONE
- [x] **C13** `feat: /api/tokens panel endpoint — live usage stats and profile info` ✅ DONE
- [x] **C14** `test: Sprint 2 unit tests — TokenCounter, context profiles, ToolSelector, compaction trigger` ✅ DONE

---

### SPRINT 3 — Skills-First Foundation (Agent Harness + Skill Factory)
> Goal: The self-improving loop is wired end-to-end. Skill Factory is operational.
> Dependency: Sprint 2 complete. This is the new keystone sprint.
> Philosophy: Build the meta-skills first, then the harness, then the API layer.

**Meta-Skills (the Skill Factory core):**
- [x] **C15** `feat: skill_creator meta-skill — writes new skills/*.md from successful workflow traces` ✅ DONE
- [x] **C16** `feat: skill_refiner meta-skill — patches failing skills from error logs` ✅ DONE
- [x] **C17** `feat: skill_template.md — canonical template enforcing progressive disclosure contract` ✅ DONE

**Agent Harness:**
- [x] **C18** `feat: SkillRegistry — loads skills/*.md, progressive disclosure (name+desc only in context)` ✅ DONE
- [x] **C19** `feat: AgentHarness — session mgmt, skill injection, tool routing, token tracking` ✅ DONE
- [x] **C20** `feat: Training Interface — training_mode flag, trace capture, guided workflow recording` ✅ DONE

**API Harnesses:**
- [x] **C21** `feat: /api/agent/chat — trainable agent entry point, streaming, session mgmt` ✅ DONE
- [x] **C22** `feat: /api/skills/generate — POST trace → agent writes skill from own context` ✅ DONE
- [x] **C23** `feat: /api/skills/refine — POST failure_log + skill_name → skill_refiner patches skill` ✅ DONE
- [x] **C24** `feat: /api/skills CRUD — list, get, create, update, delete, version history` ✅ DONE

**Tests:**
- [x] **C25** `test: Sprint 3 — SkillRegistry progressive disclosure, AgentHarness routing, skill_creator output validation` ✅ DONE

---

### SPRINT 4 — CSS & UI Architecture Cleanup
> Goal: Clean, documented, modular CSS. Required before any visual work.
> Dependency: Sprint 1 complete. MUST finish before Sprint 5 (visual).

- [x] **C26** `refactor: audit static/style.css — map all sections, mark dead rules, add headers` ✅ DONE
- [x] **C27** `refactor: extract CSS partials — layout.css, components.css, themes.css, utilities.css` ✅ DONE
- [x] **C28** `fix: modal and window positioning` ✅ DONE
- [x] **C29** `fix: mobile media override audit — comment and pair all desktop/mobile selectors` ✅ DONE
- [x] **C30** `refactor: promote tour-core.js helper — eliminate copy-pasted onboarding scaffolding` ✅ DONE
- [x] **C31** `fix: accessibility pass — keyboard nav, focus states, color contrast, reduced-motion` ✅ DONE

---

### SPRINT 5 — ShadowRealm Visual Identity
> Goal: Looks and feels like ShadowRealm.
> Dependency: Sprint 4 complete.

- [x] **C32** `style: design tokens — all colors, typography, spacing as CSS custom properties` ✅ DONE
- [x] **C33** `style: ShadowRealm palette — #0D0D0F base / #7B5CF0 primary / #00E5FF active` ✅ DONE
- [x] **C34** `style: typography — JetBrains Mono for code panels, Inter for UI chrome` ✅ DONE
- [x] **C35** `feat: 3-panel IDE layout — file tree / agent chat / preview+terminal` ✅ DONE
- [x] **C36** `style: redesign sidebar — project switcher, memory tier indicator, agent status badges` ✅ DONE
- [x] **C37** `style: motion + feedback layer — streaming animation, save confirms, loading states` ✅ DONE
- [x] **C38** `style: replace all upstream Odysseus images with ShadowRealm branded assets` ✅ DONE

---

### SPRINT 6 — 5-Tier Memory System
> Goal: The AI knows you, your projects, and your history — intelligently and cheaply.
> Dependency: Sprint 2 complete (compression needed before expanding memory).

- [ ] **C39** `feat: scaffold 5-tier memory — hot / warm / cool / episodic / procedural layers`
- [ ] **C40** `feat: integrate mem0 over existing memory_server.py + ChromaDB`
- [ ] **C41** `feat: per-project memory namespacing — isolate context per repo/workspace`
- [ ] **C42** `feat: episodic summarizer — auto-generates session recap on conversation close`
- [ ] **C43** `feat: Notion MCP server — live-sync Notion pages as cool-tier indexed memory`
- [ ] **C44** `feat: Obsidian vault integration — index local vault folder as searchable knowledge`
- [ ] **C45** `feat: memory import — JSON from Claude, ChatGPT, or custom format`
- [ ] **C46** `feat: memory tier visualizer panel — UI showing what is stored at each tier`

---

### SPRINT 7 — Developer Power Layer
> Goal: Full coding workspace. Open Hands as the runtime engine.
> Dependency: Sprint 5 complete (3-panel layout needed). Shell backend already exists.

- [ ] **C47** `feat: add open-hands service to docker-compose.yml`
- [ ] **C48** `feat: embed Open Hands iframe in 3-panel IDE layout`
- [ ] **C49** `feat: xterm.js terminal panel — wire to existing shell_routes.py backend`
- [ ] **C50** `feat: live preview pane — subprocess dev server, renders in iframe`
- [ ] **C51** `feat: auto-refresh preview on file save`
- [ ] **C52** `feat: browser-use integration — AI agent browser control + DOM analysis`
- [ ] **C53** `feat: file tree panel — open/edit/create/delete via MCP filesystem server`
- [ ] **C54** `feat: pre-wire dev MCP bundle at onboarding — GitHub, filesystem, browser-use, Notion`

---

### SPRINT 7B — Agent Pipeline Layer
> Goal: Real multi-agent orchestration. Three frameworks, one router, one memory layer.
> Dependency: Sprint 6 (memory) and Sprint 7 (execution) complete.

- [ ] **C55** `feat: Task Router — classifies requests to research / coding / scheduled pipeline`
- [ ] **C56** `feat: LangGraph research pipeline — query → search → read → summarize → memory_write → report`
- [ ] **C57** `feat: AutoGen coding pipeline — planner → coder → tester → reviewer → deployer`
- [ ] **C58** `feat: CrewAI scheduled pipeline — watcher → analyzer → notifier`
- [ ] **C59** `feat: Pipeline Monitor UI — live Kanban of all active agent tasks + state`
- [ ] **C60** `feat: wire all pipelines to 5-tier memory layer`
- [ ] **C61** `feat: wire all pipelines to Open Hands execution runtime`
- [ ] **C62** `test: pipeline integration tests — verify Task Router dispatches all three types`

---

### SPRINT 8 — Skills Layer Completion + Teach Mode
> Goal: Surface and extend what's already running. Teach Mode operational. Skill Factory fully wired to UI.
> Dependency: Sprint 7B (pipelines) complete.
> NOTE: Skills engine, LLM-as-judge, self-edit, teacher escalation, nightly audit — ALL ALREADY SHIPPED.

**Agent Bots — named personas wired to skill sets:**
- [ ] **C63** `feat: ShadowCoder — code_write, code_review, test_run, deploy, git_ops`
- [ ] **C64** `feat: ShadowResearcher — web_search, source_read, summarize, memory_write, report_gen`
- [ ] **C65** `feat: ShadowOps — shell_exec, file_manage, service_monitor, cron_schedule`
- [ ] **C66** `feat: ShadowMemory — memory_ingest, memory_compress, memory_retrieve, knowledge_sync`
- [ ] **C67** `feat: ShadowCreative — image_gen, image_edit, doc_write, content_draft`
- [ ] **C68** `feat: Agent Orchestrator — routes to correct bot, multi-agent collaboration`

**Skills UI — surface what's already running:**
- [ ] **C69** `feat: Skill Library panel — browse by agent / tag / pipeline / audit status`
- [ ] **C70** `feat: custom skill builder UI — no-code form wired to /api/skills CRUD`
- [ ] **C71** `feat: skill-to-agent assignment UI — drag/drop onto agent cards`
- [ ] **C72** `feat: skill import/export UI — JSON + skills.sh URL import`
- [ ] **C73** `feat: Skill Factory panel — run skill_creator / skill_refiner from UI with trace viewer`

**Teach Mode — human-guided workflow crystallization:**
- [ ] **C74** `feat: Teach Mode toggle — activates AI observation + trace capture layer`
- [ ] **C75** `feat: DOM/page analysis pipeline`
- [ ] **C76** `feat: interaction capture — records workflow steps with AI annotation`
- [ ] **C77** `feat: Teach Mode Q&A — AI asks clarifying questions per action in real time`
- [ ] **C78** `feat: crystallize to skill — completed Teach Mode session → POST /api/skills/generate → versioned procedural skill`

---

### SPRINT 8B — Self-Healing Completion + Reflection Engine
> Goal: Complete the 40% of self-healing not yet shipped. Wire ReflectionEngine to Skill Factory loop.
> Dependency: Sprint 8 complete. Already shipped: test runner, judge, self-edit, teacher escalation, nightly audit.

**Telemetry:**
- [ ] **C79** `feat: skill_traces table — log every execution (name, agent, tokens, latency, error_type, prompt, response)`
- [ ] **C80** `feat: query_traces MCP tool — agents query own execution history programmatically`
- [ ] **C81** `feat: trace dashboard — per-skill success rate, avg latency, token cost, error breakdown`

**Review UI:**
- [ ] **C82** `feat: self-healing review UI — diagnosis + proposed patch + approve/reject/rollback`
- [ ] **C83** `feat: skill quarantine UI — alerts when skill fails all retries, human review queue`
- [ ] **C84** `feat: patch rollback UI — one-click revert to any previous skill version`

**ReflectionEngine + Skill Factory integration (L4–L5 scaffold):**
- [ ] **C85** `feat: ReflectionEngine — nightly job reads 24h traces, identifies underperforming skills`
- [ ] **C86** `feat: ReflectionEngine → skill_refiner pipeline — auto-generates improvement proposals`
- [ ] **C87** `feat: improvement proposal queue — structured skill diffs for human review`
- [ ] **C88** `feat: proposal review UI — approve / reject / modify before SkillRegistry commit`
- [ ] **C89** `feat: learning mode toggle — continuous reflection when active; feeds Skill Factory loop`

---

### SPRINT 9 — Onboarding Wizard
> Goal: Zero-to-productive in under 10 minutes. Teaches the Skill Factory model on first run.
> Dependency: Sprint 8 complete.

- [ ] **C90** `feat: first-run detection — triggers wizard, gracefully skippable`
- [ ] **C91** `feat: onboarding quiz — stack, goals, languages, hardware, model preference`
- [ ] **C92** `feat: wire quiz to system prompt + default model selection`
- [ ] **C93** `feat: wire quiz to default agent skill assignments + active bot selection`
- [ ] **C94** `feat: API key setup step — guided entry with live test-connection per provider`
- [ ] **C95** `feat: memory import step — Claude / ChatGPT / Notion / Obsidian`
- [ ] **C96** `feat: agent activation step — choose bots, preview skill sets`
- [ ] **C97** `feat: Skill Factory intro step — guided first skill creation during onboarding`
- [ ] **C98** `feat: no-token path — full local Ollama setup with model recommendations by hardware`
- [ ] **C99** `docs: ONBOARDING.md`

---

### SPRINT 10 — Analytics, QA & Ship
> Goal: Full observability. Every edge smoothed. v2.0.0 tagged and merged.
> Dependency: All previous sprints complete.

- [ ] **C100** `feat: self-hosted PostHog — session tracking, event analytics, funnel views`
- [ ] **C101** `feat: analytics dashboard — token usage, model perf, session stats, cost estimates`
- [ ] **C102** `feat: skill execution analytics — usage, failure rates, token cost per skill`
- [ ] **C103** `feat: Grafana + Prometheus in docker-compose.yml`
- [ ] **C104** `fix: full QA pass — smoke test every sprint deliverable end-to-end`
- [ ] **C105** `fix: cross-platform test — Docker, native Python, Windows, macOS`
- [ ] **C106** `style: final polish — spacing, alignment, responsive layout, all edge cases`
- [ ] **C107** `docs: finalize README, STATUS.md, ONBOARDING.md, ROADMAP.md, CHANGELOG.md, AGENTS.md`
- [ ] **C108** `chore: tag v2.0.0 — merge shadowrealm-v2 → dev → main`

---

## Sprint Summary

| Sprint | Focus | Commits | Est. Time |
|---|---|---|---|
| 1 | Foundation stabilization | C01–C08 ✅ ALL DONE | — |
| 2 | Token & context compression | C09–C14 ✅ ALL DONE | — |
| 3 | **Skills-First: Agent Harness + Skill Factory** | C15–C17 ✅ / C18–C25 🔲 | ~6 days |
| 4 | CSS cleanup | C26–C31 | ~4 days |
| 5 | Visual identity | C32–C38 | ~5 days |
| 6 | 5-tier memory system | C39–C46 | ~9 days |
| 7 | Developer power layer | C47–C54 | ~7 days |
| 7B | Agent pipeline layer | C55–C62 | ~9 days |
| 8 | Skills UI + Teach Mode + agent bots | C63–C78 | ~6 days |
| 8B | Self-healing completion + Reflection | C79–C89 | ~6 days |
| 9 | Onboarding wizard | C90–C99 | ~6 days |
| 10 | Analytics, QA, ship | C100–C108 | ~5 days |
| **Total** | | **108 commits (19 done)** | **~11–12 weeks** |

---

## Branch Strategy

```
main              ← stable releases only (v2.0.0 tag here)
  └── dev         ← integration branch, sprint merges land here
        └── shadowrealm-v2  ← all development work
```

Merge `shadowrealm-v2` → `dev` at end of each sprint.
Merge `dev` → `main` only on C108 v2.0.0 tag.

---

## Current Progress

**Sprint 1:** ✅ COMPLETE (C01–C08 all done)
**Sprint 2:** ✅ COMPLETE (C09–C14 all shipped)
**Sprint 3:** 🔄 IN PROGRESS — C15 ✅ C16 ✅ C17 ✅ — Next: **C18** `SkillRegistry`

*This file is the single source of truth. Update the checklist as commits land.*
