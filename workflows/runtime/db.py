"""Supabase database operations via REST API.

Uses plain urllib — no supabase Python package dependency.
All configuration (agents, personas, workflows, edges) and runtime state
(pipeline_runs, run_steps, handoffs, audit logs) are stored in Supabase.
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any


# ── HTTP client ─────────────────────────────────────────────────

_base_url: str | None = None
_headers: dict[str, str] | None = None


def _get_config() -> tuple[str, dict[str, str]]:
    """Get cached Supabase URL and headers."""
    global _base_url, _headers
    if _base_url is not None and _headers is not None:
        return _base_url, _headers

    _base_url = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
    key = os.environ.get(
        "SUPABASE_SERVICE_KEY",
        os.environ.get("SUPABASE_ANON_KEY", ""),
    )
    if not key:
        raise RuntimeError(
            "Set SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY environment variable"
        )
    _headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    return _base_url, _headers


def _get(table: str, params: str = "") -> list[dict]:
    """GET from Supabase REST API."""
    base_url, headers = _get_config()
    url = f"{base_url}/rest/v1/{table}?{params}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _get_single(table: str, params: str) -> dict:
    """GET a single row from Supabase REST API."""
    results = _get(table, params)
    if not results:
        raise ValueError(f"No rows found in {table} with {params}")
    return results[0]


def _post(table: str, data: dict) -> dict:
    """POST (insert) to Supabase REST API. Strips None values."""
    base_url, headers = _get_config()
    clean = {k: v for k, v in data.items() if v is not None}
    url = f"{base_url}/rest/v1/{table}"
    body = json.dumps(clean, default=str).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            return result[0] if isinstance(result, list) else result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        raise RuntimeError(
            f"Supabase POST to {table} failed ({e.code}): {error_body}"
        ) from e


def _patch(table: str, params: str, data: dict) -> None:
    """PATCH (update) rows in Supabase REST API."""
    base_url, headers = _get_config()
    clean = {k: v for k, v in data.items() if v is not None}
    url = f"{base_url}/rest/v1/{table}?{params}"
    body = json.dumps(clean, default=str).encode()
    patch_headers = {**headers, "Prefer": "return=minimal"}
    req = urllib.request.Request(url, data=body, headers=patch_headers, method="PATCH")
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        raise RuntimeError(
            f"Supabase PATCH to {table} failed ({e.code}): {error_body}"
        ) from e


# ── Configuration reads ─────────────────────────────────────────


def load_agent(agent_id: str) -> dict:
    """Load an agent definition by ID."""
    return _get_single("agents", f"id=eq.{agent_id}&select=*")


def load_persona(persona_id: str) -> dict:
    """Load a persona definition by ID."""
    return _get_single("personas", f"id=eq.{persona_id}&select=*")


def load_type(type_id: str) -> dict:
    """Load a type definition by ID."""
    return _get_single("types", f"id=eq.{type_id}&select=*")


def load_workflow(workflow_id: str) -> dict:
    """Load a workflow definition by ID."""
    return _get_single("workflows", f"id=eq.{workflow_id}&select=*")


def load_workflow_phases(workflow_id: str) -> list[dict]:
    """Load all phases for a workflow, ordered by phase_order."""
    return _get("workflow_phases", f"workflow_id=eq.{workflow_id}&select=*&order=phase_order")


def load_workflow_edges(workflow_id: str) -> list[dict]:
    """Load all edges for a workflow, ordered by priority (desc)."""
    return _get("workflow_edges", f"workflow_id=eq.{workflow_id}&select=*&order=priority.desc")


def load_persona_overrides(agent_id: str) -> list[dict]:
    """Load dynamic persona overrides for an agent."""
    return _get("persona_overrides", f"agent_id=eq.{agent_id}&select=*&order=priority.desc")


# ── Runtime writes ──────────────────────────────────────────────


def create_run(run_data: dict) -> dict:
    """Create a new pipeline run record."""
    return _post("pipeline_runs", run_data)


def update_run(run_id: str, updates: dict) -> None:
    """Update a pipeline run record."""
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    _patch("pipeline_runs", f"id=eq.{run_id}", updates)


def create_step(step_data: dict) -> dict:
    """Create a new run step record."""
    return _post("run_steps", step_data)


def update_step(step_id: str, updates: dict) -> None:
    """Update a run step record."""
    _patch("run_steps", f"id=eq.{step_id}", updates)


def create_handoff(handoff_data: dict) -> dict:
    """Record an agent-to-agent handoff."""
    return _post("handoffs", handoff_data)


def create_artifact(artifact_data: dict) -> dict:
    """Record a produced artifact."""
    return _post("artifacts", artifact_data)


def log_llm_call(log_data: dict) -> dict:
    """Log an LLM invocation to the audit trail."""
    return _post("llm_audit_log", log_data)


def create_approval_request(run_id: str, step_id: str | None = None) -> dict:
    """Create a pending approval request."""
    data: dict[str, Any] = {"run_id": run_id, "status": "pending"}
    if step_id:
        data["step_id"] = step_id
    return _post("approvals", data)


def get_approval(approval_id: str) -> dict:
    """Get the current status of an approval request."""
    return _get_single("approvals", f"id=eq.{approval_id}&select=*")


def approve_request(approval_id: str, decided_by: str = "human", notes: str = "") -> None:
    """Approve a pending approval request."""
    _patch("approvals", f"id=eq.{approval_id}", {
        "status": "approved",
        "decided_by": decided_by,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    })


# ── Memory index ────────────────────────────────────────────────


def index_memory(
    file_path: str,
    memory_type: str,
    project_name: str | None,
    agent_id: str | None,
    tags: list[str] | None,
    summary: str,
) -> dict:
    """Index a memory file in the database."""
    return _post("memory_index", {
        "file_path": file_path,
        "memory_type": memory_type,
        "project_name": project_name,
        "agent_id": agent_id,
        "tags": tags or [],
        "summary": summary,
    })


def query_project_memories(project_name: str, limit: int = 10) -> list[dict]:
    """Query memory index for a project's memories."""
    return _get(
        "memory_index",
        f"project_name=eq.{project_name}&memory_type=eq.project"
        f"&select=file_path,summary,tags,created_at"
        f"&order=created_at.desc&limit={limit}",
    )


def query_agent_memories(agent_id: str, limit: int = 10) -> list[dict]:
    """Query memory index for an agent's global memories."""
    return _get(
        "memory_index",
        f"agent_id=eq.{agent_id}&memory_type=eq.agent"
        f"&select=file_path,summary,tags,created_at"
        f"&order=created_at.desc&limit={limit}",
    )
