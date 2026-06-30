<p align="center">
  <img src="docs/Shadow-Realm.PNG" alt="ShadowRealm" width="160">
</p>

<p align="center">
  A self-hosted AI workspace for chat, agents, research, documents, email, notes, calendar, and local model workflows.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="docs/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <img src="docs/odysseus-browser.jpg" alt="ShadowRealm interface">
</p>

---

## Overview

ShadowRealm is a self-hosted AI workspace that runs entirely on your own hardware or server. It connects local and cloud-hosted language models to a full suite of productivity tools — chat, agents, research, documents, email, notes, tasks, and calendar — through a single browser-based interface.

## Quick Start

> `dev` is the default branch and receives changes first. Switch to [`main`](https://github.com/ShadowWalkerNC/ShadowRealm/tree/main) for the more curated, stable branch.

```bash
git clone https://github.com/ShadowWalkerNC/ShadowRealm.git
cd ShadowRealm
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in:

```bash
docker compose logs shadowrealm
```

Native installs, GPU configuration, Windows/macOS instructions, HTTPS setup, and full configuration options are in the [setup guide](docs/setup.md).

## Features

- **Chat + Agents** — local and API-hosted models with tools, MCP support, file handling, shell access, skills, and persistent memory.
- **Cookbook** — hardware-aware model recommendations, automated downloads, and backend serving (Ollama, vLLM, SGLang, llama.cpp, LM Studio).
- **Deep Research** — multi-step web research with source reading and structured report generation.
- **Compare** — blind side-by-side model testing and answer synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — image gallery and editor, themes, file uploads, web search, presets, sessions, and 2FA.

## Tech Stack

| Layer | Details |
|---|---|
| Backend | Python (FastAPI / Uvicorn) |
| Frontend | Vanilla JS + CSS (served via static/) |
| AI Backends | Ollama, vLLM, SGLang, llama.cpp, LM Studio, OpenAI, Anthropic, Gemini, Groq, xAI, OpenRouter, DeepSeek |
| Storage | File-based (`data/`) + ChromaDB for vector search |
| Containerization | Docker + Docker Compose (CPU, NVIDIA GPU, AMD GPU variants) |
| Protocols | IMAP/SMTP (email), CalDAV (calendar), MCP (agent tools), ntfy (notifications) |

## Deployment Options

- **Docker (recommended)** — `docker-compose.yml` for CPU, `docker-compose.gpu-nvidia.yml` for NVIDIA, `docker-compose.gpu-amd.yml` for AMD.
- **Native Python** — Python 3.11+, `pip install -r requirements.txt`, run with Uvicorn.
- **macOS App** — `build-macos-app.sh` bundles a standalone `.app`.
- **Windows** — `launch-windows.ps1` for native launch; `build-windows-portable.ps1` for a portable build.
- **Linux Service** — `install-service.sh` registers a systemd service.

## Project Structure

```
app.py              # Main application entry point
launcher.py         # Cross-platform launcher
core/               # Core logic and utilities
routes/             # API route handlers
services/           # Background service integrations
integrations/       # External provider adapters
mcp_servers/        # MCP tool server definitions
companion/          # Companion app components
config/             # Configuration schemas
static/             # Frontend assets (JS, CSS, HTML)
docs/               # Setup guides and documentation
tests/              # Test suite
specs/              # Feature and API specifications
```

## Contributing

Help is welcome. The best entry points are fresh-install smoke testing, provider setup bugs, mobile/editor polish, documentation, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

> **Auto-generated PRs:** If you are running an LLM coding agent (Cursor, Claude Code, Copilot, etc.), open an issue describing the problem first rather than submitting a PR directly.

## Security

ShadowRealm is a self-hosted workspace with access to powerful local tools. Keep authentication enabled, keep private data out of Git, and do not expose raw model or service ports publicly. See [SECURITY.md](SECURITY.md) and the [setup guide](docs/setup.md#security-notes) for deployment hardening guidance.

## Roadmap

Current high-priority areas include bug triage, fresh-install smoke testing across platforms, Cookbook reliability improvements, Deep Research model presets by hardware, agent prompt/context optimization for smaller models, and email performance. See [ROADMAP.md](ROADMAP.md) for the full list.

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
