#!/usr/bin/env bash
#
# start.sh — One command to run the entire pipeline system.
#
# Checks every dependency, starts Supabase, applies migrations,
# seeds agents, starts the dashboard, and opens the browser.
#
# Usage:
#   ./scripts/start.sh              # Full startup
#   ./scripts/start.sh --skip-db    # Skip Supabase (already running)
#   ./scripts/start.sh --reset-db   # Reset DB (drop + recreate + reseed)
#   ./scripts/start.sh --clean      # Also delete old checkpoints and artifacts
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOWS_DIR="$PROJECT_ROOT/workflows"
SUPABASE_DIR="$WORKFLOWS_DIR/supabase"
VENV_DIR="$WORKFLOWS_DIR/.venv"
LOG_DIR="$WORKFLOWS_DIR/logs"
PORT="${DASHBOARD_PORT:-8787}"

SKIP_DB=false
RESET_DB=false
CLEAN=false

for arg in "$@"; do
  case "$arg" in
    --skip-db)  SKIP_DB=true ;;
    --reset-db) RESET_DB=true ;;
    --clean)    CLEAN=true ;;
  esac
done

# ── Colors ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

pass() { echo -e "  ${GREEN}[OK]${RESET} $1"; }
fail() { echo -e "  ${RED}[FAIL]${RESET} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${RESET} $1"; }
info() { echo -e "  ${DIM}$1${RESET}"; }

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║   Agency Agents — Pipeline System Startup        ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""

# ═══════════════════════════════════════════════════════
# PHASE 1: Dependency Checks
# ═══════════════════════════════════════════════════════
echo -e "${BOLD}Phase 1: Checking dependencies${RESET}"
echo -e "${DIM}─────────────────────────────────────────────────${RESET}"

ERRORS=0

# 1.1 Python
if command -v python3 &>/dev/null; then
  PY_VERSION=$(python3 --version 2>&1)
  pass "Python: $PY_VERSION"
else
  fail "Python 3 not found"
  ERRORS=$((ERRORS + 1))
fi

# 1.2 Node.js
if command -v node &>/dev/null; then
  NODE_VERSION=$(node --version 2>&1)
  pass "Node.js: $NODE_VERSION"
else
  fail "Node.js not found (needed for Supabase CLI)"
  ERRORS=$((ERRORS + 1))
fi

# 1.3 Supabase CLI
if command -v supabase &>/dev/null; then
  SB_VERSION=$(supabase --version 2>&1)
  pass "Supabase CLI: v$SB_VERSION"
elif npx supabase --version &>/dev/null 2>&1; then
  SB_VERSION=$(npx supabase --version 2>&1)
  pass "Supabase CLI (npx): v$SB_VERSION"
else
  fail "Supabase CLI not found"
  info "Install: brew install supabase/tap/supabase"
  ERRORS=$((ERRORS + 1))
fi

# 1.4 Docker (required for local Supabase)
if command -v docker &>/dev/null; then
  if docker info &>/dev/null 2>&1; then
    pass "Docker: running"
  else
    fail "Docker installed but not running"
    info "Start Docker Desktop first"
    ERRORS=$((ERRORS + 1))
  fi
else
  fail "Docker not found (required for local Supabase)"
  ERRORS=$((ERRORS + 1))
fi

# 1.5 Claude CLI
if command -v claude &>/dev/null; then
  pass "Claude CLI: $(which claude)"
else
  warn "Claude CLI not found — agent execution will fail"
  info "Install: npm install -g @anthropic-ai/claude-code"
fi

# 1.6 Python venv
if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python3" ]; then
  pass "Python venv: $VENV_DIR"
else
  warn "Python venv not found — will create it"
fi

# 1.7 Supabase config
if [ -f "$SUPABASE_DIR/config.toml" ]; then
  pass "Supabase config: $SUPABASE_DIR/config.toml"
else
  fail "Supabase config.toml not found"
  ERRORS=$((ERRORS + 1))
fi

echo ""
if [ "$ERRORS" -gt 0 ]; then
  echo -e "${RED}${BOLD}$ERRORS critical dependency missing. Fix these first.${RESET}"
  exit 1
fi

