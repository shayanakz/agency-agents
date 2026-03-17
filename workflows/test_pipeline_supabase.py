#!/usr/bin/env python3
"""End-to-end pipeline test with Supabase backend.

Reads agent configs + personas from local Supabase, runs brainstorm + planning
via claude -p (haiku, Max subscription), writes run records back to Supabase.

Usage:
    cd workflows
    python3 test_pipeline_supabase.py
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Supabase REST API config (from `supabase status`)
SUPABASE_URL = "http://127.0.0.1:54321"
API_KEY = "sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH"
SERVICE_KEY = "sb_secret_N7UND0UgjKTVK-Uodkm0Hg_xSvEMPvz"
HEADERS = {
    "apikey": API_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# Repo root for loading persona .md files
REPO_ROOT = Path(__file__).parent.parent


def api_get(table: str, params: str = "") -> list | dict:
    """GET from Supabase REST API."""
    import urllib.request
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def api_post(table: str, data: dict) -> dict:
    """POST to Supabase REST API."""
    import urllib.request
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        return result[0] if isinstance(result, list) else result


def load_agent(agent_id: str) -> dict:
    result = api_get("agents", f"id=eq.{agent_id}&select=*")
    return result[0]


def load_persona(persona_id: str) -> dict:
    result = api_get("personas", f"id=eq.{persona_id}&select=*")
    return result[0]


def load_persona_markdown(source: str) -> str:
    path = REPO_ROOT / source
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"(Persona file not found: {source})"


def build_system_prompt(agent: dict, persona: dict) -> str:
    """Build the system prompt. Kept concise for haiku model."""
    parts = []
    # Layer 1: Agent role (stripped of web search references for non-tool mode)
    role = agent.get("role", "")
    role = role.replace("web search, not fabrication", "your knowledge")
    role = role.replace("Research the problem space with real data (web search, not fabrication)",
                        "Research the problem space using your knowledge")
    parts.append(f"# Your Role\n\n{role}")
    # Layer 2: Persona behavioral overlay (concise, not full .md)
    overlay = persona.get("system_prompt_overlay", "")
    traits = persona.get("personality_traits", [])
    if traits:
        parts.append(f"# Your Personality: {', '.join(traits)}")
    if overlay:
        parts.append(f"# Behavioral Rules\n\n{overlay}")
    # Layer 3: Guardrails
    guardrails = agent.get("guardrails", [])
    if guardrails:
        rules = "\n".join(f"- {g}" for g in guardrails)
        parts.append(f"# Guardrails\n\n{rules}")
    # Layer 4: Output format
    parts.append("# CRITICAL: Output Format\n\nYou have NO tools available. Do NOT ask for permissions or tools. Return ONLY a raw JSON object based on your knowledge. No markdown fences. No explanation.")
    return "\n\n---\n\n".join(parts)


def call_claude(system_prompt: str, user_prompt: str, model: str = "haiku") -> str:
    """Call Claude Code CLI in print mode with forced JSON output. Prompt via stdin."""
    full_prompt = f"{system_prompt}\n\n---\n\nTASK:\n{user_prompt}"
    cmd = [
        "claude", "-p",
        "--output-format", "text",
        "--model", model,
        "--max-turns", "1",
        "--disallowed-tools", "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Agent",
        "--append-system-prompt", "You MUST return ONLY a raw JSON object. No explanation. No asking for tools or permissions. Just output the JSON.",
    ]
    print(f"  Calling claude --model {model} ...")
    start = time.monotonic()
    result = subprocess.run(cmd, input=full_prompt, capture_output=True, text=True, timeout=120)
    elapsed = time.monotonic() - start
    print(f"  Done in {elapsed:.1f}s (exit code: {result.returncode})")
    if result.returncode != 0 and result.stderr:
        print(f"  STDERR: {result.stderr[:200]}")
    return result.stdout.strip()


def parse_json(text: str) -> dict:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {"_raw": text[:500]}


class DotDict(dict):
    """Dict that supports attribute access for gate predicate evaluation."""
    def __getattr__(self, key):
        val = self.get(key)
        if isinstance(val, dict):
            return DotDict(val)
        if isinstance(val, list):
            return [DotDict(v) if isinstance(v, dict) else v for v in val]
        return val


def to_dotdict(obj):
    if isinstance(obj, dict):
        return DotDict({k: to_dotdict(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [to_dotdict(v) for v in obj]
    return obj


def eval_gate(predicate: str, context: dict) -> bool:
    """Evaluate a gate predicate, converting dicts to dot-accessible objects."""
    safe_context = {k: to_dotdict(v) for k, v in context.items()}
    safe_globals = {"__builtins__": {}, "len": len, "all": all, "any": any, "True": True, "False": False}
    safe_globals.update(safe_context)
    return bool(eval(predicate, safe_globals))


def safe_api_post(table: str, data: dict) -> dict | None:
    """POST with error handling. Strips None values to avoid FK errors."""
    clean = {k: v for k, v in data.items() if v is not None}
    try:
        return api_post(table, clean)
    except Exception as e:
        print(f"  WARN: Failed to write to {table}: {e}")
        return None


def main():
    print("=" * 60)
    print("  IDEA-TO-PRODUCTION PIPELINE TEST (Supabase-backed)")
    print("  Model: haiku | Backend: local Supabase | Max subscription")
    print("=" * 60)

    idea = "A simple todo list web app with local storage"

    # ── Create pipeline run in Supabase ─────────────────────────
    run_record = api_post("pipeline_runs", {
        "workflow_id": "idea-to-production",
        "project_name": "TodoApp-Test",
        "ideas": [idea],
        "mode": "supervised",
        "status": "running",
        "current_stage": "brainstorm",
    })
    run_id = run_record["id"]
    print(f"\n  Run ID: {run_id}")

    # ── Stage 1: Brainstorm ─────────────────────────────────────
    print("\n[STAGE 1] BRAINSTORM AGENT (config from Supabase)")
    print("-" * 40)

    agent = load_agent("brainstorm_agent")
    persona = load_persona(agent["persona_id"])
    print(f"  Agent: {agent['name']}")
    print(f"  Persona: {persona['name']} ({persona['source']})")
    print(f"  Model config: {agent['model_config']}")
    print(f"  Decision biases: {[b['id'] for b in persona.get('decision_biases', [])]}")

    system_prompt = build_system_prompt(agent, persona)
    user_prompt = (
        f"Research and validate this idea: {idea}\n\n"
        "Return a JSON object with keys: research_brief, validated_idea.\n"
        "research_brief needs: problem_statement, target_audience, competitive_landscape (3+ competitors), "
        "feature_list (with core array), technical_recommendations, risks, confidence (0.0-1.0).\n"
        "validated_idea needs: idea, confidence, differentiators."
    )

    start_time = time.monotonic()
    brainstorm_result = call_claude(system_prompt, user_prompt, model="haiku")
    brainstorm_ms = int((time.monotonic() - start_time) * 1000)
    print(f"  Raw result (first 300 chars): {brainstorm_result[:300]}")
    brainstorm_parsed = parse_json(brainstorm_result)
    print(f"  Parsed keys: {list(brainstorm_parsed.keys())[:10]}")

    # Evaluate gates (from agent config in DB)
    gates = agent.get("gates", [])
    brief = brainstorm_parsed.get("research_brief", brainstorm_parsed)
    validated = brainstorm_parsed.get("validated_idea", {})
    gate_context = {"research_brief": brief, "validated_idea": validated}

    gate_results = {}
    for gate in gates:
        try:
            result = eval_gate(gate["predicate"], gate_context)
            gate_results[gate["id"]] = bool(result)
        except Exception as e:
            gate_results[gate["id"]] = False
            print(f"  Gate eval error ({gate['id']}): {e}")

    all_passed = all(gate_results.values())
    print(f"  Gate results: {gate_results}")
    print(f"  All gates passed: {'YES' if all_passed else 'NO'}")

    # Write run_step to Supabase
    step1 = safe_api_post("run_steps", {
        "run_id": run_id,
        "agent_id": "brainstorm_agent",
        "persona_id": persona["id"],
        "step_number": 1,
        "attempt": 1,
        "status": "passed" if all_passed else "failed",
        "inputs": {"raw_idea": idea},
        "outputs": brainstorm_parsed,
        "gate_results": gate_results,
        "duration_ms": brainstorm_ms,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })

    # Write LLM audit log
    safe_api_post("llm_audit_log", {
        "run_id": run_id,
        "step_id": step1["id"] if step1 else None,
        "agent_id": "brainstorm_agent",
        "execution_type": "claude_code_print",
        "provider": "claude_code_print",
        "model": "haiku",
        "temperature": 0.0,
        "prompt_summary": user_prompt[:200],
        "latency_ms": brainstorm_ms,
    })

    # Write handoff
    safe_api_post("handoffs", {
        "run_id": run_id,
        "step_id": step1["id"] if step1 else None,
        "from_agent_id": "brainstorm_agent",
        "to_agent_id": "planning_agent",
        "payload": brainstorm_parsed,
        "gate_results": gate_results,
    })

    print(f"  Step record: {step1['id']}")

    # ── Stage 2: Planning ───────────────────────────────────────
    print("\n[STAGE 2] PLANNING AGENT (config from Supabase)")
    print("-" * 40)

    agent2 = load_agent("planning_agent")
    persona2 = load_persona(agent2["persona_id"])
    print(f"  Agent: {agent2['name']}")
    print(f"  Persona: {persona2['name']} ({persona2['source']})")
    print(f"  Decision biases: {[b['id'] for b in persona2.get('decision_biases', [])]}")

    system_prompt2 = build_system_prompt(agent2, persona2)
    user_prompt2 = (
        f"Create a sprint backlog for this validated idea:\n\n"
        f"{json.dumps(brainstorm_parsed, indent=2)}\n\n"
        "Return a JSON object with keys: sprint_backlog, architecture.\n"
        "sprint_backlog needs: tasks (array of {id, title, description, category, acceptance_criteria, estimated_hours, dependencies}), total_estimated_hours.\n"
        "architecture needs: tech_stack, data_model, api_contracts, deployment_target.\n"
        "Keep to 3-4 tasks max. No task over 8 hours."
    )

    start_time = time.monotonic()
    planning_result = call_claude(system_prompt2, user_prompt2, model="haiku")
    planning_ms = int((time.monotonic() - start_time) * 1000)
    planning_parsed = parse_json(planning_result)

    tasks = planning_parsed.get("sprint_backlog", {}).get("tasks", [])

    # Evaluate gates
    gates2 = agent2.get("gates", [])
    gate_context2 = {"sprint_backlog": planning_parsed.get("sprint_backlog", {}), "schema_contract": planning_parsed.get("schema_contract", {"mapping_functions": []})}

    gate_results2 = {}
    for gate in gates2:
        try:
            result = eval_gate(gate["predicate"], gate_context2)
            gate_results2[gate["id"]] = bool(result)
        except Exception as e:
            gate_results2[gate["id"]] = False
            print(f"  Gate eval error ({gate['id']}): {e}")

    all_passed2 = all(gate_results2.values())

    print(f"  Tasks created: {len(tasks)}")
    for t in tasks:
        print(f"    - [{t.get('category', '?')}] {t.get('title', '?')} ({t.get('estimated_hours', '?')}h)")
    print(f"  Gate results: {gate_results2}")
    print(f"  All gates passed: {'YES' if all_passed2 else 'NO'}")

    # Write run_step
    step2 = safe_api_post("run_steps", {
        "run_id": run_id,
        "agent_id": "planning_agent",
        "persona_id": persona2["id"],
        "step_number": 2,
        "attempt": 1,
        "status": "passed" if all_passed2 else "failed",
        "inputs": brainstorm_parsed,
        "outputs": planning_parsed,
        "gate_results": gate_results2,
        "duration_ms": planning_ms,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })

    safe_api_post("llm_audit_log", {
        "run_id": run_id,
        "step_id": step2["id"] if step2 else None,
        "agent_id": "planning_agent",
        "execution_type": "claude_code_print",
        "provider": "claude_code_print",
        "model": "haiku",
        "temperature": 0.0,
        "prompt_summary": user_prompt2[:200],
        "latency_ms": planning_ms,
    })

    safe_api_post("handoffs", {
        "run_id": run_id,
        "step_id": step2["id"] if step2 else None,
        "from_agent_id": "planning_agent",
        "to_agent_id": "schema_verify_agent",
        "payload": planning_parsed,
        "gate_results": gate_results2,
    })

    # ── Update pipeline run status ──────────────────────────────
    import urllib.request
    url = f"{SUPABASE_URL}/rest/v1/pipeline_runs?id=eq.{run_id}"
    body = json.dumps({"status": "complete", "current_stage": "planning"}).encode()
    req = urllib.request.Request(url, data=body, headers={**HEADERS, "Prefer": "return=minimal"}, method="PATCH")
    urllib.request.urlopen(req)

    # ── Summary ─────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  PIPELINE TEST COMPLETE (Supabase-backed)")
    print("=" * 60)
    print(f"  Run ID: {run_id}")
    print(f"  Stages: brainstorm → planning")
    print(f"  Model: haiku (via claude -p)")
    print(f"  Brainstorm: {'PASSED' if all_passed else 'PARTIAL'} ({brainstorm_ms}ms)")
    print(f"  Planning: {'PASSED' if all_passed2 else 'PARTIAL'} ({planning_ms}ms)")
    print(f"\n  Data written to Supabase:")
    print(f"    - 1 pipeline_run record")
    print(f"    - 2 run_step records")
    print(f"    - 2 llm_audit_log records")
    print(f"    - 2 handoff records")
    print(f"\n  View in Studio: http://127.0.0.1:54323")
    print(f"    → Table: pipeline_runs, run_steps, llm_audit_log, handoffs")


if __name__ == "__main__":
    main()
