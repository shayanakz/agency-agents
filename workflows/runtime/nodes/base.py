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
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import db
from ..events import EventType, PipelineEvent, get_cancel_flag, get_event_bus
from ..gate_evaluator import GateEvaluationError, all_gates_passed, evaluate_gate
from ..llm_router import execute_agent, parse_json_output
from .. import memory as mem
from ..persona_loader import build_system_prompt, resolve_persona
from ..state import PipelineState

logger = logging.getLogger("pipeline.nodes")


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
        run_id = state.get("run_id")
        bus = get_event_bus()

        # Check for user cancellation before executing
        cancel_flag = get_cancel_flag(run_id) if run_id else None
        if cancel_flag and cancel_flag.is_set():
            raise RuntimeError("Pipeline cancelled by user")

        bus.emit(PipelineEvent(
            event_type=EventType.NODE_START,
            run_id=run_id,
            agent_id=agent_id,
            step_number=step_number,
            data={"output_dir": output_dir, "project_name": project_name},
        ))

        try:
            result = _execute_node(
                agent_id, prompt_builder, output_extractor, state,
                start_time, step_number, output_dir, project_name,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            bus.emit(PipelineEvent(
                event_type=EventType.NODE_COMPLETE,
                run_id=run_id,
                agent_id=agent_id,
                step_number=step_number,
                duration_ms=duration_ms,
                data={
                    "gates_passed": result.get("_gates_passed"),
                    "messages": result.get("messages", [])[-1:],
                },
            ))
            # Strip internal-only keys before returning to LangGraph
            result.pop("_gates_passed", None)
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            bus.emit(PipelineEvent(
                event_type=EventType.NODE_ERROR,
                run_id=run_id,
                agent_id=agent_id,
                step_number=step_number,
                duration_ms=duration_ms,
                data={"error": str(exc), "error_type": type(exc).__name__},
            ))
            logger.exception("[%s] Node execution failed", agent_id)
            return {
                "step_count": step_number,
                "messages": state.get("messages", []) + [
                    f"[{agent_id}] ERROR: {exc}"
                ],
                "previous_outputs": state.get("previous_outputs", []) + [
                    {"_error": str(exc), "_error_type": type(exc).__name__}
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

    run_id = state.get("run_id")
    bus = get_event_bus()

    agent = db.load_agent(agent_id)

    # 1.5. Apply model_config overrides from state (UI/CLI)
    model_overrides = state.get("model_overrides") or {}
    agent_override = model_overrides.get(agent_id, {})
    global_override = model_overrides.get("__all__", {})
    if global_override or agent_override:
        base_config = dict(agent.get("model_config", {}))
        # Global first, then per-agent (per-agent wins)
        base_config.update(global_override)
        base_config.update(agent_override)
        agent = dict(agent)
        agent["model_config"] = base_config

    # 2. Resolve persona
    persona = resolve_persona(agent, state)
    persona_id = persona.get("id", "unknown")

    # 3. Load memories (project + agent)
    project_memories = _safe_load_memories(
        mem.load_project_memories, output_dir, project_name, run_id, agent_id, limit=5,
    )
    agent_memories = _safe_load_memories(
        mem.load_agent_memories, output_dir, agent_id, run_id, agent_id, limit=5,
    )
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

    # 6. Execute via LLM router
    # Emit LLM_CALL_START with FULL prompts for accountability
    bus.emit(PipelineEvent(
        event_type=EventType.LLM_CALL_START,
        run_id=run_id,
        agent_id=agent_id,
        step_number=step_number,
        data={
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "session_id": existing_session,
            "execution_type": agent.get("model_config", {}).get("execution", "llm_api"),
            "model": agent.get("model_config", {}).get("model", "unknown"),
        },
    ))

    project_dir = state.get("project_dir")
    llm_start = time.monotonic()
    raw_result = execute_agent(
        agent, system_prompt, user_prompt,
        session_id=existing_session,
        working_dir=project_dir,
    )
    llm_duration_ms = int((time.monotonic() - llm_start) * 1000)

    content = raw_result.get("content", "")
    mcp_unavailable = raw_result.get("mcp_unavailable", [])

    # Emit LLM_CALL_COMPLETE with FULL response for audit trail
    bus.emit(PipelineEvent(
        event_type=EventType.LLM_CALL_COMPLETE,
        run_id=run_id,
        agent_id=agent_id,
        step_number=step_number,
        duration_ms=llm_duration_ms,
        data={
            "content": content,
            "provider": raw_result.get("provider", "unknown"),
            "model": raw_result.get("model", "unknown"),
            "usage": raw_result.get("usage", {}),
            "session_id": raw_result.get("session_id"),
            "mcp_unavailable": mcp_unavailable,
        },
    ))

    # 7. Parse structured output
    parsed = parse_json_output(content)

    # Check for parse errors — emit event and tag the output
    if parsed.get("_parse_error"):
        bus.emit(PipelineEvent(
            event_type=EventType.JSON_PARSE_ERROR,
            run_id=run_id,
            agent_id=agent_id,
            step_number=step_number,
            data={
                "raw_content_preview": content[:1000],
                "raw_content_length": len(content),
            },
        ))
        logger.warning(
            "[%s] JSON parse failed — raw content length: %d, preview: %s",
            agent_id, len(content), content[:200],
        )

    if mcp_unavailable:
        parsed["_mcp_unavailable"] = mcp_unavailable

    # 8. Evaluate gates (with audit logging)
    gate_results = _evaluate_gates_with_audit(
        agent.get("gates", []), parsed, run_id, agent_id, step_number,
    )
    gates_passed = all_gates_passed(gate_results)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # 9. Save memories (with error events instead of silent swallowing)
    new_memories = mem.extract_memories_from_output(agent_id, parsed, gates_passed)
    for m in new_memories:
        _safe_save_memory(
            output_dir=output_dir,
            memory=m,
            agent_id=agent_id,
            project_name=project_name,
            run_id=run_id,
        )

    # 10. Write run_step + FULL audit to Supabase
    step_record = None
    if run_id:
        try:
            step_record = db.create_step({
                "run_id": run_id,
                "agent_id": agent_id,
                "persona_id": persona_id,
                "step_number": step_number,
                "attempt": state.get("attempt_count", 0) + 1,
                "status": "passed" if gates_passed else "failed",
                "inputs": _safe_json(
                    {k: state.get(k) for inp in agent.get("inputs", [])
                     if (k := inp.get("name")) and k in state}
                ),
                "outputs": _safe_json(parsed),
                "gate_results": gate_results,
                "duration_ms": duration_ms,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            bus.emit(PipelineEvent(
                event_type=EventType.DB_ERROR,
                run_id=run_id,
                agent_id=agent_id,
                step_number=step_number,
                data={"operation": "create_step", "error": str(exc)},
            ))
            logger.error("[%s] Failed to create step record: %s", agent_id, exc)

        # Log FULL LLM call audit (not truncated)
        try:
            log_data = {
                "run_id": run_id,
                "agent_id": agent_id,
                "execution_type": agent.get("model_config", {}).get("execution", "llm_api"),
                "provider": raw_result.get("provider", "unknown"),
                "model": raw_result.get("model", "unknown"),
                "temperature": raw_result.get("temperature", 0.0),
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "raw_response": content,
                "parsed_output": _safe_json(parsed),
                "parse_success": not parsed.get("_parse_error", False),
                "session_id": raw_result.get("session_id"),
                "input_tokens": raw_result.get("usage", {}).get("input_tokens"),
                "output_tokens": raw_result.get("usage", {}).get("output_tokens"),
                "latency_ms": raw_result.get("latency_ms", 0),
            }
            if step_record and step_record.get("id"):
                log_data["step_id"] = step_record["id"]
            db.log_llm_call(log_data)
        except Exception as exc:
            bus.emit(PipelineEvent(
                event_type=EventType.DB_ERROR,
                run_id=run_id,
                agent_id=agent_id,
                step_number=step_number,
                data={"operation": "log_llm_call", "error": str(exc)},
            ))
            logger.error("[%s] Failed to log LLM call: %s", agent_id, exc)

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

    # Internal flag for make_node to include in NODE_COMPLETE event
    state_updates["_gates_passed"] = gates_passed

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


# ── Helper: gate evaluation with audit ──────────────────────────

def _evaluate_gates_with_audit(
    gates: list[dict],
    outputs: dict[str, Any],
    run_id: str | None,
    agent_id: str,
    step_number: int,
) -> dict[str, bool]:
    """Evaluate gates and emit events for each evaluation."""
    bus = get_event_bus()
    results: dict[str, bool] = {}

    for gate in gates:
        gate_id = gate.get("id", "unknown")
        predicate = gate.get("predicate", "True")
        try:
            passed = evaluate_gate(predicate, outputs)
            results[gate_id] = passed
            bus.emit(PipelineEvent(
                event_type=EventType.GATE_EVALUATED,
                run_id=run_id,
                agent_id=agent_id,
                step_number=step_number,
                data={
                    "gate_id": gate_id,
                    "predicate": predicate,
                    "passed": passed,
                },
            ))
        except GateEvaluationError as exc:
            results[gate_id] = False
            bus.emit(PipelineEvent(
                event_type=EventType.GATE_ERROR,
                run_id=run_id,
                agent_id=agent_id,
                step_number=step_number,
                data={
                    "gate_id": gate_id,
                    "predicate": predicate,
                    "error": str(exc),
                },
            ))
            logger.warning("[%s] Gate %s failed: %s", agent_id, gate_id, exc)

    return results


# ── Helper: safe memory loading (emit events instead of silent pass) ──

def _safe_load_memories(
    loader_fn: Callable,
    output_dir: str,
    key: str,
    run_id: str | None,
    agent_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Load memories with event emission on failure instead of silent pass."""
    try:
        return loader_fn(output_dir, key, limit=limit)
    except Exception as exc:
        get_event_bus().emit(PipelineEvent(
            event_type=EventType.MEMORY_LOAD_ERROR,
            run_id=run_id,
            agent_id=agent_id,
            data={
                "loader": loader_fn.__name__,
                "key": key,
                "error": str(exc),
            },
        ))
        logger.warning("[%s] Memory load failed (%s): %s", agent_id, loader_fn.__name__, exc)
        return []


# ── Helper: safe memory save (emit events instead of silent pass) ──

def _safe_save_memory(
    output_dir: str,
    memory: dict[str, Any],
    agent_id: str,
    project_name: str,
    run_id: str | None,
) -> None:
    """Save a memory with event emission on failure."""
    bus = get_event_bus()
    try:
        filepath = mem.save_memory(
            output_dir=output_dir,
            memory_type=memory["memory_type"],
            agent_id=agent_id,
            content=memory["content"],
            summary=memory["summary"],
            project_name=project_name if memory["memory_type"] == "project" else None,
            tags=memory.get("tags", []),
        )
        bus.emit(PipelineEvent(
            event_type=EventType.MEMORY_SAVED,
            run_id=run_id,
            agent_id=agent_id,
            data={
                "memory_type": memory["memory_type"],
                "summary": memory["summary"],
                "file_path": filepath,
            },
        ))
    except Exception as exc:
        bus.emit(PipelineEvent(
            event_type=EventType.MEMORY_SAVE_ERROR,
            run_id=run_id,
            agent_id=agent_id,
            data={
                "memory_type": memory["memory_type"],
                "summary": memory["summary"],
                "error": str(exc),
            },
        ))
        logger.error("[%s] Memory save failed: %s", agent_id, exc)


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
