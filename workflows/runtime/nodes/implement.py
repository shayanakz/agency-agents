"""Implementation agent node.

Uses claude_code execution — writes files to disk via tools.
Output is NOT structured JSON — it's prose describing what was done.
We scan the filesystem to determine what was actually created.
"""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _build_prompt(agent: dict, state: PipelineState) -> str:
    current_task = state.get("current_task", {})
    task_idx = state.get("current_task_index", 0)
    total = state.get("total_tasks", 0)

    parts = [
        f"Implement task {task_idx + 1} of {total}:\n",
        f"**Task:** {current_task.get('title', 'Unknown')}\n",
        f"**Description:** {current_task.get('description', '')}\n",
        f"**Category:** {current_task.get('category', 'fullstack')}\n",
        "**Acceptance Criteria:**",
    ]
    for criterion in current_task.get("acceptance_criteria", []):
        parts.append(f"- {criterion}")

    # Add fix instructions if this is a retry
    fix_instructions = state.get("fix_instructions")
    if fix_instructions and isinstance(fix_instructions, list):
        parts.append("\n**FIX INSTRUCTIONS (from previous rejection):**")
        for fix in fix_instructions:
            if isinstance(fix, dict):
                parts.append(f"- Issue {fix.get('issue_id', '?')}: {fix.get('instruction', '')}")

    # Add previous review feedback if retrying
    review = state.get("review_verdict")
    if review and isinstance(review, dict) and review.get("status") in ("CHANGES_REQUESTED", "REJECTED"):
        parts.append("\n**PREVIOUS CODE REVIEW FEEDBACK:**")
        for comment in review.get("comments", []):
            if isinstance(comment, dict):
                parts.append(f"- [{comment.get('severity', '?')}] {comment.get('file', '?')}:{comment.get('line', '?')} — {comment.get('comment', '')}")

    parts.append("\n" + build_inputs_summary(agent, state))
    return "\n".join(parts)


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    """Extract outputs by scanning the filesystem for created files.

    claude_code agents write files directly to disk — they don't return
    structured JSON. We scan artifacts/src/ to find what was created.
    """
    output_dir = Path(state.get("output_dir", "./artifacts"))
    src_dir = output_dir / "src"

    # Scan filesystem for files that Claude Code actually created
    disk_files = []
    if src_dir.exists():
        disk_files = [
            {
                "path": str(f.relative_to(output_dir)),
                "size": f.stat().st_size,
                "action": "create",
            }
            for f in sorted(src_dir.rglob("*"))
            if f.is_file() and f.stat().st_size > 0
        ]

    # Also check if the LLM returned structured JSON (rare for claude_code)
    json_files = []
    code_artifact = parsed.get("code_artifact", {})
    if isinstance(code_artifact, dict):
        json_files = code_artifact.get("files_changed", [])
        if isinstance(json_files, list):
            for file_info in json_files:
                if isinstance(file_info, dict) and file_info.get("content"):
                    file_path = output_dir / file_info.get("path", "unknown")
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(file_info.get("content", ""), encoding="utf-8")

    # Use whichever source found files
    all_files = json_files if json_files else disk_files

    # Get the prose description from claude_code
    prose = parsed.get("_raw", "") or ""

    code_artifact = {
        "files_changed": all_files,
        "file_count": len(all_files),
        "schema_compliant": len(all_files) > 0,
        "description": prose[:500] if isinstance(prose, str) else "",
    }

    return {
        "code_artifact": code_artifact,
        "current_stage": "delivery",
        "fix_instructions": None,
    }


implement_node = make_node("implement_agent", _build_prompt, _extract_outputs)