# ═══════════════════════════════════════════════════════
# PHASE 2: Setup Python Environment
# ═══════════════════════════════════════════════════════
echo -e "${BOLD}Phase 2: Python environment${RESET}"
echo -e "${DIM}─────────────────────────────────────────────────${RESET}"

if [ ! -d "$VENV_DIR" ]; then
  info "Creating virtual environment..."
  python3 -m venv "$VENV_DIR"
  pass "Created venv at $VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python3"
PIP="$VENV_DIR/bin/pip"

# Install/upgrade dependencies
info "Checking Python packages..."
"$PIP" install -q -r "$WORKFLOWS_DIR/runtime/requirements.txt" 2>&1 | tail -1 || true

# Verify critical imports
if "$PYTHON" -c "import fastapi, uvicorn, langgraph, rich, pydantic" 2>/dev/null; then
  pass "All Python packages installed"
else
  fail "Missing Python packages"
  "$PIP" install -r "$WORKFLOWS_DIR/runtime/requirements.txt"
fi

echo ""

# ═══════════════════════════════════════════════════════
# PHASE 3: Clean up (optional)
# ═══════════════════════════════════════════════════════
if [ "$CLEAN" = true ]; then
  echo -e "${BOLD}Phase 3: Cleanup${RESET}"
  echo -e "${DIM}─────────────────────────────────────────────────${RESET}"

  # Clean old checkpoints (they can grow to 50GB+)
  for f in "$WORKFLOWS_DIR/state/"*.db "$WORKFLOWS_DIR/state/"*.db-shm "$WORKFLOWS_DIR/state/"*.db-wal; do
    if [ -f "$f" ]; then
      SIZE=$(du -sh "$f" 2>/dev/null | cut -f1)
      rm -f "$f"
      info "Deleted $f ($SIZE)"
    fi
  done
  pass "Cleaned checkpoint files"

  # Clean old event logs
  if [ -f "$WORKFLOWS_DIR/artifacts/pipeline_events.jsonl" ]; then
    rm -f "$WORKFLOWS_DIR/artifacts/pipeline_events.jsonl"
    info "Deleted old event log"
  fi

  echo ""
fi

# ═══════════════════════════════════════════════════════
# PHASE 4: Start Supabase
# ═══════════════════════════════════════════════════════
if [ "$SKIP_DB" = false ]; then
  echo -e "${BOLD}Phase 4: Supabase${RESET}"
  echo -e "${DIM}─────────────────────────────────────────────────${RESET}"

  cd "$WORKFLOWS_DIR"

  # Check if already running
  SB_STATUS=$(supabase status 2>&1 || true)
  if echo "$SB_STATUS" | grep -qE "is running|API URL|Project URL"; then
    pass "Supabase already running"
  else
    info "Starting Supabase (this may take 30-60s on first run)..."
    supabase start 2>&1 | while IFS= read -r line; do
      if echo "$line" | grep -qE "(Started|API URL|service_role|anon)"; then
        info "$line"
      fi
    done
    pass "Supabase started"
  fi

  # Extract credentials from supabase status output
  # Format varies by CLI version — try multiple patterns
  SB_OUTPUT=$(supabase status 2>&1)

  # v2.75+: "Project URL │ http://..." / "Secret │ sb_secret_..." / "Publishable │ sb_publishable_..."
  # Older:  "API URL: http://..." / "service_role key: eyJ..." / "anon key: eyJ..."
  SUPABASE_URL=$(echo "$SB_OUTPUT" | grep -E "Project URL|API URL" | grep -oE "http://[0-9.:]+")
  SUPABASE_SERVICE_KEY=$(echo "$SB_OUTPUT" | grep -E "Secret[^K]|service_role" | grep -oE "sb_secret_[A-Za-z0-9_-]+|eyJ[A-Za-z0-9._-]+")
  SUPABASE_ANON_KEY=$(echo "$SB_OUTPUT" | grep -E "Publishable|anon" | grep -oE "sb_publishable_[A-Za-z0-9_-]+|eyJ[A-Za-z0-9._-]+")

  if [ -n "$SUPABASE_URL" ] && [ -n "$SUPABASE_SERVICE_KEY" ]; then
    pass "Supabase URL: $SUPABASE_URL"
    pass "Service key: ${SUPABASE_SERVICE_KEY:0:20}..."

    export SUPABASE_URL
    export SUPABASE_SERVICE_KEY
    export SUPABASE_ANON_KEY
  else
    fail "Could not extract Supabase credentials"
    echo "$SB_OUTPUT"
    exit 1
  fi

  # Apply migrations
  # For local dev, `db reset` applies all migrations + seeds cleanly.
  # `db push` is for remote. We use reset on first run or when --reset-db.
  if [ "$RESET_DB" = true ]; then
    info "Resetting database (drop + recreate + reseed)..."
    supabase db reset 2>&1 | tail -5
    pass "Database reset complete"
  else
    # Check if migrations need to be applied
    info "Checking database migrations..."
    # Try to query agents — if it fails, we need to reset
    NEEDS_RESET=false
    "$PYTHON" -c "
