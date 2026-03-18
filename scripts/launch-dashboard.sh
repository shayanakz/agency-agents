#!/usr/bin/env bash
#
# Launch the Pipeline Dashboard
#
# Opens a web UI where you type an idea and watch it build in real-time.
# The pipeline runs in the background — events stream to the browser via SSE.
#
# Usage:
#   ./scripts/launch-dashboard.sh
#
# Requirements:
#   - Supabase local running (npx supabase start) OR SUPABASE_URL/KEY env vars set
#   - Claude Code CLI installed (for agent execution)
#   - Python 3.11+ with venv at workflows/.venv
#
# Environment variables (set these or use .env):
#   SUPABASE_URL          - Supabase REST URL (default: http://127.0.0.1:54321)
#   SUPABASE_SERVICE_KEY  - Supabase service role key
#   SUPABASE_ANON_KEY     - Alternative: Supabase anon key
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOWS_DIR="$PROJECT_ROOT/workflows"
VENV_DIR="$WORKFLOWS_DIR/.venv"
PORT="${DASHBOARD_PORT:-8787}"

# ── Colors ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║       Pipeline Dashboard             ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${RESET}"

# ── Check venv ──────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}Error: Python venv not found at $VENV_DIR${RESET}"
    echo "Run: cd workflows && python3 -m venv .venv && .venv/bin/pip install -r runtime/requirements.txt"
    exit 1
fi

PYTHON="$VENV_DIR/bin/python3"

if [ ! -f "$PYTHON" ]; then
    echo -e "${RED}Error: python3 not found in venv${RESET}"
    exit 1
fi

# ── Check dependencies ──────────────────────────────────
echo -e "${DIM}Checking dependencies...${RESET}"
if ! "$PYTHON" -c "import fastapi, uvicorn" 2>/dev/null; then
    echo -e "${CYAN}Installing fastapi + uvicorn...${RESET}"
    "$VENV_DIR/bin/pip" install -q "fastapi>=0.100.0" "uvicorn[standard]>=0.20.0"
fi

# ── Load .env if present ────────────────────────────────
if [ -f "$WORKFLOWS_DIR/.env" ]; then
    echo -e "${DIM}Loading $WORKFLOWS_DIR/.env${RESET}"
    set -a
    source "$WORKFLOWS_DIR/.env"
    set +a
elif [ -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${DIM}Loading $PROJECT_ROOT/.env${RESET}"
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# ── Check Supabase credentials ──────────────────────────
if [ -z "${SUPABASE_SERVICE_KEY:-}" ] && [ -z "${SUPABASE_ANON_KEY:-}" ]; then
    echo -e "${RED}Warning: No SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY set.${RESET}"
    echo -e "${DIM}The dashboard will start but API calls to Supabase will fail.${RESET}"
    echo -e "${DIM}Set credentials in .env or environment, or run: npx supabase start${RESET}"
    echo ""
fi

# ── Check claude CLI ────────────────────────────────────
if ! command -v claude &>/dev/null; then
    echo -e "${RED}Warning: 'claude' CLI not found in PATH.${RESET}"
    echo -e "${DIM}Agent execution (claude_code, claude_code_print) will fail.${RESET}"
    echo -e "${DIM}Install: npm install -g @anthropic-ai/claude-code${RESET}"
    echo ""
fi

# ── Create required directories ─────────────────────────
mkdir -p "$WORKFLOWS_DIR/projects"
mkdir -p "$WORKFLOWS_DIR/artifacts"
mkdir -p "$WORKFLOWS_DIR/state"

# ── Launch ──────────────────────────────────────────────
echo -e "${GREEN}${BOLD}Starting dashboard on http://localhost:${PORT}${RESET}"
echo -e "${DIM}Press Ctrl+C to stop.${RESET}"
echo ""

cd "$WORKFLOWS_DIR"
exec "$PYTHON" -m runtime.dashboard
