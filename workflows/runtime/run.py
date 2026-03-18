#!/usr/bin/env python3
"""CLI entrypoint for the Idea-to-Production pipeline.

Usage:
    # Supervised mode (pauses for approval at each phase)
    python run.py --project "NutriTrack" --idea "nutrition tracking app" --stream

    # Autonomous mode (runs end-to-end, pauses only before deploy)
    python run.py --project "NutriTrack" --idea "nutrition tracking app" --autonomous

    # Multiple ideas
    python run.py --project "Suite" --idea "feature A" --idea "feature B" --stream
"""

import argparse
import logging
import re
import sys
import uuid
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import db
from .events import (
    EventBus,
    EventHandler,
    EventType,
    PipelineEvent,
    create_file_sink,
    create_supabase_sink,
    set_event_bus,
)
from .graph import build_graph


def _derive_project_slug(project_name: str, ideas: list[str]) -> str:
    """Derive a filesystem-safe slug for the project directory.

    Priority:
    1. Slugify --project name if it's meaningful (not a generic word)
    2. Slugify the first idea (first 5 words)
    3. Fallback: short UUID prefix
    """
    def slugify(text: str, max_words: int = 5) -> str:
        words = re.sub(r"[^\w\s-]", "", text.lower()).split()[:max_words]
        return "-".join(words) if words else ""

    slug = slugify(project_name)
    if slug and slug not in ("project", "app", "untitled", "my-project"):
        return slug

    if ideas:
        slug = slugify(ideas[0], max_words=4)
        if slug:
            return slug

    return "project-" + uuid.uuid4().hex[:6]

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idea-to-Production Multi-Agent Pipeline"
    )
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument(
        "--idea",
        required=True,
        action="append",
        help="Idea description (can repeat for multiple ideas)",
    )
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help="Run in autonomous mode (fewer pauses)",
    )
    parser.add_argument(
        "--deploy-target",
        choices=["vercel", "netlify", "docker", "expo", "static"],
        help="Deployment target",
    )
    parser.add_argument(
        "--output",
        default="./artifacts",
        help="Output directory for artifacts (default: ./artifacts)",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream step-by-step output with real-time events",
    )
    parser.add_argument(
        "--workflow",
        default="idea-to-production",
        help="Workflow ID to run (default: idea-to-production)",
    )
    parser.add_argument(
        "--checkpoint-db",
        default="./state/checkpoints.db",
        help="Path to SQLite checkpoint database",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch real-time web dashboard alongside the pipeline",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8787,
        help="Port for the dashboard server (default: 8787)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    # ── Configure logging ──────────────────────────────────────
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Initialize EventBus ────────────────────────────────────
    bus = EventBus()
    set_event_bus(bus)

    # ── Launch dashboard if requested ─────────────────────────
    if args.dashboard:
        from .dashboard import start_dashboard_thread
        start_dashboard_thread(bus, port=args.dashboard_port)
        console.print(
            f"[bold cyan]Dashboard:[/] http://localhost:{args.dashboard_port}\n"
        )

    # Derive project directory from name/idea, create it
    project_slug = _derive_project_slug(args.project, args.idea)
    project_dir = str(Path("./projects") / project_slug)
    Path(project_dir).mkdir(parents=True, exist_ok=True)

    # Ensure output and state directories exist
    Path(args.output).mkdir(parents=True, exist_ok=True)
    Path(args.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)

    # ── Register event sinks ──────────────────────────────────
    # 1. File sink: structured JSONL log in output dir (always on)
    bus.subscribe(create_file_sink(args.output))

    # 2. Supabase sink: persist events for UI querying (best-effort)
    bus.subscribe(create_supabase_sink(db))

    # 3. Console sink: real-time CLI output (when --stream)
    if args.stream:
        bus.subscribe(_create_console_sink(console))

    mode = "autonomous" if args.autonomous else "supervised"

    # Print banner
    console.print(
        Panel(
            f"[bold]Project:[/] {args.project}\n"
            f"[bold]Project dir:[/] {project_dir}\n"
            f"[bold]Ideas:[/] {len(args.idea)}\n"
            f"[bold]Mode:[/] {mode}\n"
            f"[bold]Workflow:[/] {args.workflow}\n"
            f"[bold]Artifacts:[/] {args.output}\n"
            f"[bold]Event log:[/] {args.output}/pipeline_events.jsonl",
            title="[bold cyan]Idea-to-Production Pipeline[/]",
            border_style="cyan",
        )
    )

    # Create pipeline run in Supabase
    run_record = db.create_run(
        {
            "workflow_id": args.workflow,
            "project_name": args.project,
            "ideas": args.idea,
            "mode": mode,
            "status": "running",
            "current_stage": "brainstorm",
        }
    )
    run_id = run_record["id"]
    console.print(f"[dim]Run ID: {run_id}[/]\n")

    # Emit pipeline start event
    bus.emit(PipelineEvent(
        event_type=EventType.PIPELINE_START,
        run_id=run_id,
        data={
            "project_name": args.project,
            "project_dir": project_dir,
            "ideas": args.idea,
            "mode": mode,
            "workflow": args.workflow,
        },
    ))

    # Build the graph
    graph = build_graph(checkpoint_path=args.checkpoint_db)

    # Initial state
    initial_state = {
        "project_name": args.project,
        "project_dir": project_dir,
        "ideas": args.idea,
        "output_dir": args.output,
        "workflow_id": args.workflow,
        "run_id": run_id,
        "mode": mode,
        "current_stage": "brainstorm",
        "current_phase": 0,
        "total_phases": 4,
        "current_task_index": 0,
        "total_tasks": 0,
        "awaiting_approval": False,
        "phases": [],
        "completed_tasks": [],
        "attempt_count": 0,
        "max_attempts": 5,
        "messages": [],
        "step_count": 0,
    }

    config = {"configurable": {"thread_id": f"{args.project}-{run_id}"}}

    try:
        if args.stream:
            _run_streaming(graph, initial_state, config, run_id, bus)
        else:
            _run_blocking(graph, initial_state, config, run_id, bus)
    except KeyboardInterrupt:
        console.print("\n[yellow]Pipeline interrupted by user.[/]")
        db.update_run(run_id, {"status": "paused"})
        bus.emit(PipelineEvent(
            event_type=EventType.PIPELINE_ERROR,
            run_id=run_id,
            data={"error": "Interrupted by user", "error_type": "KeyboardInterrupt"},
        ))
        bus.close()
        sys.exit(1)
    except Exception as exc:
        console.print(f"\n[red]Pipeline error: {exc}[/]")
        db.update_run(run_id, {"status": "failed", "error": str(exc)})
        bus.emit(PipelineEvent(
            event_type=EventType.PIPELINE_ERROR,
            run_id=run_id,
            data={"error": str(exc), "error_type": type(exc).__name__},
        ))
        bus.close()
        raise


def _run_streaming(graph, initial_state: dict, config: dict, run_id: str, bus: EventBus) -> None:
    """Stream execution, printing each node's output as it completes."""
    for event in graph.stream(initial_state, config=config):
        for node_name, state_update in event.items():
            console.print(f"\n[bold cyan]{'─' * 60}[/]")
            console.print(f"[bold cyan]  Node: {node_name}[/]")
            console.print(f"[bold cyan]{'─' * 60}[/]")

            messages = state_update.get("messages", [])
            for msg in messages[-1:]:
                console.print(f"  {msg}")

            stage = state_update.get("current_stage", "")
            if stage:
                console.print(f"  [dim]Stage: {stage}[/]")

    db.update_run(run_id, {"status": "complete"})
    bus.emit(PipelineEvent(
        event_type=EventType.PIPELINE_COMPLETE,
        run_id=run_id,
    ))
    bus.close()
    console.print("\n[bold green]Pipeline complete.[/]")


def _run_blocking(graph, initial_state: dict, config: dict, run_id: str, bus: EventBus) -> None:
    """Block until the full pipeline completes."""
    console.print("[dim]Running pipeline (blocking mode)...[/]\n")
    final_state = graph.invoke(initial_state, config=config)

    db.update_run(run_id, {"status": "complete"})
    bus.emit(PipelineEvent(
        event_type=EventType.PIPELINE_COMPLETE,
        run_id=run_id,
        data={"steps_executed": final_state.get("step_count", 0)},
    ))
    bus.close()

    # Print summary
    console.print("\n[bold green]Pipeline complete.[/]\n")

    table = Table(title="Pipeline Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Steps executed", str(final_state.get("step_count", 0)))
    table.add_row("Tasks completed", str(len(final_state.get("completed_tasks", []))))
    table.add_row("Final stage", final_state.get("current_stage", "unknown"))

    deployment = final_state.get("deployment_manifest", {})
    if deployment:
        table.add_row("Deploy status", deployment.get("status", "N/A"))
        table.add_row("URL", deployment.get("url", "N/A"))

    console.print(table)

    # Print messages
    for msg in final_state.get("messages", []):
        console.print(f"  {msg}")

    console.print(f"\n  [dim]Full event log: {initial_state['output_dir']}/pipeline_events.jsonl[/]")


# ── Console event sink for --stream mode ─────────────────────

_EVENT_STYLES = {
    EventType.NODE_START: ("bold cyan", ">>"),
    EventType.NODE_COMPLETE: ("bold green", "OK"),
    EventType.NODE_ERROR: ("bold red", "ERR"),
    EventType.NODE_SKIP: ("dim", "SKIP"),
    EventType.LLM_CALL_START: ("dim", "LLM"),
    EventType.LLM_CALL_COMPLETE: ("", "LLM"),
    EventType.LLM_CALL_ERROR: ("bold red", "LLM ERR"),
    EventType.GATE_EVALUATED: ("", "GATE"),
    EventType.GATE_ERROR: ("bold yellow", "GATE ERR"),
    EventType.JSON_PARSE_ERROR: ("bold yellow", "PARSE ERR"),
    EventType.MEMORY_SAVED: ("dim green", "MEM"),
    EventType.MEMORY_SAVE_ERROR: ("yellow", "MEM ERR"),
    EventType.MEMORY_LOAD_ERROR: ("yellow", "MEM ERR"),
    EventType.GIT_COMMIT: ("dim", "GIT"),
    EventType.GIT_ERROR: ("yellow", "GIT ERR"),
    EventType.DB_ERROR: ("yellow", "DB ERR"),
    EventType.FABRICATION_DETECTED: ("bold red", "FABRICATION"),
    EventType.MCP_RESOLVED: ("dim green", "MCP"),
    EventType.MCP_UNAVAILABLE: ("bold yellow", "MCP MISS"),
    EventType.PIPELINE_START: ("bold cyan", "START"),
    EventType.PIPELINE_COMPLETE: ("bold green", "DONE"),
    EventType.PIPELINE_ERROR: ("bold red", "FAIL"),
}


def _create_console_sink(rich_console: Console) -> EventHandler:
    """Create a console event handler for real-time CLI output."""

    def _sink(event: PipelineEvent) -> None:
        style, prefix = _EVENT_STYLES.get(event.event_type, ("", "?"))
        agent = event.agent_id or ""
        step = f"#{event.step_number}" if event.step_number else ""
        duration = f" ({event.duration_ms}ms)" if event.duration_ms else ""

        # Build a concise one-line summary based on event type
        detail = ""
        if event.event_type == EventType.NODE_START:
            detail = ""
        elif event.event_type == EventType.NODE_COMPLETE:
            passed = event.data.get("gates_passed")
            detail = "gates passed" if passed else "gates FAILED" if passed is not None else ""
        elif event.event_type == EventType.LLM_CALL_START:
            detail = f"{event.data.get('execution_type', '')} / {event.data.get('model', '')}"
        elif event.event_type == EventType.LLM_CALL_COMPLETE:
            usage = event.data.get("usage", {})
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            detail = f"{tokens} tokens" if tokens else ""
        elif event.event_type == EventType.GATE_EVALUATED:
            gid = event.data.get("gate_id", "")
            passed = event.data.get("passed")
            detail = f"{gid}: {'PASS' if passed else 'FAIL'}"
        elif event.event_type == EventType.GATE_ERROR:
            detail = f"{event.data.get('gate_id', '')}: {event.data.get('error', '')[:80]}"
        elif event.event_type == EventType.JSON_PARSE_ERROR:
            detail = f"raw len={event.data.get('raw_content_length', '?')}"
        elif event.event_type in (EventType.NODE_ERROR, EventType.LLM_CALL_ERROR,
                                   EventType.DB_ERROR, EventType.GIT_ERROR,
                                   EventType.PIPELINE_ERROR):
            detail = event.data.get("error", "")[:100]
        elif event.event_type == EventType.MCP_RESOLVED:
            detail = f"{event.data.get('server', '')} via {event.data.get('provider', '')}"
        elif event.event_type == EventType.MCP_UNAVAILABLE:
            detail = event.data.get("server", "")
        elif event.event_type == EventType.FABRICATION_DETECTED:
            detail = event.data.get("description", "")[:100]

        line = f"  [{style}][{prefix}][/{style}] {agent}{step}{duration}"
        if detail:
            line += f"  {detail}"

        rich_console.print(line)

    return _sink


if __name__ == "__main__":
    main()
