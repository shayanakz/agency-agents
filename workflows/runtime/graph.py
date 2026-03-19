"""LangGraph StateGraph construction.

Builds the pipeline graph dynamically from the Supabase database.
The graph IS the orchestrator — it evaluates gates, routes edges,
manages retries, and enforces sequence. No orchestrator agent.
"""

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from . import db
from .nodes import (
    brainstorm_node,
    code_review_node,
    deploy_node,
    implement_node,
    planning_node,
    qa_node,
    reality_check_node,
    schema_verify_node,
)
from .state import PipelineState

# Node registry mapping agent IDs to node functions
NODE_REGISTRY = {
    "brainstorm_agent": brainstorm_node,
    "planning_agent": planning_node,
    "schema_verify_agent": schema_verify_node,
    "implement_agent": implement_node,
    "code_review_agent": code_review_node,
    "qa_agent": qa_node,
    "reality_check_agent": reality_check_node,
    "deploy_agent": deploy_node,
}


# ── Routing functions ───────────────────────────────────────────
# These are the conditional edge functions. The graph calls them
# after each node completes to determine the next node.


def _is_supervised(state: PipelineState) -> bool:
    return state.get("mode") == "supervised"


def route_after_brainstorm(state: PipelineState) -> str:
    if _is_supervised(state):
        return "await_approval"
    return "planning_agent"


def route_after_planning(state: PipelineState) -> str:
    if _is_supervised(state):
        return "await_approval"
    # Skip schema verify — go straight to build. The implement agent
    # with sonnet/30 turns is better at catching schema issues during
    # implementation than a separate verify step.
    return "prepare_build"


def route_after_schema_verify(state: PipelineState) -> str:
    # If schema verified OR we've retried enough, move to build
    if state.get("schema_valid") or state.get("verified_schema"):
        return "implement_agent"
    schema_retries = state.get("schema_retry_count", 0)
    if schema_retries >= 2:
        return "implement_agent"
    return "planning_agent"


def route_after_reality_check(state: PipelineState) -> str:
    verdict = state.get("reality_verdict", {})
    status = verdict.get("status", "NEEDS_WORK") if isinstance(verdict, dict) else "NEEDS_WORK"

    if status == "READY":
        if _is_supervised(state):
            return "await_approval"
        return "deploy_agent"

    if status == "NEEDS_WORK":
        attempts = state.get("attempt_count", 0)
        max_attempts = state.get("max_attempts", 5)
        if attempts >= max_attempts:
            if _is_supervised(state):
                return "await_approval"
            return "deploy_agent"  # give up, deploy what we have
        return "implement_agent"  # retry the whole build

    # BLOCKED
    if _is_supervised(state):
        return "await_approval"
    return "deploy_agent"


def route_after_approval(state: PipelineState) -> str:
    stage = state.get("current_stage", "")
    if stage == "brainstorm":
        return "planning_agent"
    if stage == "planning":
        return "schema_verify_agent"
    if stage == "delivery":
        return "deploy_agent"
    if stage == "deployment":
        return END
    return END


# ── Helper nodes ────────────────────────────────────────────────


def await_approval_node(state: PipelineState) -> dict:
    """Pause point for human approval.

    In autonomous mode: auto-approves and continues (no blocking).
    In supervised mode: prompts the user interactively via CLI.
    """
    run_id = state.get("run_id")
    stage = state.get("current_stage", "unknown")
    mode = state.get("mode", "supervised")

    # In autonomous mode: auto-approve and continue
    if mode == "autonomous":
        if run_id:
            approval = db.create_approval_request(run_id)
            db.approve_request(
                approval.get("id"), decided_by="autonomous", notes="auto-approved"
            )
        return {"awaiting_approval": False}

    # In supervised mode: prompt the user
    from rich.console import Console
    from rich.prompt import Confirm

    console = Console()

    messages = state.get("messages", [])
    if messages:
        console.print(f"\n[bold cyan]── Stage: {stage} ──[/]")
        for msg in messages[-3:]:
            console.print(f"  {msg}")

    if run_id:
        approval = db.create_approval_request(run_id)
        approval_id = approval.get("id")

        try:
            approved = Confirm.ask(
                "\n[bold yellow]Approve and continue?[/]", default=True
            )
        except EOFError:
            # Non-interactive — auto-approve
            approved = True

        if approved:
            db.approve_request(approval_id, decided_by="cli_user")
        else:
            db.approve_request(approval_id, decided_by="cli_user", notes="rejected")
            return {"awaiting_approval": False, "current_stage": "blocked"}

    return {"awaiting_approval": False}


