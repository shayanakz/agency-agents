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

    # Guard against parse failures — empty backlog triggers gate failure
    if parsed.get("_parse_error"):
        return {
            "sprint_backlog": {},
            "architecture": {},
            "schema_contract": {},
            "total_tasks": 0,
            "current_stage": "planning",
            "awaiting_approval": state.get("mode") == "supervised",
        }

    sprint_backlog = parsed.get("sprint_backlog", {})
    architecture = parsed.get("architecture", {})
    schema_contract = parsed.get("schema_contract", {})

    tasks = sprint_backlog.get("tasks", [])

    # Cap tasks to prevent excessively long pipelines
    max_tasks = min(len(tasks), 5)
    if len(tasks) > max_tasks:
        sprint_backlog = dict(sprint_backlog)
        sprint_backlog["tasks"] = tasks[:max_tasks]
        tasks = sprint_backlog["tasks"]

    # Append to PROJECT_PLAN.md (brainstorm wrote the spec section)
    plan_path = docs_dir / "PROJECT_PLAN.md"
    sections = []

    sections.append("\n## Tasks\n")
    for i, task in enumerate(tasks, 1):
        title = task.get("title", f"Task {i}")
        desc = task.get("description", "")
        hours = task.get("estimated_hours", "?")
        cat = task.get("category", "")
        sections.append(f"### {i}. {title}")
        if desc:
            sections.append(desc)
        criteria = task.get("acceptance_criteria", [])
        if criteria:
            sections.append("**Acceptance Criteria:**")
            for c in criteria:
                sections.append(f"- {c}")
        sections.append(f"*{cat} · {hours}h*\n")

    if architecture:
        sections.append("## Architecture\n")
        tech = architecture.get("tech_stack", {})
        if isinstance(tech, dict):
            for k, v in tech.items():
                sections.append(f"- **{k}:** {v}")
        sections.append("")

    # Write by appending to existing plan
    existing = ""
    if plan_path.exists():
        existing = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(existing + "\n".join(sections), encoding="utf-8")

    return {
        "sprint_backlog": sprint_backlog,
        "architecture": architecture,
        "schema_contract": schema_contract,
        "total_tasks": max_tasks,
        "current_stage": "planning",
        "awaiting_approval": state.get("mode") == "supervised",
    }


planning_node = make_node("planning_agent", _build_prompt, _extract_outputs)
