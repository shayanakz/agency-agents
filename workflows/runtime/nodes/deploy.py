"""Deployment agent node."""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _build_prompt(agent: dict, state: PipelineState) -> str:
    architecture = state.get("architecture", {})
    deploy_target = architecture.get("deployment_target", "vercel")
    project_dir = state.get("project_dir", "./projects/unnamed")

    parts = [
        f"Deploy project '{state.get('project_name', 'Unknown')}' to {deploy_target}.\n",
        f"**Project directory:** `{project_dir}`  ← source to deploy",
        f"**Deployment target:** {deploy_target}",
        f"**Tasks completed:** {state.get('total_tasks', 0)}",
        f"**Reality check status:** {state.get('reality_verdict', {}).get('status', 'N/A')}",
        "\nYou MUST:",
        "1. Run deployment commands from the project directory",
        "2. Generate deployment config for the target",
        "3. Run health checks",
        "4. Produce rollback instructions regardless of outcome",
        "5. Log the full deployment output",
    ]

    return "\n".join(parts)


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    # Guard against parse failures
    if parsed.get("_parse_error"):
        deployment_manifest = {
            "status": "failed",
            "error": "Deploy agent output could not be parsed as JSON",
            "_parse_error": True,
        }
    else:
        deployment_manifest = parsed.get("deployment_manifest", parsed)

    output_dir = Path(state.get("output_dir", "./artifacts"))
    results_dir = output_dir / "test-results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "deployment-verification.md").write_text(
        json.dumps(deployment_manifest, indent=2), encoding="utf-8"
    )

    return {
        "deployment_manifest": deployment_manifest,
        "current_stage": "deployment",
    }


deploy_node = make_node("deploy_agent", _build_prompt, _extract_outputs)
