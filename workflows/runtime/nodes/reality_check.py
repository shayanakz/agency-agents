"""Reality Check agent node.

IMPORTANT: Validates that meaningful evidence exists before judging.
If code_artifact is empty and QA found nothing, returns NEEDS_WORK
immediately with clear instructions — does NOT fabricate a verdict.
"""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _has_meaningful_evidence(state: PipelineState) -> bool:
    """Check if there's real evidence to judge."""
    artifact = state.get("code_artifact", {})
    if not isinstance(artifact, dict):
        return False
    files = artifact.get("files_changed", [])
    if not isinstance(files, list) or len(files) == 0:
        return False
    return True


def _build_prompt(agent: dict, state: PipelineState) -> str:
    current_task = state.get("current_task", {})
    review_verdict = state.get("review_verdict", {})
    qa_report = state.get("qa_report", {})

    parts = [
        f"Reality check for: {current_task.get('title', 'Unknown')}\n",
        "**Acceptance Criteria:**",
    ]
    for criterion in current_task.get("acceptance_criteria", []):
        parts.append(f"- {criterion}")

    rv_status = review_verdict.get("status", "N/A") if isinstance(review_verdict, dict) else "N/A"
    qa_verdict = qa_report.get("overall_verdict", "N/A") if isinstance(qa_report, dict) else "N/A"
    qa_screenshots = len(qa_report.get("screenshots", [])) if isinstance(qa_report, dict) else 0
    qa_issues = len(qa_report.get("issues", [])) if isinstance(qa_report, dict) else 0
    qa_errors = len(qa_report.get("console_errors", [])) if isinstance(qa_report, dict) else 0

    parts.append(f"\n**Code Review Status:** {rv_status}")
    parts.append(f"**QA Verdict:** {qa_verdict}")
    parts.append(f"**QA Screenshots:** {qa_screenshots}")
    parts.append(f"**QA Issues Found:** {qa_issues}")
    parts.append(f"**Console Errors:** {qa_errors}")

    parts.append(
        "\nYour default is NEEDS_WORK. Only output READY if:\n"
        "- Every acceptance criterion has evidence\n"
        "- All code review issues are resolved\n"
        "- Zero console errors\n"
        "- Screenshots exist for all screens"
    )

    parts.append("\n" + build_inputs_summary(agent, state))
    return "\n".join(parts)


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    reality_verdict = parsed.get("reality_verdict", parsed)
    if not isinstance(reality_verdict, dict):
        reality_verdict = {"status": "NEEDS_WORK", "_raw": str(reality_verdict)[:500]}

    output_dir = Path(state.get("output_dir", "./artifacts"))
    results_dir = output_dir / "test-results"
    results_dir.mkdir(parents=True, exist_ok=True)
    phase = state.get("current_task_index", 0) + 1
    (results_dir / f"phase-{phase}-reality-check.md").write_text(
        json.dumps(reality_verdict, indent=2, default=str), encoding="utf-8"
    )

    status = reality_verdict.get("status", "NEEDS_WORK")
    updates: dict[str, Any] = {
        "reality_verdict": reality_verdict,
        "current_stage": "delivery",
    }

    if status == "NEEDS_WORK":
        updates["fix_instructions"] = reality_verdict.get("fix_instructions", [])
        updates["attempt_count"] = state.get("attempt_count", 0) + 1

    return updates


_base_reality_check_node = make_node("reality_check_agent", _build_prompt, _extract_outputs)


def reality_check_node(state: PipelineState) -> dict:
    """Reality check with input validation — fast-fails if no evidence exists."""
    if not _has_meaningful_evidence(state):
        attempt = state.get("attempt_count", 0) + 1
        return {
            "reality_verdict": {
                "status": "NEEDS_WORK",
                "quality_rating": "F",
                "issues": [{"id": "no-code", "severity": "critical", "description": "Implement agent produced no files"}],
                "evidence_references": [],
                "spec_compliance": [],
                "fix_instructions": [{"issue_id": "no-code", "instruction": "Implement agent must produce actual source files", "files_to_modify": []}],
            },
            "fix_instructions": [{"issue_id": "no-code", "instruction": "Implement agent must produce actual source files", "files_to_modify": []}],
            "attempt_count": attempt,
            "current_stage": "delivery",
            "step_count": state.get("step_count", 0) + 1,
            "messages": state.get("messages", []) + [
                f"[reality_check_agent] FAST-FAIL — no code evidence (attempt {attempt})"
            ],
        }
    return _base_reality_check_node(state)
