"""Implementation agent node.

Uses claude_code execution — writes files to disk via tools.
Output is NOT structured JSON — it's prose describing what was done.
We scan project_dir to find what was created, read contents, and
git-commit so downstream agents get a real diff.
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ..events import EventType, PipelineEvent, get_event_bus
from ..state import PipelineState
from .base import build_inputs_summary, make_node

logger = logging.getLogger("pipeline.nodes.implement")


def _build_prompt(agent: dict, state: PipelineState) -> str:
    current_task = state.get("current_task", {})
    task_idx = state.get("current_task_index", 0)
    total = state.get("total_tasks", 0)
    project_dir = state.get("project_dir", "./projects/unnamed")

    parts = [
        f"**Project directory:** `{project_dir}`  ← write ALL files here\n",
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
    """Extract outputs by scanning project_dir for created files.

    claude_code agents write files directly to disk — they don't return
    structured JSON. We scan project_dir, read file contents, and
    git-commit so downstream agents get a real diff.
    """
    project_dir = Path(state.get("project_dir", "./projects/unnamed"))
    project_dir.mkdir(parents=True, exist_ok=True)

    # Also check if the LLM returned structured JSON and write those files
    json_files = []
    raw_artifact = parsed.get("code_artifact", {})
    if isinstance(raw_artifact, dict):
        json_files = raw_artifact.get("files_changed", [])
        if isinstance(json_files, list):
            for file_info in json_files:
                if isinstance(file_info, dict) and file_info.get("content"):
                    file_path = project_dir / file_info.get("path", "unknown")
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(file_info.get("content", ""), encoding="utf-8")

    # Scan project_dir for all files Claude Code actually wrote
    disk_files = []
    if project_dir.exists():
        for f in sorted(project_dir.rglob("*")):
            if not f.is_file() or f.stat().st_size == 0:
                continue
            # Skip .git internals
            if ".git" in f.parts:
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
            disk_files.append({
                "path": str(f.relative_to(project_dir)),
                "size": f.stat().st_size,
                "action": "create",
                "content": content[:8000],  # cap per file to avoid token explosion
            })

    all_files = disk_files if disk_files else json_files

    # Git commit so downstream agents can use git diff
    git_diff = _git_commit(
        project_dir,
        task_index=state.get("current_task_index", 0),
        task_title=state.get("current_task", {}).get("title", "implementation"),
    )

    prose = parsed.get("_raw", "") or ""

    code_artifact = {
        "files_changed": all_files,
        "file_count": len(all_files),
        "project_dir": str(project_dir),
        "git_diff": git_diff,
        "schema_compliant": len(all_files) > 0,
        "description": prose[:500] if isinstance(prose, str) else "",
    }

    return {
        "code_artifact": code_artifact,
        "current_stage": "delivery",
        "fix_instructions": None,
    }


def _git_commit(project_dir: Path, task_index: int, task_title: str) -> str:
    """Git init (if needed), stage all, commit, return diff stat.

    Returns the diff --stat string so reviewers see what changed.
    Emits events on failure instead of silently returning empty string.
    """
    bus = get_event_bus()
    try:
        git_dir = project_dir / ".git"
        if not git_dir.exists():
            subprocess.run(
                ["git", "init"],
                cwd=str(project_dir),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "pipeline@agents.local"],
                cwd=str(project_dir),
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Pipeline"],
                cwd=str(project_dir),
                capture_output=True,
                check=True,
            )

        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(project_dir),
            capture_output=True,
            check=True,
        )

        commit_msg = f"Task {task_index + 1}: {task_title}"
        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,  # returncode 1 = nothing to commit
        )

        if "nothing to commit" in commit_result.stdout:
            return ""

        if commit_result.returncode != 0:
            error_msg = commit_result.stderr.strip() or commit_result.stdout.strip()
            bus.emit(PipelineEvent(
                event_type=EventType.GIT_ERROR,
                data={"operation": "commit", "error": error_msg, "project_dir": str(project_dir)},
            ))
            logger.warning("Git commit failed in %s: %s", project_dir, error_msg)
            return ""

        diff_result = subprocess.run(
            ["git", "diff", "HEAD~1", "--stat"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )

        bus.emit(PipelineEvent(
            event_type=EventType.GIT_COMMIT,
            data={
                "project_dir": str(project_dir),
                "commit_msg": commit_msg,
                "diff_stat": diff_result.stdout.strip()[:500],
            },
        ))
        return diff_result.stdout.strip()
    except Exception as exc:
        bus.emit(PipelineEvent(
            event_type=EventType.GIT_ERROR,
            data={"operation": "git_commit", "error": str(exc), "project_dir": str(project_dir)},
        ))
        logger.error("Git operations failed in %s: %s", project_dir, exc)
        return ""


implement_node = make_node("implement_agent", _build_prompt, _extract_outputs)
