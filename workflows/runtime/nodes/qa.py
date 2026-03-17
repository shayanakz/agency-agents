"""QA & Evidence agent node.

IMPORTANT: Validates that code_artifact has actual files before testing.
If no code exists, returns FAIL immediately — does NOT waste an LLM call
testing nothing.
"""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _has_real_code(state: PipelineState) -> bool:
    """Check if there's actual code to test."""
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
    current_task = state.get("current_task", {})
    code_artifact = state.get("code_artifact", {})

    parts = [
        f"Perform QA testing for: {current_task.get('title', 'Unknown')}\n",
        "**Acceptance Criteria to verify:**",
    ]
    for criterion in current_task.get("acceptance_criteria", []):
        parts.append(f"- {criterion}")

    file_count = 0
    if isinstance(code_artifact, dict):
        file_count = len(code_artifact.get("files_changed", []))
    parts.append(f"\n**Files implemented:** {file_count}")

    parts.append(
        "\nYou MUST:\n"
        "1. Take screenshots (desktop + mobile minimum)\n"
        "2. Check every acceptance criterion with evidence\n"
        "3. Check console for errors\n"
        "4. Default to finding 3-5 issues\n\n"
        "curl returns 200 is NOT QA."
    )

    return "\n".join(parts)


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    qa_report = parsed.get("qa_report", parsed)
    if not isinstance(qa_report, dict):
        qa_report = {"overall_verdict": "PARTIAL", "_raw": str(qa_report)[:500]}

    output_dir = Path(state.get("output_dir", "./artifacts"))
    results_dir = output_dir / "test-results"
    results_dir.mkdir(parents=True, exist_ok=True)
    phase = state.get("current_task_index", 0) + 1
    (results_dir / f"phase-{phase}-qa-report.md").write_text(
        json.dumps(qa_report, indent=2, default=str), encoding="utf-8"
    )

    return {
        "qa_report": qa_report,
        "current_stage": "delivery",
    }


_base_qa_node = make_node("qa_agent", _build_prompt, _extract_outputs)


def qa_node(state: PipelineState) -> dict:
    """QA with input validation — returns FAIL immediately if no code exists."""
    if not _has_real_code(state):
        return {
            "qa_report": {
                "overall_verdict": "FAIL",
                "screenshots": [],
                "issues": [{"id": "no-code", "severity": "critical", "description": "No implementation files found to test"}],
                "criteria_checked": [],
                "console_errors": ["No application to test — implement agent produced no files"],
            },
            "current_stage": "delivery",
            "step_count": state.get("step_count", 0) + 1,
            "messages": state.get("messages", []) + [
                "[qa_agent] SKIPPED — no code to test"
            ],
        }
    return _base_qa_node(state)
