#!/usr/bin/env bash
#
# logs.sh — View all pipeline system logs in real-time.
#
# Usage:
#   ./scripts/logs.sh              # All logs (dashboard + events + supabase)
#   ./scripts/logs.sh events       # Only pipeline events
#   ./scripts/logs.sh errors       # Only error events
#   ./scripts/logs.sh dashboard    # Only dashboard server logs
#   ./scripts/logs.sh supabase     # Supabase container logs
#   ./scripts/logs.sh llm          # Only LLM call events (prompts + responses)
#   ./scripts/logs.sh run <run_id> # All events for a specific run
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOWS_DIR="$PROJECT_ROOT/workflows"
LOG_DIR="$WORKFLOWS_DIR/logs"
EVENT_LOG="$WORKFLOWS_DIR/artifacts/pipeline_events.jsonl"
DASHBOARD_LOG="$LOG_DIR/dashboard.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

MODE="${1:-all}"
RUN_ID="${2:-}"

# Ensure directories exist
mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$EVENT_LOG")"

case "$MODE" in

  # ── All logs ────────────────────────────────────────
  all)
    echo -e "${BOLD}${CYAN}Pipeline Logs (all)${RESET}"
    echo -e "${DIM}Dashboard: $DASHBOARD_LOG${RESET}"
    echo -e "${DIM}Events:    $EVENT_LOG${RESET}"
    echo -e "${DIM}Ctrl+C to stop${RESET}"
    echo ""

    # Create files if they don't exist (tail -f needs them)
    touch "$DASHBOARD_LOG" "$EVENT_LOG"

    # Tail both files, label each line
    tail -f "$DASHBOARD_LOG" "$EVENT_LOG" 2>/dev/null | while IFS= read -r line; do
      if echo "$line" | grep -q '"event_type"'; then
        # Event log line — colorize by type
        TYPE=$(echo "$line" | grep -o '"event_type":"[^"]*"' | cut -d'"' -f4)
        AGENT=$(echo "$line" | grep -o '"agent_id":"[^"]*"' | cut -d'"' -f4)
        case "$TYPE" in
          *error*|*fail*)  echo -e "${RED}[EVENT]${RESET} ${TYPE} ${AGENT} ${DIM}${line:0:200}${RESET}" ;;
          *complete*)      echo -e "${GREEN}[EVENT]${RESET} ${TYPE} ${AGENT}" ;;
          *start*)         echo -e "${CYAN}[EVENT]${RESET} ${TYPE} ${AGENT}" ;;
          *)               echo -e "${DIM}[EVENT] ${TYPE} ${AGENT}${RESET}" ;;
        esac
      elif echo "$line" | grep -q "==> "; then
        # File header from tail -f
        echo -e "${BOLD}${line}${RESET}"
      else
        # Dashboard log line
        if echo "$line" | grep -qi "error\|fail\|exception\|traceback"; then
          echo -e "${RED}[DASH]${RESET} $line"
        elif echo "$line" | grep -qi "warning\|warn"; then
          echo -e "${YELLOW}[DASH]${RESET} $line"
        else
          echo -e "${DIM}[DASH]${RESET} $line"
        fi
      fi
    done
    ;;

  # ── Events only ─────────────────────────────────────
  events)
    echo -e "${BOLD}${CYAN}Pipeline Events${RESET}"
    echo -e "${DIM}File: $EVENT_LOG${RESET}"
    echo ""

    if [ ! -f "$EVENT_LOG" ]; then
      echo -e "${YELLOW}No event log found. Run a pipeline first.${RESET}"
      exit 0
    fi

    # Pretty-print existing events, then tail for new ones
    python3 -c "
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        evt = json.loads(line)
        t = evt.get('event_type', '?')
        a = evt.get('agent_id', '')
        ts = evt.get('timestamp', '')[-12:-1] if evt.get('timestamp') else ''
        dur = f\" {evt.get('duration_ms')}ms\" if evt.get('duration_ms') else ''
        err = evt.get('data', {}).get('error', '')
        if err:
            print(f'  \033[0;31m{ts} [{t}] {a}{dur} — {err[:100]}\033[0m')
        elif 'complete' in t:
            print(f'  \033[0;32m{ts} [{t}] {a}{dur}\033[0m')
        elif 'start' in t:
            print(f'  \033[0;36m{ts} [{t}] {a}{dur}\033[0m')
        else:
            print(f'  \033[2m{ts} [{t}] {a}{dur}\033[0m')
    except json.JSONDecodeError:
        print(f'  {line[:120]}')
