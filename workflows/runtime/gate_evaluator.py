"""Deterministic gate evaluator.

Evaluates gate predicate expressions (stored as strings in agents.gates JSONB)
against structured agent output. This is Python evaluation, NOT LLM judgment.

Supports both dot-access (validated_idea.confidence) and dict-access
(validated_idea.get('confidence')) syntax via DotDict wrapper.
"""

from typing import Any


class GateEvaluationError(Exception):
    """Raised when a gate predicate cannot be evaluated."""


class DotDict(dict):
    """Dict subclass that supports attribute access for gate predicate evaluation.

    This allows predicates like `validated_idea.confidence >= 0.6` to work
    on plain dict data returned by LLMs.
    """

    def __getattr__(self, key: str) -> Any:
        try:
            val = self[key]
        except KeyError:
            return None
        return _to_dotdict(val)

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


def _to_dotdict(obj: Any) -> Any:
    """Recursively convert dicts and lists to DotDict-wrapped versions."""
    if isinstance(obj, DotDict):
        return obj
    if isinstance(obj, dict):
        return DotDict({k: _to_dotdict(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_dotdict(v) for v in obj]
    return obj


def _count(lst: list | Any, **kwargs: Any) -> int:
    """Count items in a list matching all key=value filters."""
    if not isinstance(lst, (list, tuple)):
        return 0
    total = 0
    for item in lst:
        match = True
        for k, v in kwargs.items():
            item_val = item.get(k) if isinstance(item, dict) else getattr(item, k, None)
            if item_val != v:
                match = False
                break
        if match:
            total += 1
    return total


def evaluate_gate(predicate: str, context: dict[str, Any]) -> bool:
    """Evaluate a single gate predicate expression.

    All dict values in context are wrapped in DotDict so both dot-access
    and .get() syntax work in predicates.

    Examples:
        "validated_idea.confidence >= 0.6"
        "len(research_brief.competitive_landscape) >= 3"
        "review_verdict.status == 'APPROVED'"
        "not any(i.get('severity') == 'critical' for i in schema_issues)"
    """
    # Wrap all context values in DotDict for dot-access support
    wrapped_context = {k: _to_dotdict(v) for k, v in context.items()}

    safe_globals: dict[str, Any] = {
        "__builtins__": {},
        "len": len,
        "all": all,
        "any": any,
        "count": _count,
        "sum": sum,
        "min": min,
        "max": max,
        "abs": abs,
        "bool": bool,
        "int": int,
        "float": float,
        "str": str,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
        "isinstance": isinstance,
        "True": True,
        "False": False,
        "None": None,
        "true": True,
        "false": False,
        "null": None,
    }
    safe_globals.update(wrapped_context)

    try:
        result = eval(predicate, safe_globals)  # noqa: S307
        return bool(result)
    except Exception as exc:
        raise GateEvaluationError(
            f"Gate predicate failed: {predicate!r} — {exc}"
        ) from exc


def evaluate_all_gates(
    gates: list[dict], outputs: dict[str, Any]
) -> dict[str, bool]:
    """Evaluate all gates for an agent.

    Args:
        gates: List of gate dicts with 'id' and 'predicate' keys.
        outputs: Flattened dict of all agent output fields.

    Returns:
        Dict mapping gate_id -> passed (bool).
    """
    results: dict[str, bool] = {}
    for gate in gates:
        gate_id = gate.get("id", "unknown")
        predicate = gate.get("predicate", "True")
        try:
            results[gate_id] = evaluate_gate(predicate, outputs)
        except GateEvaluationError:
            results[gate_id] = False
    return results


def all_gates_passed(gate_results: dict[str, bool]) -> bool:
    """Check if all gates passed."""
    return all(gate_results.values()) if gate_results else True
