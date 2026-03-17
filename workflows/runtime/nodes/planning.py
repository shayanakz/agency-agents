"""Sprint Planning agent node."""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _build_prompt(agent: dict, state: PipelineState) -> str:
    parts = [
        "Based on the approved research brief, create a sprint backlog, "
        "technical architecture, and schema contract.\n"
    ]
    parts.append(build_inputs_summary(agent, state))
    return "\n\n".join(parts)


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    output_dir = Path(state.get("output_dir", "./artifacts"))
    docs_dir = output_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    sprint_backlog = parsed.get("sprint_backlog", {})
    architecture = parsed.get("architecture", {})
    schema_contract = parsed.get("schema_contract", {})

    tasks = sprint_backlog.get("tasks", [])

    # Write phase specs
    for i, task in enumerate(tasks, 1):
        (docs_dir / f"phase-{i}-spec.md").write_text(
            json.dumps(task, indent=2), encoding="utf-8"
        )

    (docs_dir / "schema-contract.md").write_text(
        json.dumps(schema_contract, indent=2), encoding="utf-8"
    )
    (docs_dir / "architecture.md").write_text(
        json.dumps(architecture, indent=2), encoding="utf-8"
    )

    # Cap tasks to prevent excessively long pipelines
    max_tasks = min(len(tasks), 3)
    if len(tasks) > max_tasks:
        sprint_backlog = dict(sprint_backlog)
        sprint_backlog["tasks"] = tasks[:max_tasks]

    return {
        "sprint_backlog": sprint_backlog,
        "architecture": architecture,
        "schema_contract": schema_contract,
        "total_tasks": max_tasks,
        "current_task_index": 0,
        "current_stage": "planning",
        "awaiting_approval": state.get("mode") == "supervised",
    }


planning_node = make_node("planning_agent", _build_prompt, _extract_outputs)