" < <(tail -f "$EVENT_LOG" 2>/dev/null)
    ;;

  # ── Errors only ─────────────────────────────────────
  errors)
    echo -e "${BOLD}${RED}Pipeline Errors${RESET}"
    echo ""

    if [ ! -f "$EVENT_LOG" ]; then
      echo -e "${YELLOW}No event log found. Run a pipeline first.${RESET}"
      exit 0
    fi

    # Show existing errors
    EXISTING=$(grep -c '"error"' "$EVENT_LOG" 2>/dev/null || echo "0")
    echo -e "${DIM}Found $EXISTING error events in log${RESET}"
    echo ""

    python3 -c "
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        evt = json.loads(line)
        t = evt.get('event_type', '')
        if 'error' not in t: continue
        a = evt.get('agent_id', '')
        ts = evt.get('timestamp', '')[-12:-1] if evt.get('timestamp') else ''
        d = evt.get('data', {})
        err = d.get('error', '')
        err_type = d.get('error_type', '')
        step = evt.get('step_number', '')

        print(f'\033[0;31m{ts} [{t}] {a} #{step}\033[0m')
        if err_type: print(f'  Type: {err_type}')
        if err: print(f'  Error: {err[:200]}')
        extra = {k:v for k,v in d.items() if k not in ('error','error_type')}
        if extra: print(f'  Data: {json.dumps(extra, default=str)[:200]}')
        print()
    except json.JSONDecodeError:
        pass
" < <(tail -f "$EVENT_LOG" 2>/dev/null)
    ;;

  # ── Dashboard server logs ───────────────────────────
  dashboard)
    echo -e "${BOLD}${CYAN}Dashboard Server Logs${RESET}"
    echo -e "${DIM}File: $DASHBOARD_LOG${RESET}"
    echo ""

    if [ ! -f "$DASHBOARD_LOG" ]; then
      echo -e "${YELLOW}No dashboard log found. Run ./scripts/start.sh first.${RESET}"
      exit 0
    fi

    tail -f "$DASHBOARD_LOG" 2>/dev/null | while IFS= read -r line; do
      if echo "$line" | grep -qi "error\|fail\|exception\|traceback"; then
        echo -e "${RED}$line${RESET}"
      elif echo "$line" | grep -qi "warning\|warn"; then
        echo -e "${YELLOW}$line${RESET}"
      else
        echo "$line"
      fi
    done
    ;;

  # ── Supabase logs ───────────────────────────────────
  supabase)
    echo -e "${BOLD}${CYAN}Supabase Logs${RESET}"
    echo ""
    cd "$WORKFLOWS_DIR"
    supabase logs --follow 2>&1 || {
      echo -e "${YELLOW}Supabase logs not available. Is Supabase running?${RESET}"
      echo -e "${DIM}Try: cd workflows && supabase start${RESET}"
    }
    ;;

  # ── LLM calls only ─────────────────────────────────
  llm)
    echo -e "${BOLD}${CYAN}LLM Calls${RESET}"
    echo ""

    if [ ! -f "$EVENT_LOG" ]; then
      echo -e "${YELLOW}No event log found.${RESET}"
      exit 0
    fi

    python3 -c "
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        evt = json.loads(line)
        t = evt.get('event_type', '')
        if 'llm' not in t: continue
        a = evt.get('agent_id', '')
        ts = evt.get('timestamp', '')[-12:-1] if evt.get('timestamp') else ''
        d = evt.get('data', {})
        dur = evt.get('duration_ms')

        if t == 'llm.call.start':
            model = d.get('model', '?')
            exe = d.get('execution_type', '?')
            prompt_len = len(d.get('user_prompt', ''))
            sys_len = len(d.get('system_prompt', ''))
            print(f'\033[0;36m{ts} [{a}] LLM START — {exe}/{model} (sys:{sys_len}ch, user:{prompt_len}ch)\033[0m')
        elif t == 'llm.call.complete':
            usage = d.get('usage', {})
            inp = usage.get('input_tokens', 0)
            out = usage.get('output_tokens', 0)
            resp_len = len(d.get('content', ''))
            dur_str = f'{dur}ms' if dur else '?'
            print(f'\033[0;32m{ts} [{a}] LLM DONE — {inp}+{out} tokens, {resp_len}ch response, {dur_str}\033[0m')
        elif t == 'llm.call.error':
            err = d.get('error', '')
            print(f'\033[0;31m{ts} [{a}] LLM ERROR — {err[:100]}\033[0m')
        print()
    except json.JSONDecodeError:
        pass
