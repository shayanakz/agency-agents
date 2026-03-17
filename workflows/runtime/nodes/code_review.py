"""Code Review agent node.

IMPORTANT: Validates that code_artifact has actual files before reviewing.
If no code exists, returns CHANGES_REQUESTED immediately — does NOT waste
an LLM call reviewing nothing.
"""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _has_real_code(state: PipelineState) -> bool:
    """Check if there's actual code to review."""
    artifact = state.get("code_artifact", {})
    if not isinstance(artifact, dict):
        return False
    files = artifact.get("files_changed", [])
    if not isinstance(files, list) or len(files) == 0:
        return False
    if artifact.get("_parse_error"):
        return False
    return True


def _build_prompt(agent: dict, state: PipelineState) -> str:
    code_artifact = state.get("code_artifact", {})
    if not isinstance(code_artifact, dict):
        return "No code artifact provided. Return CHANGES_REQUESTED."

    files = code_artifact.get("files_changed", [])
    parts = ["Review the following implementation:\n"]

    if isinstance(files, list):
        for f in files[:20]:
            if isinstance(f, dict):
                path = f.get("path", "unknown")
                content = f.get("content", "")
                if content:
                    parts.append(f"### {path}\n```\n{content[:5000]}\n```")
                else:
                    parts.append(f"### {path} (file exists on disk, {f.get('size', '?')} bytes)")

    # Include description from implement agent
    desc = code_artifact.get("description", "")
    if desc:
        parts.append(f"\n### Implementation Summary\n{desc}")

    parts.append("\n" + build_inputs_summary(agent, state))
    return "\n\n".join(parts)


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    # If we short-circuited (no code to review), return the pre-built verdict
    if parsed.get("_short_circuit"):
        return {
            "review_verdict": parsed["_short_circuit"],
            "current_stage": "delivery",
        }

    review_verdict = parsed.get("review_verdict", parsed)
    if not isinstance(review_verdict, dict):
        review_verdict = {"status": "APPROVED", "_raw": str(review_verdict)[:500]}

    output_dir = Path(state.get("output_dir", "./artifacts"))
    results_dir = output_dir / "test-results"
    results_dir.mkdir(parents=True, exist_ok=True)
    phase = state.get("current_task_index", 0) + 1
    (results_dir / f"phase-{phase}-code-review.md").write_text(
        json.dumps(review_verdict, indent=2, default=str), encoding="utf-8"
    )

    return {
        "review_verdict": review_verdict,
        "current_stage": "delivery",
    }


# Custom node that validates input before calling LLM
_base_code_review_node = make_node("code_review_agent", _build_prompt, _extract_outputs)


def code_review_node(state: PipelineState) -> dict:
    """Code review with input validation — skips LLM if no code exists."""
    if not _has_real_code(state):
        return {
            "review_verdict": {
                "status": "CHANGES_REQUESTED",
                "comments": [{"severity": "critical", "comment": "No code was produced by implement agent", "file": "N/A"}],
                "blocking_issues": ["No implementation files found"],
            },
            "current_stage": "delivery",
            "step_count": state.get("step_count", 0) + 1,
            "messages": state.get("messages", []) + [
                "[code_review_agent] SKIPPED — no code to review"
            ],
        }
    return _base_code_review_node(state)
