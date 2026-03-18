"""Schema Contract Verification agent node."""

import json
from pathlib import Path
from typing import Any

from ..state import PipelineState
from .base import build_inputs_summary, make_node


def _build_prompt(agent: dict, state: PipelineState) -> str:
    parts = [
        "Verify the schema contract for internal consistency, completeness, "
        "and alignment with the architecture and sprint backlog.\n"
    ]
    parts.append(build_inputs_summary(agent, state))
    return "\n\n".join(parts)


def _extract_outputs(parsed: dict[str, Any], state: PipelineState) -> dict[str, Any]:
    output_dir = Path(state.get("output_dir", "./artifacts"))
    results_dir = output_dir / "test-results"
    results_dir.mkdir(parents=True, exist_ok=True)

    phase = state.get("current_phase", 0) + 1
    (results_dir / f"phase-{phase}-schema-check.md").write_text(
        json.dumps(parsed, indent=2), encoding="utf-8"
    )

    # Guard against parse failures — treat as invalid schema
    if parsed.get("_parse_error"):
        schema_retry_count = state.get("schema_retry_count", 0)
        return {
            "verified_schema": None,
            "schema_contract": state.get("schema_contract"),
            "current_stage": "planning",
            "schema_valid": False,
            "schema_issues": [{"severity": "critical", "description": "Schema verify output was not valid JSON"}],
            "schema_retry_count": schema_retry_count + 1,
        }

    schema_valid = parsed.get("schema_valid", False)
    verified_schema = parsed.get("verified_schema", state.get("schema_contract"))

    schema_retry_count = state.get("schema_retry_count", 0)

    return {
        "verified_schema": verified_schema if schema_valid else None,
        "schema_contract": verified_schema or state.get("schema_contract"),
        "current_stage": "delivery" if schema_valid else "planning",
        # Flatten for gate evaluation
        "schema_valid": schema_valid,
        "schema_issues": parsed.get("schema_issues", []),
        # Track retries to prevent infinite loop
        "schema_retry_count": schema_retry_count + (0 if schema_valid else 1),
    }


schema_verify_node = make_node("schema_verify_agent", _build_prompt, _extract_outputs)
