"""Deployment agent node."""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _build_prompt(agent: dict, state: PipelineState) -> str:
    architecture = state.get("architecture", {})
    deploy_target = architecture.get("deployment_target", "vercel")

    parts = [
        f"Deploy project '{state.get('project_name', 'Unknown')}' to {deploy_target}.\n",
        f"**Deployment target:** {deploy_target}",
        f"**Tasks completed:** {state.get('total_tasks', 0)}",
        f"**Reality check status:** {state.get('reality_verdict', {}).get('status', 'N/A')}",
        "\nYou MUST:",
        "1. Generate deployment config for the target",
        "2. Execute deployment commands",
        "3. Run health checks",
        "4. Produce rollback instructions regardless of outcome",
        "5. Log the full deployment output",
    ]

    return "\n".join(parts)


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
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