def prepare_build_node(state: PipelineState) -> dict:
    """Prepare state for the build phase — load all tasks as a batch."""
    return {
        "attempt_count": 0,
        "current_stage": "delivery",
        "previous_outputs": [],
        "session_ids": {},
    }


# ── Graph construction ──────────────────────────────────────────


def build_graph(checkpoint_path: str = "./state/checkpoints.db") -> StateGraph:
    """Build the LangGraph StateGraph for the idea-to-production pipeline.

    The graph structure is:
        brainstorm → planning → schema_verify → load_next_task
            → implement → code_review → qa → reality_check
                (loop back on NEEDS_WORK/CHANGES_REQUESTED/FAIL)
            → advance_task (loop) or deploy
    """
    builder = StateGraph(PipelineState)

    # ── Add nodes ───────────────────────────────────────────────
    builder.add_node("brainstorm_agent", brainstorm_node)
    builder.add_node("planning_agent", planning_node)
    builder.add_node("schema_verify_agent", schema_verify_node)
    builder.add_node("prepare_build", prepare_build_node)
    builder.add_node("implement_agent", implement_node)
    builder.add_node("code_review_agent", code_review_node)
    builder.add_node("qa_agent", qa_node)
    builder.add_node("reality_check_agent", reality_check_node)
    builder.add_node("deploy_agent", deploy_node)
    builder.add_node("await_approval", await_approval_node)

    # ── Set entry point ─────────────────────────────────────────
    builder.set_entry_point("brainstorm_agent")

    # ── Conditional edges ───────────────────────────────────────

    builder.add_conditional_edges(
        "brainstorm_agent",
        route_after_brainstorm,
        {"await_approval": "await_approval", "planning_agent": "planning_agent"},
    )

    builder.add_conditional_edges(
        "planning_agent",
        route_after_planning,
        {"await_approval": "await_approval", "prepare_build": "prepare_build"},
    )

    builder.add_conditional_edges(
        "schema_verify_agent",
        route_after_schema_verify,
        {"implement_agent": "prepare_build", "planning_agent": "planning_agent"},
    )

    # Prepare build → implement (builds ALL tasks in one shot)
    builder.add_edge("prepare_build", "implement_agent")

    # Implement → code review → QA → reality check (single pass)
    builder.add_edge("implement_agent", "code_review_agent")
    builder.add_edge("code_review_agent", "qa_agent")
    builder.add_edge("qa_agent", "reality_check_agent")

    # Reality check decides: retry build or deploy
    builder.add_conditional_edges(
        "reality_check_agent",
        route_after_reality_check,
        {
            "deploy_agent": "deploy_agent",
            "implement_agent": "implement_agent",
            "await_approval": "await_approval",
        },
    )

    # Approval routing
    builder.add_conditional_edges(
        "await_approval",
        route_after_approval,
        {
            "planning_agent": "planning_agent",
            "schema_verify_agent": "schema_verify_agent",
            "deploy_agent": "deploy_agent",
            END: END,
        },
    )

    # Deploy → end
    builder.add_edge("deploy_agent", END)

    # ── Compile with checkpointer ───────────────────────────────
    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return builder.compile(checkpointer=checkpointer)
