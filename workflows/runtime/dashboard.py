"""Real-time pipeline dashboard.

Serves a web UI where users submit ideas via the browser.
The pipeline runs in a background thread and streams events
to the UI in real-time via SSE.

Launch:
    # From workflows/ directory:
    python -m runtime.dashboard

    # Or via the launch script:
    ./scripts/launch-dashboard.sh
"""

import asyncio
import logging
import queue
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import db
from .events import (
    EventBus,
    EventType,
    PipelineEvent,
    create_file_sink,
    create_supabase_sink,
    get_event_bus,
    register_cancel_flag,
    remove_cancel_flag,
    request_cancel,
    set_event_bus,
)

logger = logging.getLogger("pipeline.dashboard")

app = FastAPI(title="Pipeline Dashboard", docs_url=None, redoc_url=None)

_DASHBOARD_HTML = Path(__file__).parent / "dashboard.html"

# Track active pipeline threads so we can report status
_active_runs: dict[str, threading.Thread] = {}
_active_runs_lock = threading.Lock()


# ── SSE endpoint ─────────────────────────────────────────────

@app.get("/sse")
async def sse_stream(request: Request):
    """Server-Sent Events stream of live pipeline events."""
    bus = get_event_bus()
    q = bus.create_sse_queue()

    async def event_generator():
        loop = asyncio.get_event_loop()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await loop.run_in_executor(
                        None, _queue_get_with_timeout, q, 1.0
                    )
                    if event is None:
                        yield "event: close\ndata: {}\n\n"
                        break
                    yield event.to_sse()
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            bus.remove_sse_queue(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _queue_get_with_timeout(q: queue.Queue, timeout: float) -> PipelineEvent | None:
    return q.get(block=True, timeout=timeout)


# ── Pipeline launch endpoint ─────────────────────────────────

class RunRequest(BaseModel):
    project: str
    idea: str
    mode: str = "autonomous"  # autonomous | supervised
    # Model overrides: {"__all__": {"model": "sonnet"}} or per-agent
    # {"brainstorm_agent": {"model": "opus"}, "implement_agent": {"model": "sonnet"}}
    model_overrides: dict[str, dict[str, str]] | None = None


@app.post("/api/runs")
async def launch_run(req: RunRequest):
    """Launch a new pipeline run from the UI.

    Starts the pipeline in a background thread so the SSE stream
    can deliver events to the browser in real-time.
    """
    bus = get_event_bus()
    project = req.project.strip() or "Untitled"
    idea = req.idea.strip()
    if not idea:
        return JSONResponse({"error": "Idea is required"}, status_code=400)

    mode = req.mode if req.mode in ("autonomous", "supervised") else "autonomous"
    model_overrides = req.model_overrides or {}

    # Derive a project slug
    def slugify(text: str, max_words: int = 5) -> str:
        words = re.sub(r"[^\w\s-]", "", text.lower()).split()[:max_words]
        return "-".join(words) if words else ""

    slug = slugify(project)
    if not slug or slug in ("project", "app", "untitled", "my-project"):
        slug = slugify(idea, max_words=4) or ("project-" + uuid.uuid4().hex[:6])

    project_dir = str(Path("./projects") / slug)
    output_dir = "./artifacts"
    checkpoint_db = "./state/checkpoints.db"

    Path(project_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(checkpoint_db).parent.mkdir(parents=True, exist_ok=True)

    # Create run record in Supabase (or generate local ID if DB unavailable)
    try:
        run_record = db.create_run({
            "workflow_id": "idea-to-production",
            "project_name": project,
            "ideas": [idea],
            "mode": mode,
            "status": "running",
            "current_stage": "brainstorm",
        })
        run_id = run_record["id"]
    except Exception as exc:
        # Supabase unavailable — generate a local run ID and continue.
        # The pipeline will still work; events just won't persist to DB.
        run_id = str(uuid.uuid4())
        logger.warning(
            "Supabase unavailable, using local run_id %s: %s", run_id, exc
        )

    # Emit pipeline start event
    bus.emit(PipelineEvent(
        event_type=EventType.PIPELINE_START,
        run_id=run_id,
        data={
            "project_name": project,
            "project_dir": project_dir,
            "ideas": [idea],
            "mode": mode,
            "workflow": "idea-to-production",
            "model_overrides": model_overrides,
        },
    ))

    # Register cancel flag so the UI can stop this run
    register_cancel_flag(run_id)

    # Start pipeline in background thread
    def _run_pipeline():
        try:
            from .graph import build_graph

            graph = build_graph(checkpoint_path=checkpoint_db)

            initial_state = {
                "project_name": project,
                "project_dir": project_dir,
                "ideas": [idea],
                "output_dir": output_dir,
                "workflow_id": "idea-to-production",
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
                "model_overrides": model_overrides,
                "messages": [],
                "step_count": 0,
            }

            config = {"configurable": {"thread_id": f"{project}-{run_id}"}}

            # Run the graph (blocking in this thread)
            graph.invoke(initial_state, config=config)

            try:
                db.update_run(run_id, {"status": "complete"})
            except Exception:
                pass  # DB unavailable — events still capture completion
            bus.emit(PipelineEvent(
                event_type=EventType.PIPELINE_COMPLETE,
                run_id=run_id,
                data={"project_name": project},
            ))
        except Exception as exc:
            logger.exception("Pipeline run %s failed", run_id)
            try:
                db.update_run(run_id, {"status": "failed", "error": str(exc)})
            except Exception:
                pass
            bus.emit(PipelineEvent(
                event_type=EventType.PIPELINE_ERROR,
                run_id=run_id,
                data={"error": str(exc), "error_type": type(exc).__name__},
            ))
        finally:
            with _active_runs_lock:
                _active_runs.pop(run_id, None)
            remove_cancel_flag(run_id)

    thread = threading.Thread(target=_run_pipeline, daemon=True, name=f"run-{run_id[:8]}")
    with _active_runs_lock:
        _active_runs[run_id] = thread
    thread.start()

    return JSONResponse({
        "run_id": run_id,
        "project": project,
        "idea": idea,
        "mode": mode,
        "project_dir": project_dir,
        "status": "running",
    })


# ── REST query endpoints ─────────────────────────────────────

@app.get("/api/runs")
async def list_runs():
    """List past pipeline runs, newest first."""
    try:
        runs = db._get(
            "pipeline_runs",
            "select=id,project_name,mode,status,current_stage,started_at,completed_at"
            "&order=started_at.desc&limit=50",
        )
        return JSONResponse(runs)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    try:
        run = db._get_single("pipeline_runs", f"id=eq.{run_id}&select=*")
        return JSONResponse(run)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/runs/{run_id}/steps")
async def get_run_steps(run_id: str):
    try:
        steps = db._get(
            "run_steps",
            f"run_id=eq.{run_id}&select=*&order=step_number",
        )
        return JSONResponse(steps)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/runs/{run_id}/events")
async def get_run_events(run_id: str):
    try:
        events = db._get(
            "pipeline_events",
            f"run_id=eq.{run_id}&select=*&order=timestamp",
        )
        return JSONResponse(events)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/runs/{run_id}/llm-calls")
async def get_llm_calls(run_id: str):
    try:
        calls = db._get(
            "llm_audit_log",
            f"run_id=eq.{run_id}&select=*&order=created_at",
        )
        return JSONResponse(calls)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/runs/{run_id}/errors")
async def get_run_errors(run_id: str):
    """Get errors for a specific run from the event log file.

    This is the fallback when SSE missed events — reads from the JSONL file
    which is always written regardless of Supabase availability.
    """
    import json as _json
    log_file = Path("./artifacts/pipeline_events.jsonl")
    errors = []
    if log_file.exists():
        try:
            with open(log_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if evt.get("run_id") == run_id and "error" in evt.get("event_type", ""):
                        errors.append(evt)
            return JSONResponse(errors)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse([])


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Cancel a running pipeline. Stops at next node boundary."""
    if request_cancel(run_id):
        bus = get_event_bus()
        bus.emit(PipelineEvent(
            event_type=EventType.PIPELINE_ERROR,
            run_id=run_id,
            data={"error": "Cancelled by user", "error_type": "UserCancelled"},
        ))
        return JSONResponse({"status": "cancelling", "run_id": run_id})
    return JSONResponse({"error": "Run not found or already finished"}, status_code=404)


@app.get("/api/status")
async def get_status():
    """Health check — Supabase, Claude CLI, active runs."""
    import shutil

    with _active_runs_lock:
        active = list(_active_runs.keys())

    # Check Supabase connectivity
    supabase_ok = False
    supabase_error = None
    try:
        db._get("agents", "select=id&limit=1")
        supabase_ok = True
    except Exception as exc:
        supabase_error = str(exc)

    # Check Claude CLI
    claude_ok = shutil.which("claude") is not None

    return JSONResponse({
        "active_runs": active,
        "count": len(active),
        "supabase": {"ok": supabase_ok, "error": supabase_error},
        "claude_cli": {"ok": claude_ok},
    })


# ── HTML dashboard ───────────────────────────────────────────

@app.get("/")
async def serve_dashboard():
    if _DASHBOARD_HTML.exists():
        return FileResponse(_DASHBOARD_HTML, media_type="text/html")
    return HTMLResponse("<h1>Dashboard HTML not found</h1>", status_code=404)


# ── Integration with run.py (kept for backwards compat) ──────

def start_dashboard_thread(bus: EventBus, port: int = 8787) -> threading.Thread:
    """Start the dashboard server in a daemon thread."""
    import uvicorn
    set_event_bus(bus)

    def _run_server():
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    thread = threading.Thread(target=_run_server, daemon=True, name="dashboard")
    thread.start()
    logger.info("Dashboard started at http://localhost:%d", port)
    return thread


# ── Standalone mode ──────────────────────────────────────────

def main():
    """Run the dashboard as the primary server.

    Users open the browser, type an idea, and watch it execute in real-time.
    """
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    bus = EventBus()
    set_event_bus(bus)

    # Register event sinks
    bus.subscribe(create_file_sink("./artifacts"))
    try:
        bus.subscribe(create_supabase_sink(db))
    except Exception:
        logger.warning("Supabase sink not available — events will only log to file")

    print("=" * 60)
    print("  Pipeline Dashboard")
    print("  http://localhost:8787")
    print()
    print("  Open in your browser, type an idea, and watch it build.")
    print("=" * 60)
    print()

    uvicorn.run(app, host="0.0.0.0", port=8787, log_level="info")


if __name__ == "__main__":
    main()
