"""Base node pattern for all agent nodes.

Every agent node follows the same 12-step execution pattern:
1. Load agent config from Supabase
2. Resolve persona (static or dynamic)
3. Load memories (project + agent)
4. Build system prompt (6 layers, including memory)
5. Build user prompt from state (including previous attempt context)
6. Execute via LLM router
7. Parse structured output
8. Evaluate gates
9. Save memories (decisions, patterns, lessons)
10. Write run_step + audit to Supabase
11. Write artifacts to disk
12. Return state update (including previous_outputs for retries)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import db
from ..gate_evaluator import all_gates_passed, evaluate_all_gates
from ..llm_router import execute_agent, parse_json_output
from .. import memory as mem
from ..persona_loader import build_system_prompt, resolve_persona
from ..state import PipelineState


def make_node(
    agent_id: str,
    prompt_builder: Callable[[dict, PipelineState], str],
    output_extractor: Callable[[dict[str, Any], PipelineState], dict[str, Any]],
) -> Callable[[PipelineState], dict]:
    """Create a LangGraph node function for a given agent.

    Args:
        agent_id: The agent's ID in the agents table.
        prompt_builder: Function that builds the user prompt from agent config and state.
        output_extractor: Function that extracts state updates from parsed LLM output.
    """

    def node_fn(state: PipelineState) -> dict:
        start_time = time.monotonic()
        step_number = state.get("step_count", 0) + 1
        output_dir = state.get("output_dir", "./artifacts")
        project_name = state.get("project_name", "unknown")

        try:
            return _execute_node(agent_id, prompt_builder, output_extractor, state,
                                 start_time, step_number, output_dir, project_name)
        except Exception as exc:
            # Safety net: never crash the pipeline, return a failed state
            return {
                "step_count": step_number,
                "messages": state.get("messages", []) + [
                    f"[{agent_id}] ERROR: {exc}"
                ],
                "previous_outputs": state.get("previous_outputs", []) + [
                    {"_error": str(exc)}
                ],
            }

    node_fn.__name__ = f"{agent_id}_node"
    return node_fn


def _execute_node(
    agent_id: str,
    prompt_builder: Callable,
    output_extractor: Callable,
    state: PipelineState,
    start_time: float,
    step_number: int,
    output_dir: str,
    project_name: str,
) -> dict:
    """Inner execution logic — separated so make_node can catch errors."""

    agent = db.load_agent(agent_id)

    # 2. Resolve persona
    persona = resolve_persona(agent, state)
    persona_id = persona.get("id", "unknown")

    # 3. Load memories (project + agent)
    project_memories = mem.load_project_memories(output_dir, project_name, limit=5)
    agent_memories = mem.load_agent_memories(output_dir, agent_id, limit=5)
    memory_context = mem.format_memories_for_prompt(project_memories, agent_memories)

    # 4. Build system prompt (6 layers, including memory)
    system_prompt = build_system_prompt(agent, persona, memory_context=memory_context)

    # 5. Build user prompt from state (including previous attempt context)
    user_prompt = prompt_builder(agent, state)
    previous_outputs = state.get("previous_outputs", [])
    if previous_outputs:
        prev_context = "\n\n".join(
            f"### Previous Attempt {i+1}\n```json\n{json.dumps(p, indent=2, default=str)[:2000]}\n```"
            for i, p in enumerate(previous_outputs[-3:])
        )
        user_prompt += f"\n\n## PREVIOUS ATTEMPTS (learn from these, don't repeat mistakes)\n{prev_context}"

    # 5.5. Check for existing session (coding agents retain context on retry)
    session_ids = state.get("session_ids", {})
    existing_session = session_ids.get(agent_id)

    # 6. Execute via LLM router (pass session_id for claude_code agents)
    raw_result = execute_agent(agent, system_prompt, user_prompt, session_id=existing_session)
    content = raw_result.get("content", "")

    # 7. Parse structured output
    parsed = parse_json_output(content)

    # 8. Evaluate gates
    gate_results = evaluate_all_gates(agent.get("gates", []), parsed)
    gates_passed = all_gates_passed(gate_results)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # 9. Save memories
    new_memories = mem.extract_memories_from_output(agent_id, parsed, gates_passed)
    for m in new_memories:
        mem.save_memory(
            output_dir=output_dir,
            memory_type=m["memory_type"],
            agent_id=agent_id,
            content=m["content"],
            summary=m["summary"],
            project_name=project_name if m["memory_type"] == "project" else None,
            tags=m.get("tags", []),
        )

    # 10. Write run_step + audit to Supabase
    run_id = state.get("run_id")
    step_record = None
    if run_id:
        step_record = db.create_step({
            "run_id": run_id,
            "agent_id": agent_id,
            "persona_id": persona_id,
            "step_number": step_number,
            "attempt": state.get("attempt_count", 0) + 1,
            "status": "passed" if gates_passed else "failed",
            "inputs": _safe_json(
                {k: state.get(k) for inp in agent.get("inputs", []) if (k := inp.get("name")) and k in state}
            ),
            "outputs": _safe_json(parsed),
            "gate_results": gate_results,
            "duration_ms": duration_ms,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        log_data = {
            "run_id": run_id,
            "agent_id": agent_id,
            "execution_type": agent.get("model_config", {}).get("execution", "llm_api"),
            "provider": raw_result.get("provider", "unknown"),
            "model": raw_result.get("model", "unknown"),
            "temperature": raw_result.get("temperature", 0.0),
            "prompt_summary": user_prompt[:500],
            "input_tokens": raw_result.get("usage", {}).get("input_tokens"),
            "output_tokens": raw_result.get("usage", {}).get("output_tokens"),
            "latency_ms": raw_result.get("latency_ms", 0),
        }
        if step_record and step_record.get("id"):
            log_data["step_id"] = step_record["id"]
        db.log_llm_call(log_data)

    # 11. Write artifacts to disk
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 12. Extract state updates and return
    state_updates = output_extractor(parsed, state)
    state_updates["step_count"] = step_number
    state_updates["messages"] = state.get("messages", []) + [
        f"[{agent_id}] "
        f"{'PASSED' if gates_passed else 'FAILED'} "
        f"(gates: {gate_results})"
    ]

    # Track previous outputs for retry context
    if not gates_passed:
        prev = state.get("previous_outputs", [])
        state_updates["previous_outputs"] = prev + [_safe_json(parsed)]
    else:
        state_updates["previous_outputs"] = []

    # Track session IDs for coding agents (claude_code execution type)
    returned_session_id = raw_result.get("session_id")
    if returned_session_id:
        updated_sessions = dict(session_ids)
        updated_sessions[agent_id] = returned_session_id
        state_updates["session_ids"] = updated_sessions

    return state_updates

    node_fn.__name__ = f"{agent_id}_node"
    return node_fn


def _safe_json(obj: Any) -> Any:
    """Ensure an object is JSON-serializable for Supabase."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def build_inputs_summary(agent: dict, state: PipelineState) -> str:
    """Build a summary of the agent's inputs from state for the user prompt."""
    parts: list[str] = []
    for inp in agent.get("inputs", []):
        name = inp.get("name", "")
        value = state.get(name)
        if value is not None:
            if isinstance(value, (dict, list)):
                parts.append(f"## {name}\n```json\n{json.dumps(value, indent=2)}\n```")
            else:
                parts.append(f"## {name}\n{value}")
    return "\n\n".join(parts)
