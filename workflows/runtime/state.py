"""LangGraph shared state definition.

PipelineState is the TypedDict that flows through every node in the graph.
LangGraph manages this state — nodes return partial updates that get merged.
"""

from typing import Any, Literal, Optional, TypedDict


class PhaseState(TypedDict, total=False):
    phase_number: int
    status: str  # PENDING | IN_PROGRESS | COMPLETE | BLOCKED
    attempts: int
    current_agent: Optional[str]
    gate_results: dict


class PipelineState(TypedDict, total=False):
    # ── Identity ────────────────────────────────────────────────
    project_name: str
    project_dir: str  # Dedicated directory for all project source code
    ideas: list[str]
    output_dir: str   # Artifacts base (docs, test-results, memory)
    workflow_id: str
    run_id: str  # UUID from pipeline_runs table
    mode: Literal["supervised", "autonomous"]

    # ── Stage tracking ──────────────────────────────────────────
    current_stage: str  # brainstorm | planning | delivery | deployment
    current_phase: int
    total_phases: int
    current_task_index: int
    total_tasks: int
    awaiting_approval: bool

    # ── Accumulated artifacts ───────────────────────────────────
    # Each agent writes to these; downstream agents read from them.
    research_brief: Optional[dict[str, Any]]
    validated_idea: Optional[dict[str, Any]]
    sprint_backlog: Optional[dict[str, Any]]
    architecture: Optional[dict[str, Any]]
    schema_contract: Optional[dict[str, Any]]
    verified_schema: Optional[dict[str, Any]]
    current_task: Optional[dict[str, Any]]
    code_artifact: Optional[dict[str, Any]]
    review_verdict: Optional[dict[str, Any]]
    qa_report: Optional[dict[str, Any]]
    reality_verdict: Optional[dict[str, Any]]
    fix_instructions: Optional[list[dict[str, Any]]]
    deployment_manifest: Optional[dict[str, Any]]

    # ── Per-phase tracking ──────────────────────────────────────
    phases: list[PhaseState]
    completed_tasks: list[dict[str, Any]]

    # ── Retry tracking ──────────────────────────────────────────
    attempt_count: int
    max_attempts: int
    previous_outputs: list[dict[str, Any]]  # outputs from previous attempts (retry context)

    # ── Schema verification tracking ─────────────────────────────
    schema_valid: Optional[bool]
    schema_issues: Optional[list[dict[str, Any]]]
    schema_retry_count: int

    # ── Session tracking (for coding agents with --resume) ────
    session_ids: dict[str, str]  # agent_id → Claude Code session_id

    # ── Model overrides (from UI or CLI) ───────────────────────
    # Maps agent_id -> partial model_config dict.
    # Example: {"brainstorm_agent": {"model": "sonnet"}, "__all__": {"model": "opus"}}
    # Per-agent overrides take precedence over __all__.
    model_overrides: Optional[dict[str, dict[str, Any]]]

    # ── Observability ───────────────────────────────────────────
    messages: list[str]
    step_count: int  # incremented per node execution
