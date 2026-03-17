"""Brainstorm & Research agent node."""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _build_prompt(agent: dict, state: PipelineState) -> str:
    ideas = state.get("ideas", [])
    idea_text = "\n".join(f"- {idea}" for idea in ideas)
    return (
        f"Research and validate the following idea(s):\n\n{idea_text}\n\n"
        f"Project name: {state.get('project_name', 'Unnamed')}\n\n"
        f"Provide a comprehensive research brief with market analysis, "
        f"competitive landscape (at least 3 competitors), feature breakdown, "
        f"technical recommendations, risks, and a confidence score (0.0-1.0)."
    )


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    output_dir = Path(state.get("output_dir", "./artifacts"))
    docs_dir = output_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "brainstorm.md").write_text(
        json.dumps(parsed, indent=2), encoding="utf-8"
    )

    return {
        "research_brief": parsed.get("research_brief", parsed),
        "validated_idea": parsed.get("validated_idea", {}),
        "current_stage": "brainstorm",
        "awaiting_approval": state.get("mode") == "supervised",
    }


brainstorm_node = make_node("brainstorm_agent", _build_prompt, _extract_outputs)
