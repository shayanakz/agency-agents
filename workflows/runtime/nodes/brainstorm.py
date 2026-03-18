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
        f"Project: {state.get('project_name', 'Unnamed')}\n"
        f"Idea: {idea_text}\n\n"
        f"Turn this idea into a tight, implementation-ready spec.\n\n"
        f"Answer only these questions — nothing else:\n"
        f"1. What exactly does this build? (one crisp sentence)\n"
        f"2. Who uses it and what problem does it solve for them?\n"
        f"3. What are the CORE features? (only what's in the idea — do not add extras)\n"
        f"4. What is explicitly OUT OF SCOPE? (name the tempting extras to avoid)\n"
        f"5. What tech stack fits this specific project?\n"
        f"6. What are the top 2-3 risks that could derail implementation?\n\n"
        f"DO NOT: write market analysis, competitor research, RICE scores, "
        f"or business strategy. This is a build spec, not a business plan."
    )


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    output_dir = Path(state.get("output_dir", "./artifacts"))
    docs_dir = output_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "brainstorm.md").write_text(
        json.dumps(parsed, indent=2), encoding="utf-8"
    )

    # Guard against parse failures — don't propagate garbage as a valid brief
    if parsed.get("_parse_error"):
        return {
            "research_brief": {"_parse_error": True, "_raw": parsed.get("_raw", "")[:500]},
            "validated_idea": {},
            "current_stage": "brainstorm",
            "awaiting_approval": state.get("mode") == "supervised",
        }

    return {
        "research_brief": parsed.get("research_brief", parsed),
        "validated_idea": parsed.get("validated_idea", {}),
        "current_stage": "brainstorm",
        "awaiting_approval": state.get("mode") == "supervised",
    }


brainstorm_node = make_node("brainstorm_agent", _build_prompt, _extract_outputs)
