#!/usr/bin/env bash
# =============================================================================
# ShadowRealm — setup.sh
# One-shot setup: OS detection, dependency install, .env builder, launch
# Usage: bash setup.sh [--no-launch] [--docker]
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}=== $* ===${RESET}\n"; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
LAUNCH=true
DOCKER=false
for arg in "$@"; do
  case $arg in
    --no-launch) LAUNCH=false ;;
    --docker)    DOCKER=true  ;;
    --help|-h)
      echo "Usage: bash setup.sh [--no-launch] [--docker]"
      echo "  --no-launch  Set up environment but do not start the app"
      echo "  --docker     Start via docker-compose instead of python launcher"
      exit 0 ;;
    *) warn "Unknown argument: $arg" ;;
  esac
done

# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------
header "Detecting environment"
OS="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  OS="linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
  OS="macos"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
  OS="windows"
fi
info "OS: $OS | Shell: $BASH_VERSION"

# ---------------------------------------------------------------------------
# Python check (3.10+)
# ---------------------------------------------------------------------------
header "Checking Python"
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    VER=$("$cmd" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    MAJOR=$(echo "$VER" | cut -d. -f1)
    MINOR=$(echo "$VER" | cut -d. -f2)
    if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 10 ]]; then
      PYTHON="$cmd"
      ok "Found $cmd $VER"
      break
    else
      warn "$cmd $VER is below the required 3.10 — skipping"
    fi
  fi
done
[[ -z "$PYTHON" ]] && error "Python 3.10+ not found. Install from https://python.org and re-run."

# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------
header "Setting up virtual environment"
VENV_DIR=".venv"
if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating virtual environment in $VENV_DIR …"
  "$PYTHON" -m venv "$VENV_DIR"
  ok "Virtual environment created"
else
  ok "Virtual environment already exists"
fi

# Activate
if [[ "$OS" == "windows" ]]; then
  ACTIVATE="$VENV_DIR/Scripts/activate"
else
  ACTIVATE="$VENV_DIR/bin/activate"
fi
# shellcheck source=/dev/null
source "$ACTIVATE"
ok "Virtual environment activated"

# ---------------------------------------------------------------------------
# Pip upgrade + dependency install
# ---------------------------------------------------------------------------
header "Installing dependencies"
pip install --upgrade pip --quiet
if [[ -f requirements.txt ]]; then
  info "Installing requirements.txt …"
  pip install -r requirements.txt --quiet
  ok "Core dependencies installed"
else
  warn "requirements.txt not found — skipping"
fi

if [[ -f requirements-optional.txt ]]; then
  read -r -p "$(echo -e "${YELLOW}Install optional dependencies? (image gen, browser-use, etc.) [y/N]:${RESET} ")" OPT
  if [[ "${OPT,,}" == "y" ]]; then
    pip install -r requirements-optional.txt --quiet
    ok "Optional dependencies installed"
  else
    info "Skipping optional dependencies"
  fi
fi

# ---------------------------------------------------------------------------
# .env builder
# ---------------------------------------------------------------------------
header "Environment configuration"
if [[ -f .env ]]; then
  ok ".env already exists — skipping interactive builder"
  warn "To reconfigure, delete .env and re-run setup.sh"
else
  if [[ ! -f .env.example ]]; then
    error ".env.example not found — cannot build .env"
  fi

  info "Building .env from .env.example …"
  cp .env.example .env

  echo ""
  echo -e "${BOLD}ShadowRealm needs at least one AI provider key to work.${RESET}"
  echo -e "Press ENTER to skip any key you don't have yet (you can edit .env later).\n"

  prompt_key() {
    local LABEL="$1" VAR="$2"
    read -r -p "$(echo -e "  ${CYAN}$LABEL${RESET}: ")" VAL
    if [[ -n "$VAL" ]]; then
      # Replace the placeholder line in .env
      if grep -q "^${VAR}=" .env; then
        sed -i.bak "s|^${VAR}=.*|${VAR}=${VAL}|" .env && rm -f .env.bak
      else
        echo "${VAR}=${VAL}" >> .env
      fi
      ok "$VAR set"
    else
      warn "$VAR skipped — set it in .env before using that provider"
    fi
  }

  prompt_key "OpenAI API key     (sk-...)"         OPENAI_API_KEY
  prompt_key "Anthropic API key  (sk-ant-...)"     ANTHROPIC_API_KEY
  prompt_key "Google API key     (for Gemini)"     GOOGLE_API_KEY
  prompt_key "Groq API key"                        GROQ_API_KEY
  prompt_key "SearXNG URL        (leave blank for default)" SEARXNG_URL

  echo ""
  ok ".env written — edit it any time to add more keys"
fi

# ---------------------------------------------------------------------------
# Node / npm check (optional — for any JS tooling)
# ---------------------------------------------------------------------------
if command -v node &>/dev/null; then
  NODE_VER=$(node --version)
  ok "Node.js $NODE_VER found"
  if [[ -f package.json ]] && [[ ! -d node_modules ]]; then
    info "Installing npm packages …"
    npm install --silent
    ok "npm packages installed"
  fi
else
  warn "Node.js not found — JS tooling unavailable (not required for core features)"
fi

# ---------------------------------------------------------------------------
# Docker path
# ---------------------------------------------------------------------------
if [[ "$DOCKER" == true ]]; then
  header "Docker launch"
  if ! command -v docker &>/dev/null; then
    error "docker not found. Install Docker Desktop and re-run with --docker."
  fi
  if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    error "docker-compose not found."
  fi
  ok "Docker available"
  if [[ "$LAUNCH" == true ]]; then
    info "Starting ShadowRealm via docker-compose …"
    if docker compose version &>/dev/null 2>&1; then
      docker compose up --build
    else
      docker-compose up --build
    fi
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Native launch
# ---------------------------------------------------------------------------
if [[ "$LAUNCH" == true ]]; then
  header "Launching ShadowRealm"
  if [[ -f launcher.py ]]; then
    info "Starting via launcher.py …"
    exec "$PYTHON" launcher.py
  elif [[ -f app.py ]]; then
    info "Starting via app.py …"
    exec "$PYTHON" app.py
  else
    error "Neither launcher.py nor app.py found — cannot start."
  fi
else
  header "Setup complete"
  echo -e "${GREEN}${BOLD}ShadowRealm is ready.${RESET}"
  echo -e "To start:  ${CYAN}source $ACTIVATE && python launcher.py${RESET}"
  echo -e "Docker:    ${CYAN}bash setup.sh --docker${RESET}"
fi