import os, json, urllib.request
os.environ['SUPABASE_URL'] = '$SUPABASE_URL'
os.environ['SUPABASE_SERVICE_KEY'] = '$SUPABASE_SERVICE_KEY'
url = '$SUPABASE_URL/rest/v1/agents?select=id&limit=1'
req = urllib.request.Request(url, headers={
    'apikey': '$SUPABASE_SERVICE_KEY',
    'Authorization': 'Bearer $SUPABASE_SERVICE_KEY',
})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
        if len(data) == 0:
            exit(1)  # table exists but empty
except:
    exit(1)  # table doesn't exist
" 2>/dev/null || NEEDS_RESET=true

    if [ "$NEEDS_RESET" = true ]; then
      info "Database needs setup — running migrations + seed..."
      supabase db reset 2>&1 | tail -5
      pass "Database initialized"
    else
      pass "Database already set up"
    fi
  fi

  # Verify agents are seeded
  AGENT_COUNT=$("$PYTHON" -c "
import os, json, urllib.request
os.environ.setdefault('SUPABASE_URL', '$SUPABASE_URL')
os.environ.setdefault('SUPABASE_SERVICE_KEY', '$SUPABASE_SERVICE_KEY')
url = '$SUPABASE_URL/rest/v1/agents?select=id'
req = urllib.request.Request(url, headers={
    'apikey': '$SUPABASE_SERVICE_KEY',
    'Authorization': 'Bearer $SUPABASE_SERVICE_KEY',
})
with urllib.request.urlopen(req) as resp:
    agents = json.loads(resp.read())
    print(len(agents))
" 2>/dev/null || echo "0")

  if [ "$AGENT_COUNT" -gt 0 ]; then
    pass "Agents seeded: $AGENT_COUNT agents in database"
  else
    warn "No agents found — running db reset to seed..."
    supabase db reset 2>&1 | tail -3
    pass "Database seeded"
  fi

  # Write .env for future runs
  cat > "$WORKFLOWS_DIR/.env" << ENVEOF
# Auto-generated by start.sh — $(date)
SUPABASE_URL=$SUPABASE_URL
SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY
SUPABASE_ANON_KEY=$SUPABASE_ANON_KEY
ENVEOF
  pass "Credentials written to $WORKFLOWS_DIR/.env"

  echo ""
fi

# ═══════════════════════════════════════════════════════
# PHASE 5: Create directories
# ═══════════════════════════════════════════════════════
echo -e "${BOLD}Phase 5: Directories${RESET}"
echo -e "${DIM}─────────────────────────────────────────────────${RESET}"

mkdir -p "$WORKFLOWS_DIR/projects"
mkdir -p "$WORKFLOWS_DIR/artifacts"
mkdir -p "$WORKFLOWS_DIR/state"
mkdir -p "$LOG_DIR"
pass "projects/, artifacts/, state/, logs/ ready"

echo ""

# ═══════════════════════════════════════════════════════
# PHASE 6: Load env and start dashboard
# ═══════════════════════════════════════════════════════
echo -e "${BOLD}Phase 6: Starting dashboard${RESET}"
echo -e "${DIM}─────────────────────────────────────────────────${RESET}"

# Source .env if we didn't already set the vars (e.g., --skip-db path)
if [ -z "${SUPABASE_URL:-}" ]; then
  if [ -f "$WORKFLOWS_DIR/.env" ]; then
    set -a
    source "$WORKFLOWS_DIR/.env"
    set +a
    pass "Loaded credentials from .env"
  else
    # No .env and no vars — try to get from running Supabase
    SB_STATUS_CHECK=$(cd "$WORKFLOWS_DIR" && supabase status 2>&1 || true)
    if echo "$SB_STATUS_CHECK" | grep -qE "is running|Project URL"; then
      SUPABASE_URL=$(echo "$SB_STATUS_CHECK" | grep -E "Project URL|API URL" | grep -oE "http://[0-9.:]+" || true)
      SUPABASE_SERVICE_KEY=$(echo "$SB_STATUS_CHECK" | grep -E "Secret[^K]|service_role" | grep -oE "sb_secret_[A-Za-z0-9_-]+|eyJ[A-Za-z0-9._-]+" || true)
      SUPABASE_ANON_KEY=$(echo "$SB_STATUS_CHECK" | grep -E "Publishable|anon" | grep -oE "sb_publishable_[A-Za-z0-9_-]+|eyJ[A-Za-z0-9._-]+" || true)
      export SUPABASE_URL SUPABASE_SERVICE_KEY SUPABASE_ANON_KEY
      if [ -n "$SUPABASE_URL" ] && [ -n "$SUPABASE_SERVICE_KEY" ]; then
        pass "Extracted credentials from running Supabase"
        # Write .env for next time
        cat > "$WORKFLOWS_DIR/.env" << ENVEOF2
# Auto-generated by start.sh — $(date)
SUPABASE_URL=$SUPABASE_URL
SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY
SUPABASE_ANON_KEY=$SUPABASE_ANON_KEY
ENVEOF2
      fi
    else
      warn "No Supabase credentials found — dashboard will show errors"
      warn "Run without --skip-db, or set SUPABASE_URL + SUPABASE_SERVICE_KEY"
    fi
  fi
fi

cd "$WORKFLOWS_DIR"

# Final connectivity check (non-fatal)
if [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_SERVICE_KEY:-}" ]; then
  CONN_CHECK=$("$PYTHON" -c "
import os, json, urllib.request
url = os.environ['SUPABASE_URL'] + '/rest/v1/agents?select=id&limit=1'
req = urllib.request.Request(url, headers={
    'apikey': os.environ.get('SUPABASE_SERVICE_KEY', os.environ.get('SUPABASE_ANON_KEY', '')),
    'Authorization': 'Bearer ' + os.environ.get('SUPABASE_SERVICE_KEY', os.environ.get('SUPABASE_ANON_KEY', '')),
})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())
    print(f'OK ({len(data)} agents)')
" 2>&1 || echo "FAILED")

  if echo "$CONN_CHECK" | grep -q "OK"; then
    pass "Supabase connectivity: $CONN_CHECK"
  else
    warn "Supabase connectivity failed: $CONN_CHECK"
    warn "The dashboard will start but pipeline runs may fail"
  fi
else
  warn "Supabase credentials not set — skipping connectivity check"
fi

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   All systems ready                              ║${RESET}"
echo -e "${BOLD}${GREEN}╠══════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}${GREEN}║                                                  ║${RESET}"
echo -e "${BOLD}${GREEN}║   Dashboard:  http://localhost:${PORT}              ║${RESET}"
echo -e "${BOLD}${GREEN}║   Supabase:   http://localhost:54323 (Studio)    ║${RESET}"
echo -e "${BOLD}${GREEN}║   Logs:       ./scripts/logs.sh                  ║${RESET}"
echo -e "${BOLD}${GREEN}║                                                  ║${RESET}"
echo -e "${BOLD}${GREEN}║   Open dashboard, type an idea, hit Launch.      ║${RESET}"
echo -e "${BOLD}${GREEN}║                                                  ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""

# Start dashboard with logging
DASHBOARD_LOG="$LOG_DIR/dashboard.log"
EVENT_LOG="$WORKFLOWS_DIR/artifacts/pipeline_events.jsonl"

info "Dashboard log:  $DASHBOARD_LOG"
info "Event log:      $EVENT_LOG"
info "Press Ctrl+C to stop."
echo ""

# Run dashboard, tee output to log file
exec "$PYTHON" -m runtime.dashboard 2>&1 | tee -a "$DASHBOARD_LOG"
