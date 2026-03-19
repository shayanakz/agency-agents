"""Typed event bus for pipeline observability.

Every significant action in the pipeline emits an event. Events are:
1. Logged to Supabase (pipeline_events table) for full audit trail
2. Streamable via SSE/WebSocket to a UI for real-time visibility
3. Written to structured log files for offline replay

Events are the single source of truth for "what happened."
No more silent swallowing — if something fails, there's an event for it.
"""

import json
import logging
import time
import threading
import queue
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("pipeline.events")


class EventType(str, Enum):
    # ── Node lifecycle ──────────────────────────────────
    NODE_START = "node.start"
    NODE_COMPLETE = "node.complete"
    NODE_ERROR = "node.error"
    NODE_SKIP = "node.skip"           # e.g., code_review skips when no code

    # ── LLM calls ───────────────────────────────────────
    LLM_CALL_START = "llm.call.start"
    LLM_CALL_COMPLETE = "llm.call.complete"
    LLM_CALL_ERROR = "llm.call.error"
    LLM_PROGRESS = "llm.progress"       # intermediate progress during build

    # ── Gate evaluation ─────────────────────────────────
    GATE_EVALUATED = "gate.evaluated"
    GATE_ERROR = "gate.error"

    # ── Memory operations ───────────────────────────────
    MEMORY_SAVED = "memory.saved"
    MEMORY_SAVE_ERROR = "memory.save_error"
    MEMORY_LOAD_ERROR = "memory.load_error"

    # ── Git operations ──────────────────────────────────
    GIT_COMMIT = "git.commit"
    GIT_ERROR = "git.error"

    # ── JSON parsing ────────────────────────────────────
    JSON_PARSE_ERROR = "json.parse_error"

    # ── Pipeline lifecycle ──────────────────────────────
    PIPELINE_START = "pipeline.start"
    PIPELINE_COMPLETE = "pipeline.complete"
    PIPELINE_ERROR = "pipeline.error"

    # ── Anti-fabrication ────────────────────────────────
    FABRICATION_DETECTED = "fabrication.detected"

    # ── DB operations ───────────────────────────────────
    DB_ERROR = "db.error"

    # ── MCP server resolution ───────────────────────────
    MCP_RESOLVED = "mcp.resolved"
    MCP_UNAVAILABLE = "mcp.unavailable"


class PipelineEvent:
    """A single, immutable pipeline event."""

    __slots__ = (
        "event_type", "timestamp", "run_id", "agent_id",
        "step_number", "data", "duration_ms",
    )

    def __init__(
        self,
        event_type: EventType,
        run_id: str | None = None,
        agent_id: str | None = None,
        step_number: int | None = None,
        data: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ):
        self.event_type = event_type
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.run_id = run_id
        self.agent_id = agent_id
        self.step_number = step_number
        self.data = data or {}
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
        }
        if self.run_id is not None:
            d["run_id"] = self.run_id
        if self.agent_id is not None:
            d["agent_id"] = self.agent_id
        if self.step_number is not None:
            d["step_number"] = self.step_number
        if self.data:
            d["data"] = self.data
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    def to_sse(self) -> str:
        """Format as a Server-Sent Event line."""
        return f"event: {self.event_type.value}\ndata: {self.to_json()}\n\n"


# ── Subscriber types ──────────────────────────────────────────

EventHandler = Callable[[PipelineEvent], None]


