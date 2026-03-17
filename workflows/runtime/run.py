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
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import db
from .graph import build_graph

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
        help="Stream step-by-step output",
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
    args = parser.parse_args()

    # Ensure output and state directories exist
    Path(args.output).mkdir(parents=True, exist_ok=True)
    Path(args.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)

    mode = "autonomous" if args.autonomous else "supervised"

    # Print banner
    console.print(
        Panel(
            f"[bold]Project:[/] {args.project}\n"
            f"[bold]Ideas:[/] {len(args.idea)}\n"
            f"[bold]Mode:[/] {mode}\n"
            f"[bold]Workflow:[/] {args.workflow}\n"
            f"[bold]Output:[/] {args.output}",
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

    # Build the graph
    graph = build_graph(checkpoint_path=args.checkpoint_db)

    # Initial state
    initial_state = {
        "project_name": args.project,
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
            _run_streaming(graph, initial_state, config, run_id)
        else:
            _run_blocking(graph, initial_state, config, run_id)
    except KeyboardInterrupt:
        console.print("\n[yellow]Pipeline interrupted by user.[/]")
        db.update_run(run_id, {"status": "paused"})
        sys.exit(1)
    except Exception as exc:
        console.print(f"\n[red]Pipeline error: {exc}[/]")
        db.update_run(run_id, {"status": "failed", "error": str(exc)})
        raise


def _run_streaming(graph, initial_state: dict, config: dict, run_id: str) -> None:
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
    console.print("\n[bold green]Pipeline complete.[/]")


def _run_blocking(graph, initial_state: dict, config: dict, run_id: str) -> None:
    """Block until the full pipeline completes."""
    console.print("[dim]Running pipeline (blocking mode)...[/]\n")
    final_state = graph.invoke(initial_state, config=config)

    db.update_run(run_id, {"status": "complete"})

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


if __name__ == "__main__":
    main()
