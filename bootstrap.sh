#!/usr/bin/env bash
# bootstrap.sh — One-command setup for bird_brain (Linux)
#
# Usage (fresh install):
#   curl -sSL https://raw.githubusercontent.com/kleer001/bird_brain/main/bootstrap.sh | bash
#
# Usage (re-run from inside repo):
#   bash bootstrap.sh
#
# Idempotent: safe to re-run. Never prompts, so it works piped from curl.
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}   $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
warn() { echo -e "${YELLOW}[!!]${NC}   $1"; }
info() { echo -e "${CYAN}[..]${NC}   $1"; }

echo -e "\n${BOLD}=== bird_brain bootstrap ===${NC}\n"

# -------------------------------------------------------
# Step 1: Prerequisites
# -------------------------------------------------------
echo -e "${BOLD}Step 1: Checking prerequisites${NC}"

# Linux only, and not incidentally: the whole design rests on PipeWire exposing a
# .monitor source for the far side of the call. macOS and Windows have no
# equivalent that works without a virtual audio device.
OS="$(uname -s)"
if [ "$OS" != "Linux" ]; then
    fail "bird_brain is Linux-only (detected: $OS)."
    echo "  It captures the far side of a call from PipeWire's .monitor source,"
    echo "  which has no equivalent on $OS without a virtual audio device."
    exit 1
fi
ok "Linux detected"

if command -v git &>/dev/null; then
    ok "git found: $(git --version | cut -d' ' -f3)"
else
    fail "git is not installed."
    exit 1
fi

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver="$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
        if [ -n "$ver" ]; then
            major="${ver%%.*}"; minor="${ver##*.}"
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then PYTHON="$cmd"; break; fi
        fi
    fi
done
if [ -n "$PYTHON" ]; then
    ok "Python found: $($PYTHON --version | cut -d' ' -f2)"
else
    fail "Python 3.10+ is required but was not found."
    exit 1
fi

# Audio tooling. pactl/parec are required — without them there is no capture at
# all. sox and pw-play are only used by the preflight, so they are advisory.
missing_required=()
for t in pactl parec; do command -v "$t" &>/dev/null || missing_required+=("$t"); done
if [ ${#missing_required[@]} -gt 0 ]; then
    fail "Missing audio tools: ${missing_required[*]}"
    echo "  These ship separately from PipeWire itself. Install with:"
    echo -e "    ${BOLD}sudo apt install -y pulseaudio-utils${NC}"
    exit 1
fi
ok "Audio capture tools present (pactl, parec)"

missing_optional=()
for t in sox pw-play; do command -v "$t" &>/dev/null || missing_optional+=("$t"); done
if [ ${#missing_optional[@]} -gt 0 ]; then
    warn "Missing preflight tools: ${missing_optional[*]}"
    echo "    sudo apt install -y sox pipewire-bin      # needed by ./run.sh --check"
fi

if pactl info 2>/dev/null | grep -qi pipewire; then
    ok "PipeWire is the running sound server"
else
    warn "Sound server does not report as PipeWire — capture may still work, but"
    echo "    the .monitor source is what this depends on. See docs/INSTALL.md §1."
fi

# -------------------------------------------------------
# Step 2: Repository
# -------------------------------------------------------
echo -e "\n${BOLD}Step 2: Repository${NC}"

if [ -f "run.sh" ] && [ -d "src" ] && [ -f "requirements.txt" ]; then
    ok "Already inside the bird_brain repo — skipping clone"
else
    info "Cloning bird_brain..."
    git clone https://github.com/kleer001/bird_brain.git
    cd bird_brain
    ok "Cloned into $(pwd)"
fi
REPO_DIR="$(pwd)"

# -------------------------------------------------------
# Step 3: Python environment
# -------------------------------------------------------
echo -e "\n${BOLD}Step 3: Python environment${NC}"

if [ ! -d ".venv" ]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv .venv
fi
ok "Virtual environment: .venv/"

info "Installing dependencies (this pulls faster-whisper and torch — a few minutes)..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
ok "Dependencies installed"

if .venv/bin/python -c "import ctranslate2, sys; sys.exit(0 if ctranslate2.get_cuda_device_count() else 1)" 2>/dev/null; then
    ok "CUDA device visible — local transcription will run on the GPU"
else
    warn "No CUDA device visible — transcription will run on CPU"
    echo "    Workable: a 5 s window decodes in about 1 s on CPU, still ahead of real time."
fi

# -------------------------------------------------------
# Step 4: Claude Code CLI
# -------------------------------------------------------
echo -e "\n${BOLD}Step 4: Claude Code${NC}"

# Both lanes drive the Claude Code CLI through the Agent SDK, so this is the
# credential for the whole app — there is no API key path to fall back on.
if command -v claude &>/dev/null; then
    ok "claude CLI found"
    if claude -p 'say ok' &>/dev/null; then
        ok "claude is authenticated"
    else
        warn "claude is installed but not logged in"
        echo -e "    Run: ${BOLD}claude login${NC}"
    fi
else
    warn "claude CLI not found — both lanes need it"
    if command -v npm &>/dev/null; then
        echo -e "    Run: ${BOLD}npm install -g @anthropic-ai/claude-code && claude login${NC}"
    else
        echo "    Install Node.js first, then:"
        echo -e "    ${BOLD}npm install -g @anthropic-ai/claude-code && claude login${NC}"
    fi
fi

# -------------------------------------------------------
# Step 5: Configuration
# -------------------------------------------------------
echo -e "\n${BOLD}Step 5: Configuration${NC}"

if [ -f ".env" ]; then
    ok ".env already exists — leaving it alone"
else
    cp .env.example .env
    ok "Created .env from .env.example"
fi

FIFO="$(grep -E '^BIRD_BRAIN_FIFO=' .env | cut -d= -f2- || true)"
FIFO="${FIFO:-/tmp/bird_brain.fifo}"
if [ -p "$FIFO" ]; then
    ok "Trigger FIFO exists: $FIFO"
else
    mkfifo "$FIFO" && ok "Created trigger FIFO: $FIFO"
fi

# -------------------------------------------------------
# Done
# -------------------------------------------------------
echo -e "\n${BOLD}=== Setup complete ===${NC}\n"
echo -e "  ${BOLD}cd $REPO_DIR${NC}"
echo
echo -e "  ${BOLD}./run.sh --check${NC}   preflight — devices, signal levels, transcription, both lanes"
echo -e "  ${BOLD}./run.sh${NC}           start listening"
echo
echo "  Two things bootstrap cannot do for you:"
echo "    1. Bind the hotkeys — desktop-specific, see docs/INSTALL.md §4"
echo "    2. Point BIRD_BRAIN_RESUME at your own background file, if you want one"
echo
