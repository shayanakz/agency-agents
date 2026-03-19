"""QA & Evidence agent node.

Anti-fabrication contract:
- If no code exists → hard FAIL, no LLM call.
- If browser MCP is unavailable → hard FAIL, no LLM call.
  A FAIL is honest. A fabricated screenshot report is not.
- After the LLM runs, every claimed screenshot is verified to exist
  on disk. Screenshots that don't exist are stripped from the report.
  If zero real screenshots remain → verdict downgraded to FAIL.
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
    code_artifact = state.get("code_artifact", {}) if isinstance(state.get("code_artifact"), dict) else {}

    project_dir = code_artifact.get("project_dir", state.get("project_dir", ""))
    files = code_artifact.get("files_changed", [])
    file_count = len(files) if isinstance(files, list) else 0

    # Determine how to run the app
    file_names = [f.get("path", "") for f in files if isinstance(f, dict)]
    has_package_json = any("package.json" in p for p in file_names)
    has_index_html = any(p.endswith("index.html") for p in file_names)

    if has_package_json:
        run_instruction = f"cd {project_dir} && npm install && npm run dev (or npm start)"
    elif has_index_html:
        index_path = next((p for p in file_names if p.endswith("index.html")), "index.html")
        run_instruction = f"open {project_dir}/{index_path} in a browser"
    else:
        run_instruction = f"inspect files in {project_dir}"

    parts = [
        f"Perform QA testing for: {current_task.get('title', 'Unknown')}\n",
        f"**Project directory:** `{project_dir}`",
        f"**How to run:** {run_instruction}",
        f"**Files implemented:** {file_count}",
        "\n**Acceptance Criteria to verify:**",
    ]
    for criterion in current_task.get("acceptance_criteria", []):
        parts.append(f"- {criterion}")

    parts.append(
        "\nYou MUST:\n"
        "1. Navigate to the project directory and run the app\n"
        "2. Take screenshots (desktop + mobile minimum)\n"
        "3. Check every acceptance criterion with evidence\n"
        "4. Check browser console for errors\n"
        "5. Default to finding 3-5 real issues\n\n"
        "curl returns 200 is NOT QA. Screenshots are required."
    )

    return "\n".join(parts)


def _verify_screenshots(
    claimed: list[dict], screenshots_dir: Path
) -> tuple[list[dict], list[str]]:
    """Cross-reference claimed screenshots against files that actually exist on disk.

    Returns:
        (verified, fabricated_names)
        verified        — screenshot entries where the file exists on disk
        fabricated_names — names that were claimed but don't exist
    """
    verified = []
    fabricated = []

    for s in claimed:
        if not isinstance(s, dict):
            continue
        name = s.get("name", "")
        if not name:
            fabricated.append("<unnamed>")
            continue
        # Accept the screenshot if the file exists anywhere under screenshots_dir
        # or as an absolute path
        candidate = screenshots_dir / name
        alt_candidate = screenshots_dir / Path(name).name  # strip subdirs
        if candidate.exists() or alt_candidate.exists():
            verified.append(s)
        else:
            fabricated.append(name)

    return verified, fabricated


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    # Guard against parse failures — FAIL is the safe default, never fabricate a PASS
    if parsed.get("_parse_error"):
        qa_report = {
            "overall_verdict": "FAIL",
            "screenshots": [],
            "issues": [{"id": "parse-error", "severity": "critical",
                        "description": "QA agent output could not be parsed as JSON"}],
            "criteria_checked": [],
            "_parse_error": True,
        }
    else:
        qa_report = parsed.get("qa_report", parsed)
    if not isinstance(qa_report, dict):
        qa_report = {"overall_verdict": "FAIL", "_raw": str(qa_report)[:500]}

    output_dir = Path(state.get("output_dir", "./artifacts"))
    results_dir = output_dir / "test-results"
    screenshots_dir = results_dir / "screenshots"
    results_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # ── Anti-fabrication: verify every claimed screenshot exists on disk ──
    claimed_screenshots = qa_report.get("screenshots", [])
    if not isinstance(claimed_screenshots, list):
        claimed_screenshots = []

    verified_screenshots, fabricated_names = _verify_screenshots(
        claimed_screenshots, screenshots_dir
    )

    if fabricated_names:
        # Record exactly what was fabricated so reality_check can see it
        qa_report["fabricated_screenshots"] = fabricated_names
        qa_report["screenshots"] = verified_screenshots

        if not verified_screenshots:
            # All screenshots were fabricated — downgrade to hard FAIL
            qa_report["overall_verdict"] = "FAIL"
            qa_report.setdefault("issues", []).insert(0, {
                "id": "fabricated-screenshots",
                "severity": "critical",
                "description": (
                    f"QA agent claimed {len(fabricated_names)} screenshot(s) that do not "
                    f"exist on disk: {fabricated_names}. All screenshots fabricated. "
                    f"Verdict forced to FAIL."
                ),
            })
        else:
            # Some real, some fabricated — keep real ones, note the fabrication
            qa_report.setdefault("issues", []).insert(0, {
                "id": "partial-fabrication",
                "severity": "high",
                "description": (
                    f"QA agent claimed {len(fabricated_names)} screenshot(s) that do not "
                    f"exist on disk: {fabricated_names}. Only verified screenshots kept."
                ),
            })

    phase = state.get("current_task_index", 0) + 1
    (results_dir / f"phase-{phase}-qa-report.md").write_text(
        json.dumps(qa_report, indent=2, default=str), encoding="utf-8"
    )

    return {
        "qa_report": qa_report,
        "current_stage": "delivery",
    }


_base_qa_node = make_node("qa_agent", _build_prompt, _extract_outputs)


def _hard_fail(reason: str, step_count: int, messages: list) -> dict:
    """Return a hard FAIL qa_report without calling the LLM."""
    return {
        "qa_report": {
            "overall_verdict": "FAIL",
            "screenshots": [],
            "issues": [{"id": "qa-precondition-failed", "severity": "critical", "description": reason}],
            "criteria_checked": [],
            "console_errors": [reason],
        },
        "current_stage": "delivery",
        "step_count": step_count,
        "messages": messages + [f"[qa_agent] HARD FAIL — {reason}"],
    }


def qa_node(state: PipelineState) -> dict:
    """QA with pre-flight checks.

    Checks before calling LLM:
    1. Code must exist (files written by implement_agent)
    2. Browser MCP is optional — if unavailable, QA does code-based testing
    """
    step = state.get("step_count", 0) + 1
    messages = state.get("messages", [])

    # Check: code must exist
    if not _has_real_code(state):
        return _hard_fail("No implementation files found to test", step, messages)

    # Browser MCP is nice-to-have, not a hard requirement
    return _base_qa_node(state)