class EventBus:
    """Central publish/subscribe event bus for pipeline observability.

    Thread-safe. Supports:
    - Synchronous handlers (called inline)
    - SSE queue for real-time UI streaming
    - Supabase persistence sink
    """

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._sse_queues: list[queue.Queue[PipelineEvent | None]] = []
        self._lock = threading.Lock()

    def subscribe(self, handler: EventHandler) -> None:
        """Register a synchronous event handler."""
        with self._lock:
            self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        with self._lock:
            self._handlers = [h for h in self._handlers if h is not handler]

    def create_sse_queue(self) -> queue.Queue[PipelineEvent | None]:
        """Create a new SSE subscriber queue. Returns a Queue that receives events.

        Send None to the queue to signal the subscriber to disconnect.
        """
        q: queue.Queue[PipelineEvent | None] = queue.Queue(maxsize=1000)
        with self._lock:
            self._sse_queues.append(q)
        return q

    def remove_sse_queue(self, q: queue.Queue) -> None:
        with self._lock:
            self._sse_queues = [sq for sq in self._sse_queues if sq is not q]

    def emit(self, event: PipelineEvent) -> None:
        """Publish an event to all subscribers."""
        # Log every event
        logger.info(
            "[%s] %s/%s: %s",
            event.event_type.value,
            event.run_id or "-",
            event.agent_id or "-",
            json.dumps(event.data, default=str)[:500],
        )

        # Notify synchronous handlers
        with self._lock:
            handlers = list(self._handlers)
            sse_queues = list(self._sse_queues)

        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.error("Event handler error: %s", exc)

        # Push to all SSE queues (non-blocking)
        for q in sse_queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                logger.warning("SSE queue full, dropping event")

    def close(self) -> None:
        """Signal all SSE subscribers to disconnect."""
        with self._lock:
            for q in self._sse_queues:
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
            self._sse_queues.clear()


# ── Supabase persistence sink ────────────────────────────────

def create_supabase_sink(db_module: Any) -> EventHandler:
    """Create an event handler that persists events to Supabase.

    Args:
        db_module: The db module (avoids circular import).
    """
    def _sink(event: PipelineEvent) -> None:
        try:
            db_module.log_pipeline_event(event.to_dict())
        except Exception as exc:
            # Log locally but never crash the pipeline over a logging failure
            logger.error("Failed to persist event to Supabase: %s", exc)

    return _sink


# ── Structured file logger sink ──────────────────────────────

def create_file_sink(log_dir: str) -> EventHandler:
    """Create an event handler that appends events to a JSONL file."""
    from pathlib import Path
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    log_file = path / "pipeline_events.jsonl"

    _file_lock = threading.Lock()

    def _sink(event: PipelineEvent) -> None:
        try:
            with _file_lock:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(event.to_json() + "\n")
        except Exception as exc:
            logger.error("Failed to write event to file: %s", exc)

    return _sink


# ── Module-level singleton ───────────────────────────────────
# Each pipeline run creates its own bus, but we provide a default
# for cases where code needs to emit events without a reference.

_default_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Get the module-level default EventBus (lazy-created)."""
    global _default_bus
    with _bus_lock:
        if _default_bus is None:
            _default_bus = EventBus()
        return _default_bus


def set_event_bus(bus: EventBus) -> None:
    """Set the module-level default EventBus (called by run.py at startup)."""
    global _default_bus
    with _bus_lock:
        _default_bus = bus


# ── Cancel flag registry ────────────────────────────────────
# Allows the dashboard to signal a running pipeline to stop.
# Each node checks the flag at entry; if set, it raises.

_cancel_flags: dict[str, threading.Event] = {}
_cancel_flags_lock = threading.Lock()


def register_cancel_flag(run_id: str) -> threading.Event:
    """Create and register a cancel flag for a pipeline run."""
    flag = threading.Event()
    with _cancel_flags_lock:
        _cancel_flags[run_id] = flag
    return flag


def get_cancel_flag(run_id: str) -> threading.Event | None:
    """Get the cancel flag for a run (returns None if not registered)."""
    with _cancel_flags_lock:
        return _cancel_flags.get(run_id)


def remove_cancel_flag(run_id: str) -> None:
    """Remove the cancel flag after a run completes."""
    with _cancel_flags_lock:
        _cancel_flags.pop(run_id, None)


def request_cancel(run_id: str) -> bool:
    """Request cancellation of a running pipeline. Returns True if flag was found."""
    with _cancel_flags_lock:
        flag = _cancel_flags.get(run_id)
    if flag:
        flag.set()
        return True
    return False
