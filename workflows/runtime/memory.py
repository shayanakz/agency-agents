"""Hybrid file-based memory with Supabase index.

Memory files are markdown with YAML frontmatter, stored on disk.
Supabase `memory_index` table provides queryability.

Files are the source of truth — human-readable, git-friendly, Claude Code native.
The DB index enables fast lookups by project, agent, and tags.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .events import EventType, PipelineEvent, get_event_bus

logger = logging.getLogger("pipeline.memory")

# ── File I/O ────────────────────────────────────────────────────


def _next_sequence(directory: Path) -> int:
    """Get the next sequence number for memory files in a directory."""
    if not directory.exists():
        return 1
    existing = sorted(directory.glob("*.md"))
    if not existing:
        return 1
    # Extract sequence from filename like "003-brainstorm-decision.md"
    for f in reversed(existing):
        match = re.match(r"^(\d+)-", f.name)
        if match:
            return int(match.group(1)) + 1
    return len(existing) + 1


def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return slug.strip("-")[:50]


def save_memory(
    output_dir: str,
    memory_type: str,
    agent_id: str,
    content: str,
    summary: str,
    project_name: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """Save a memory as a markdown file and index it in Supabase.

    Args:
        output_dir: Base artifacts directory.
        memory_type: "project" or "agent".
        agent_id: Which agent created this memory.
        content: The memory content (markdown body).
        summary: One-line summary for the index.
        project_name: Project name (required for project memories).
        tags: Optional tags for filtering.

    Returns:
        The file path of the created memory, or None on failure.
    """
    tags = tags or []
    now = datetime.now(timezone.utc)
    base = Path(output_dir) / "memory"

    if memory_type == "project" and project_name:
        directory = base / "projects" / _slugify(project_name)
    elif memory_type == "agent":
        directory = base / "agents" / _slugify(agent_id)
    else:
        return None

    directory.mkdir(parents=True, exist_ok=True)

    seq = _next_sequence(directory)
    slug = _slugify(summary)
    filename = f"{seq:03d}-{slug}.md"
    filepath = directory / filename

    # Build frontmatter
    frontmatter_lines = [
        "---",
        f"type: {memory_type}",
    ]
    if project_name:
        frontmatter_lines.append(f"project: {project_name}")
    frontmatter_lines.extend([
        f"agent: {agent_id}",
        f"tags: [{', '.join(tags)}]",
        f"summary: {summary}",
        f"created: {now.isoformat()}",
        "---",
    ])
    frontmatter = "\n".join(frontmatter_lines)

    file_content = f"{frontmatter}\n\n{content}\n"
    filepath.write_text(file_content, encoding="utf-8")

    # Index in Supabase — file is the source of truth, DB is for querying
    relative_path = str(filepath.relative_to(Path(output_dir)))
    try:
        db.index_memory(
            file_path=relative_path,
            memory_type=memory_type,
            project_name=project_name,
            agent_id=agent_id,
            tags=tags,
            summary=summary,
        )
    except Exception as exc:
        # File is saved even if indexing fails, but we emit an event
        # so the issue is visible rather than silently swallowed
        logger.warning("Memory indexing failed for %s: %s", relative_path, exc)
        get_event_bus().emit(PipelineEvent(
            event_type=EventType.MEMORY_SAVE_ERROR,
            agent_id=agent_id,
            data={
                "operation": "index_memory",
                "file_path": relative_path,
                "error": str(exc),
            },
        ))

    return str(filepath)


# ── Memory Loading ──────────────────────────────────────────────


def load_project_memories(
    output_dir: str, project_name: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Load project memories from disk.

    Tries Supabase index first for file paths, falls back to directory scan.
    """
    memories = []

    # Try DB index first
    try:
        indexed = db.query_project_memories(project_name, limit=limit)
        for entry in indexed:
            filepath = Path(output_dir) / entry["file_path"]
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8")
                memories.append({
                    "file_path": entry["file_path"],
                    "summary": entry.get("summary", ""),
                    "tags": entry.get("tags", []),
                    "content": _extract_body(content),
                })
        if memories:
            return memories[:limit]
    except Exception as exc:
        logger.debug("DB memory index unavailable for project %s, falling back to disk: %s", project_name, exc)

    # Fallback: scan directory
    project_dir = Path(output_dir) / "memory" / "projects" / _slugify(project_name)
    if project_dir.exists():
        for f in sorted(project_dir.glob("*.md"))[-limit:]:
            content = f.read_text(encoding="utf-8")
            frontmatter = _parse_frontmatter(content)
            memories.append({
                "file_path": str(f.relative_to(Path(output_dir))),
                "summary": frontmatter.get("summary", f.stem),
                "tags": frontmatter.get("tags", []),
                "content": _extract_body(content),
            })

    return memories[:limit]