" < <(tail -f "$EVENT_LOG" 2>/dev/null)
    ;;

  # ── Specific run ────────────────────────────────────
  run)
    if [ -z "$RUN_ID" ]; then
      echo -e "${RED}Usage: ./scripts/logs.sh run <run_id>${RESET}"
      echo ""
      echo "Recent run IDs from event log:"
      if [ -f "$EVENT_LOG" ]; then
        grep -o '"run_id":"[^"]*"' "$EVENT_LOG" | sort -u | tail -10 | while IFS= read -r line; do
          RID=$(echo "$line" | cut -d'"' -f4)
          echo "  $RID"
        done
      fi
      exit 1
    fi

    echo -e "${BOLD}${CYAN}Events for run: ${RUN_ID:0:8}...${RESET}"
    echo ""

    if [ ! -f "$EVENT_LOG" ]; then
      echo -e "${YELLOW}No event log found.${RESET}"
      exit 0
    fi

    python3 -c "
import json, sys
run_id = '$RUN_ID'
count = 0
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        evt = json.loads(line)
        if evt.get('run_id') != run_id: continue
        count += 1
        t = evt.get('event_type', '?')
        a = evt.get('agent_id', '')
        ts = evt.get('timestamp', '')[-12:-1] if evt.get('timestamp') else ''
        dur = f' {evt.get(\"duration_ms\")}ms' if evt.get('duration_ms') else ''
        d = evt.get('data', {})
        err = d.get('error', '')

        if 'error' in t:
            print(f'\033[0;31m  {count:4d} {ts} [{t}] {a}{dur}\033[0m')
            if err: print(f'         {err[:120]}')
        elif 'complete' in t:
            print(f'\033[0;32m  {count:4d} {ts} [{t}] {a}{dur}\033[0m')
        elif 'start' in t:
            print(f'\033[0;36m  {count:4d} {ts} [{t}] {a}{dur}\033[0m')
        else:
            print(f'\033[2m  {count:4d} {ts} [{t}] {a}{dur}\033[0m')
    except json.JSONDecodeError:
        pass
print(f'\n  Total: {count} events')
" < "$EVENT_LOG"
    ;;

  # ── Help ────────────────────────────────────────────
  *)
    echo -e "${BOLD}Usage:${RESET} ./scripts/logs.sh [mode] [args]"
    echo ""
    echo "Modes:"
    echo "  all        All logs — dashboard + events (default)"
    echo "  events     Pipeline events, color-coded"
    echo "  errors     Error events only, with full details"
    echo "  dashboard  Dashboard server logs"
    echo "  supabase   Supabase container logs"
    echo "  llm        LLM call events (prompts, tokens, latency)"
    echo "  run <id>   All events for a specific run ID"
    echo ""
    echo "Log locations:"
    echo "  Dashboard: $DASHBOARD_LOG"
    echo "  Events:    $EVENT_LOG"
    ;;
esac
