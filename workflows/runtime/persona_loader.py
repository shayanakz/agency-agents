"""Persona loader and system prompt builder.

Reads persona config from Supabase, loads the full Agency .md file from disk,
and constructs the 5-layer system prompt for each agent invocation.
"""

import json
from pathlib import Path
from typing import Any

from . import db

# Resolve to agency-agents/ root (runtime/ → workflows/ → agency-agents/)
REPO_ROOT = Path(__file__).parent.parent.parent


def load_agency_markdown(source: str) -> str:
    """Read the full Agency .md file from the repo.

    Args:
        source: Repo-relative path, e.g. "testing/testing-reality-checker.md"
    """
    path = REPO_ROOT / source
    if not path.exists():
        raise FileNotFoundError(
            f"Agency persona file not found: {path}\n"
            f"Expected at repo root: {REPO_ROOT}"
        )
    return path.read_text(encoding="utf-8")


def resolve_persona(agent: dict, state: dict[str, Any]) -> dict:
    """Resolve the persona for an agent, handling dynamic persona_selector.

    If the agent has a static persona_id, use that.
    If it has a persona_selector, evaluate rules against state to pick one.
    """
    # Static persona
    if agent.get("persona_id"):
        return db.load_persona(agent["persona_id"])

    # Dynamic persona selector
    selector = agent.get("persona_selector")
    if not selector:
        raise ValueError(
            f"Agent {agent['id']} has no persona_id and no persona_selector"
        )

    rules = selector.get("rules", [])
    default_id = selector.get("default", "senior-developer")

    # Build a simple evaluation context from state
    context = _build_eval_context(state)

    for rule in rules:
        condition = rule.get("condition", "")
        try:
            if eval(condition, {"__builtins__": {}}, context):  # noqa: S307
                return db.load_persona(rule["persona_id"])
        except Exception:
            continue  # skip unparseable rules

    return db.load_persona(default_id)


def format_output_schema(outputs: list[dict]) -> str:
    """Generate a human-readable output schema description for the system prompt."""
    lines = ["You MUST return your response as a JSON object with these keys:\n"]
    for output in outputs:
        name = output.get("name", "unknown")
        type_name = output.get("type", "any")
        validations = output.get("validation", [])
        lines.append(f"- **{name}** (type: {type_name})")
        for v in validations:
            field = v.get("field", "")
            rule = v.get("rule", "")
            value = v.get("value", v.get("values", ""))
            lines.append(f"  - {field}: {rule} {value}")
    return "\n".join(lines)


def build_system_prompt(
    agent: dict, persona: dict, memory_context: str = ""
) -> str:
    """Construct the full system prompt by combining 6 layers.

    Layer 1: Agent role description (what to do)
    Layer 2: Full Agency .md file content (who you are)
    Layer 3: Persona system_prompt_overlay (how to behave)
    Layer 4: Agent guardrails (what you must NOT do)
    Layer 5: Memory context (what you've learned)
    Layer 6: Output JSON schema (output format)
    """
    parts: list[str] = []

    # Layer 1: Agent role
    role = agent.get("role", "")
    if role:
        parts.append(f"# Your Role\n\n{role}")

    # Layer 2: Full Agency persona markdown
    source = persona.get("source", "")
    if source:
        try:
            agency_md = load_agency_markdown(source)
            parts.append(f"# Your Personality & Expertise\n\n{agency_md}")
        except FileNotFoundError:
            parts.append(
                f"# Your Personality & Expertise\n\n"
                f"(Persona file not found: {source})"
            )

    # Layer 3: Persona behavioral overlay
    overlay = persona.get("system_prompt_overlay", "")
    if overlay:
        parts.append(f"# Behavioral Rules\n\n{overlay}")

    # Layer 4: Agent guardrails
    guardrails = agent.get("guardrails", [])
    if guardrails:
        rules = "\n".join(f"- {g}" for g in guardrails)
        parts.append(f"# Non-Negotiable Guardrails\n\n{rules}")

    # Layer 5: Memory context (from previous work)
    if memory_context:
        parts.append(f"# Context from Previous Work\n\n{memory_context}")

    # Layer 6: Output format
    outputs = agent.get("outputs", [])
    if outputs:
        output_desc = format_output_schema(outputs)
        parts.append(
            f"# Required Output Format\n\n{output_desc}\n\n"
            f"Return ONLY valid JSON. Do not wrap in markdown code fences."
        )

    return "\n\n---\n\n".join(parts)


def _build_eval_context(state: dict[str, Any]) -> dict[str, Any]:
    """Build a safe evaluation context from pipeline state for persona selector rules."""
    context: dict[str, Any] = {}

    # Expose current_task for category-based persona selection
    # Always provide a dict so .get() calls in predicates don't fail
    current_task = state.get("current_task")
    if isinstance(current_task, dict):
        context["current_task"] = current_task
    else:
        context["current_task"] = {}

    # Expose basic state fields
    for key in ("current_stage", "current_phase", "mode"):
        context[key] = state.get(key)

    return context