def load_agent_memories(
    output_dir: str, agent_id: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Load agent-scoped memories from disk."""
    memories = []

    # Try DB index first
    try:
        indexed = db.query_agent_memories(agent_id, limit=limit)
        for entry in indexed:
            filepath = Path(output_dir) / entry["file_path"]
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8")
                memories.append({
                    "file_path": entry["file_path"],
                    "summary": entry.get("summary", ""),
                    "tags": entry.get("tags", []),
                    "content": _extract_body(content),
                })
        if memories:
            return memories[:limit]
    except Exception as exc:
        logger.debug("DB memory index unavailable for agent %s, falling back to disk: %s", agent_id, exc)

    # Fallback: scan directory
    agent_dir = Path(output_dir) / "memory" / "agents" / _slugify(agent_id)
    if agent_dir.exists():
        for f in sorted(agent_dir.glob("*.md"))[-limit:]:
            content = f.read_text(encoding="utf-8")
            frontmatter = _parse_frontmatter(content)
            memories.append({
                "file_path": str(f.relative_to(Path(output_dir))),
                "summary": frontmatter.get("summary", f.stem),
                "tags": frontmatter.get("tags", []),
                "content": _extract_body(content),
            })

    return memories[:limit]


# ── Prompt Formatting ───────────────────────────────────────────


def format_memories_for_prompt(
    project_memories: list[dict], agent_memories: list[dict]
) -> str:
    """Format memories as markdown for injection into system prompt."""
    parts = []

    if project_memories:
        parts.append("## Project Context (from previous work on this project)")
        for m in project_memories:
            tags_str = f" [{', '.join(m.get('tags', []))}]" if m.get("tags") else ""
            parts.append(f"- **{m['summary']}**{tags_str}")
            body = m.get("content", "").strip()
            if body:
                # Indent body under the bullet
                for line in body.split("\n")[:5]:  # Cap at 5 lines per memory
                    parts.append(f"  {line}")

    if agent_memories:
        parts.append("\n## Lessons Learned (from your experience across all projects)")
        for m in agent_memories:
            tags_str = f" [{', '.join(m.get('tags', []))}]" if m.get("tags") else ""
            parts.append(f"- **{m['summary']}**{tags_str}")
            body = m.get("content", "").strip()
            if body:
                for line in body.split("\n")[:5]:
                    parts.append(f"  {line}")

    return "\n".join(parts)


# ── Memory Save Heuristics ──────────────────────────────────────


def extract_memories_from_output(
    agent_id: str, parsed_output: dict[str, Any], gates_passed: bool
) -> list[dict[str, Any]]:
    """Determine what memories to save based on agent output.

    Returns a list of memory dicts with: memory_type, summary, content, tags.
    """
    memories = []

    # Brainstorm: save the spec decisions (tech stack + out_of_scope boundary)
    if agent_id == "brainstorm_agent":
        brief = parsed_output.get("research_brief", {})
        if isinstance(brief, dict):
            tech = brief.get("tech_stack") or brief.get("technical_recommendations", "")
            if tech:
                memories.append({
                    "memory_type": "project",
                    "summary": f"Tech stack: {json.dumps(tech)[:80]}",
                    "content": json.dumps(tech, indent=2) if isinstance(tech, dict) else str(tech),
                    "tags": ["architecture", "stack-decision"],
                })
            out_of_scope = brief.get("out_of_scope", [])
            if out_of_scope:
                memories.append({
                    "memory_type": "project",
                    "summary": f"Out of scope: {', '.join(out_of_scope[:3])}",
                    "content": "\n".join(f"- {item}" for item in out_of_scope),
                    "tags": ["scope", "out-of-scope"],
                })

    # Planning: save architecture decisions
    elif agent_id == "planning_agent":
        arch = parsed_output.get("architecture", {})
        if isinstance(arch, dict) and arch.get("tech_stack"):
            memories.append({
                "memory_type": "project",
                "summary": f"Architecture: {json.dumps(arch.get('tech_stack', {}))[:80]}",
                "content": json.dumps(arch, indent=2),
                "tags": ["architecture", "planning"],
            })

    # Reality check: save patterns (especially failures)
    elif agent_id == "reality_check_agent":
        verdict = parsed_output.get("reality_verdict", parsed_output)
        if isinstance(verdict, dict):
            status = verdict.get("status", "")
            issues = verdict.get("issues", [])
            if status == "NEEDS_WORK" and issues:
                for issue in issues[:3]:  # Cap at 3 lessons per check
                    if isinstance(issue, dict) and issue.get("severity") in ("critical", "high"):
                        memories.append({
                            "memory_type": "agent",
                            "summary": f"Pattern: {issue.get('description', 'unknown')[:80]}",
                            "content": json.dumps(issue, indent=2),
                            "tags": ["pattern", f"severity-{issue.get('severity', 'unknown')}"],
                        })

    # Gate failure: save as agent lesson
    if not gates_passed:
        memories.append({
            "memory_type": "agent",
            "summary": f"Gate failure in {agent_id}",
            "content": f"Agent {agent_id} failed gate evaluation. Output keys: {list(parsed_output.keys())[:10]}",
            "tags": ["gate-failure", "lesson"],
        })

    return memories


# ── Frontmatter Parsing ─────────────────────────────────────────


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a markdown file."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    fm = {}
    for line in parts[1].strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip()
            # Parse simple arrays like [tag1, tag2]
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            fm[key.strip()] = value
    return fm


def _extract_body(content: str) -> str:
    """Extract the body (after frontmatter) from a markdown file."""
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    return parts[2].strip()
