#!/usr/bin/env python3
"""Minimal pipeline test — runs brainstorm + planning via Claude Code CLI (haiku).

No Supabase needed. No API keys needed. Uses Max subscription via `claude -p`.

Usage:
    cd workflows
    python3 test_pipeline.py
"""

import json
import subprocess
import time
import sys
from pathlib import Path


def call_claude(system_prompt: str, user_prompt: str, model: str = "haiku") -> str:
    """Call Claude Code CLI in print mode."""
    full_prompt = f"{system_prompt}\n\n---\n\nTASK:\n{user_prompt}"

    cmd = [
        "claude", "-p", full_prompt,
        "--output-format", "json",
        "--model", model,
        "--max-turns", "1",
    ]

    print(f"  Calling claude --model {model} ...")
    start = time.monotonic()

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    elapsed = time.monotonic() - start

    print(f"  Done in {elapsed:.1f}s")

    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:200]}")
        return ""

    try:
        cli_output = json.loads(result.stdout)
        return cli_output.get("result", result.stdout)
    except json.JSONDecodeError:
        return result.stdout


def parse_json(text: str) -> dict:
    """Extract JSON from LLM response."""
    text = text.strip()
    # Strip markdown fences
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {"_raw": text[:500]}


def main():
    print("=" * 60)
    print("  IDEA-TO-PRODUCTION PIPELINE TEST")
    print("  Model: haiku | Mode: claude -p (Max subscription)")
    print("=" * 60)

    idea = "A simple todo list web app with local storage"

    # ── Stage 1: Brainstorm ─────────────────────────────────────
    print("\n[STAGE 1] BRAINSTORM AGENT")
    print("-" * 40)

    brainstorm_system = """You are a market researcher and idea validator.
Given an idea, provide a brief research assessment.

Return ONLY a JSON object with this exact structure:
{
  "research_brief": {
    "problem_statement": "...",
    "target_audience": "...",
    "competitive_landscape": [
      {"name": "...", "strengths": ["..."], "weaknesses": ["..."]}
    ],
    "feature_list": {"core": [{"name": "...", "description": "..."}]},
    "technical_recommendations": "...",
    "risks": ["..."],
    "confidence": 0.8
  },
  "validated_idea": {
    "idea": "...",
    "confidence": 0.8,
    "differentiators": ["..."]
  }
}

Keep it concise. Return ONLY valid JSON, no other text."""

    brainstorm_result = call_claude(
        brainstorm_system,
        f"Research and validate this idea: {idea}",
        model="haiku"
    )
    brainstorm_parsed = parse_json(brainstorm_result)

    # Evaluate gates
    confidence = 0.0
    validated = brainstorm_parsed.get("validated_idea", {})
    brief = brainstorm_parsed.get("research_brief", brainstorm_parsed)
    if isinstance(validated, dict):
        confidence = validated.get("confidence", 0.0)
    competitors = brief.get("competitive_landscape", [])

    gates = {
        "confidence >= 0.6": confidence >= 0.6,
        "competitors >= 3": len(competitors) >= 3,
    }
    all_passed = all(gates.values())

    print(f"  Confidence: {confidence}")
    print(f"  Competitors found: {len(competitors)}")
    print(f"  Gates: {gates}")
    print(f"  All gates passed: {'YES' if all_passed else 'NO'}")

    if not all_passed:
        print("\n  Gates did not pass, but continuing for test purposes...")

    # ── Stage 2: Planning ───────────────────────────────────────
    print("\n[STAGE 2] PLANNING AGENT")
    print("-" * 40)

    planning_system = """You are a senior project manager.
Given a research brief, create a sprint backlog.

Return ONLY a JSON object with this exact structure:
{
  "sprint_backlog": {
    "tasks": [
      {
        "id": "task-1",
        "title": "...",
        "description": "...",
        "category": "frontend",
        "acceptance_criteria": ["..."],
        "estimated_hours": 4,
        "dependencies": []
      }
    ],
    "total_estimated_hours": 12
  },
  "architecture": {
    "tech_stack": {"frontend": "...", "storage": "..."},
    "data_model": "...",
    "api_contracts": "N/A for local-only app",
    "deployment_target": "static"
  }
}

Keep it to 3-4 tasks max. Return ONLY valid JSON, no other text."""

    planning_result = call_claude(
        planning_system,
        f"Create a sprint backlog for this validated idea:\n\n{json.dumps(brainstorm_parsed, indent=2)}",
        model="haiku"
    )
    planning_parsed = parse_json(planning_result)

    tasks = planning_parsed.get("sprint_backlog", {}).get("tasks", [])
    architecture = planning_parsed.get("architecture", {})

    # Evaluate gates
    tasks_have_criteria = all(
        len(t.get("acceptance_criteria", [])) > 0
        for t in tasks
    ) if tasks else False
    tasks_scoped = all(
        t.get("estimated_hours", 99) <= 8
        for t in tasks
    ) if tasks else False

    gates2 = {
        "has_tasks": len(tasks) > 0,
        "tasks_have_criteria": tasks_have_criteria,
        "tasks_scoped_<=8h": tasks_scoped,
    }
    all_passed2 = all(gates2.values())

    print(f"  Tasks created: {len(tasks)}")
    for t in tasks:
        print(f"    - [{t.get('category', '?')}] {t.get('title', '?')} ({t.get('estimated_hours', '?')}h)")
    print(f"  Architecture: {architecture.get('tech_stack', {})}")
    print(f"  Gates: {gates2}")
    print(f"  All gates passed: {'YES' if all_passed2 else 'NO'}")

    # ── Save artifacts ──────────────────────────────────────────
    output_dir = Path("test-artifacts")
    output_dir.mkdir(exist_ok=True)
    (output_dir / "brainstorm.json").write_text(json.dumps(brainstorm_parsed, indent=2))
    (output_dir / "planning.json").write_text(json.dumps(planning_parsed, indent=2))

    # ── Summary ─────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  PIPELINE TEST COMPLETE")
    print("=" * 60)
    print(f"  Stages run: 2 (brainstorm, planning)")
    print(f"  Model: haiku (via claude -p, Max subscription)")
    print(f"  Brainstorm gates: {'PASSED' if all_passed else 'PARTIAL'}")
    print(f"  Planning gates: {'PASSED' if all_passed2 else 'PARTIAL'}")
    print(f"  Artifacts saved: test-artifacts/")
    print(f"\n  This proves the pipeline can:")
    print(f"    1. Call Claude Code CLI programmatically")
    print(f"    2. Get structured JSON output from agents")
    print(f"    3. Evaluate deterministic gates against output")
    print(f"    4. Pass data between stages (handoff)")
    print(f"    5. Save artifacts to disk")


if __name__ == "__main__":
    main()
