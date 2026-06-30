# ShadowRealm Integration Status

This file is the live audit of every integration, service, and feature in ShadowRealm.  
Update this file whenever a status changes. Do not leave "unknown" entries indefinitely.

**Legend:**
- ✅ Working — confirmed on fresh install
- ⚠️ Partial — works with manual steps or has known edge cases
- ❌ Broken — confirmed not working, needs fix
- 🔲 Untested — no confirmation either way
- 📝 Needs Docs — works but setup is undocumented or unclear

---

## AI Model Backends

| Provider | Status | Notes |
|---|---|---|
| Ollama (local) | ✅ Working | Primary local backend; Docker and native both work |
| OpenAI API | ✅ Working | Requires `OPENAI_API_KEY` in `.env` |
| Anthropic API | ⚠️ Partial | Needs provider setup/probing audit per ROADMAP |
| Gemini API | ⚠️ Partial | Needs provider setup/probing audit per ROADMAP |
| Groq API | ⚠️ Partial | Needs provider setup/probing audit per ROADMAP |
| xAI (Grok) API | ⚠️ Partial | Needs provider setup/probing audit per ROADMAP |
| OpenRouter | ⚠️ Partial | Needs provider setup/probing audit per ROADMAP |
| DeepSeek API | ⚠️ Partial | Needs provider setup/probing audit per ROADMAP |
| vLLM | 🔲 Untested | Docker compose variant available; not smoke tested |
| SGLang | ⚠️ Partial | Cross-platform reliability issues noted in ROADMAP |
| llama.cpp | 🔲 Untested | Listed as supported; no fresh-install test on record |
| LM Studio | 🔲 Untested | Listed as supported; requires LM Studio running locally |

---

## Memory & Vector Search

| Service | Status | Notes |
|---|---|---|
| ChromaDB (vector memory) | ⚠️ Partial | Wired in; degraded-state reporting is missing per ROADMAP |
| Persistent chat memory | ⚠️ Partial | Exists but inconsistent per upstream notes; Beta/Experimental |
| File-based storage (`data/`) | ✅ Working | Default storage; backup guide exists at docs/backup-restore.md |

---

## Agent Tools & MCP

| Tool / Server | Status | Notes |
|---|---|---|
| MCP tool calling | ✅ Working | Core MCP support is live |
| Shell/bash execution | ⚠️ Partial | Available in agent mode; security hardening needed |
| Web search (SearXNG) | ⚠️ Partial | Degraded-state reporting missing; requires SearXNG instance |
| File uploads | ✅ Working | Confirmed functional |
| Skills system | ⚠️ Partial | Prompt-injection audit needed per ROADMAP |
| Deep Research | ⚠️ Partial | Works; needs model presets by hardware class per ROADMAP |

---

## Productivity Integrations

| Service | Status | Notes |
|---|---|---|
| Email (IMAP/SMTP) | ⚠️ Partial | Works but performance issues on high-latency providers per ROADMAP |
| Email — Outlook/Exchange | 📝 Needs Docs | See docs/email-outlook.md (minimal, needs expansion) |
| CalDAV (calendar sync) | 🔲 Untested | Listed as supported; Radicale URL quirks documented in ROADMAP |
| ntfy (notifications) | ⚠️ Partial | Android Instant Delivery issues with non-ntfy.sh servers per ROADMAP |
| Notes | ✅ Working | Functional; AI integration needs improvement per ROADMAP |
| Tasks / Todos | ✅ Working | Functional; agent assignment from UI not yet built |

---

## Deployment Methods

| Method | Status | Notes |
|---|---|---|
| Docker (CPU) | ✅ Working | Primary recommended path |
| Docker (NVIDIA GPU) | ⚠️ Partial | Available; driver/CUDA compatibility varies |
| Docker (AMD GPU) | 🔲 Untested | Compose file exists; not smoke tested |
| Native Python (Linux) | 🔲 Untested | Needs fresh-install smoke test |
| Native Python (macOS) | 🔲 Untested | Needs fresh-install smoke test |
| Native Python (Windows) | 🔲 Untested | `launch-windows.ps1` exists; no confirmed test |
| WSL | 🔲 Untested | Mentioned in ROADMAP as needing coverage |
| Linux systemd service | 🔲 Untested | `install-service.sh` exists; not smoke tested |
| macOS .app bundle | 🔲 Untested | `build-macos-app.sh` exists; not smoke tested |
| Windows portable build | 🔲 Untested | `build-windows-portable.ps1` exists; not smoke tested |

---

## Frontend Features

| Feature | Status | Notes |
|---|---|---|
| Chat (multi-model) | ✅ Working | Core feature; functional |
| Compare mode | ✅ Working | Side-by-side model testing |
| Image gallery & editor | ⚠️ Partial | Mobile polish needed per ROADMAP |
| Themes | ✅ Working | Multiple themes available; CSS cleanup needed |
| 2FA | ✅ Working | Listed as functional |
| Onboarding tours | ⚠️ Partial | Tours fight each other; need refactor per ROADMAP |
| Mobile layout | ⚠️ Partial | Media override bugs; accessibility pass needed |

---

## Planned / Not Yet Built (v2 Targets)

| Feature | Sprint | Notes |
|---|---|---|
| Token compression middleware | Sprint 2 | LLMLingua integration + auto-compaction |
| 5-tier memory architecture | Sprint 5 | Extends existing ChromaDB |
| Notion live sync | Sprint 5 | Via Notion MCP |
| Obsidian vault integration | Sprint 5 | Local folder indexing |
| 3-panel IDE layout | Sprint 4 | File tree + agent + preview/terminal |
| Live preview pane | Sprint 6 | Subprocess dev server + iframe |
| xterm.js terminal panel | Sprint 6 | In-browser bash/shell |
| browser-use integration | Sprint 6 | AI agent browser control |
| First-run onboarding wizard | Sprint 7 | Replaces fragmented tours |
| Teach Mode + Skill Library | Sprint 8 | Human-to-AI skill transfer |
| Built-in analytics dashboard | Sprint 9 | Self-hosted PostHog + Grafana |

---

## Known Blockers

- **Context/token bloat in agent mode** — tool schemas + memory + skills exhaust context on small models. Fix in Sprint 2.
- **CSS spaghetti in `static/style.css`** — blocks any UI work. Fix in Sprint 3 before Sprint 4.
- **Upstream Odysseus branding in `docs/`** — `odysseus-browser.jpg`, `odysseus-wordmark.png`, `odysseus.jpg` need to be replaced with ShadowRealm assets.
- **Onboarding tours conflict** — `tour-core.js` refactor needed before wizard is built in Sprint 7.

---

*Last updated: 2026-06-29 — ShadowRealm v2 audit pass*
