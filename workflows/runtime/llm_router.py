"""LLM execution router.

Routes agent execution to the correct backend based on the agent's model_config:
- claude_code_print: Uses `claude -p` for thinking tasks (Max subscription, no API key)
- llm_api: Direct API call via LangChain (Anthropic/OpenAI/Google, requires API keys)
- claude_code: Spawns Claude Code CLI with tools for coding tasks
- codex: Calls OpenAI Codex agent API
"""

import json
import subprocess
import time
from typing import Any


def execute_agent(
    agent_config: dict,
    system_prompt: str,
    user_prompt: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Route to the correct execution backend based on model_config.execution.

    Args:
        session_id: For claude_code agents, resume an existing session.
                    Ignored for claude_code_print and other backends.
    """
    model_config = agent_config.get("model_config", {})
    execution = model_config.get("execution", "llm_api")

    if execution == "llm_api":
        return _execute_llm_api(model_config, system_prompt, user_prompt)
    elif execution == "claude_code":
        return _execute_claude_code(model_config, system_prompt, user_prompt, session_id=session_id)
    elif execution == "claude_code_print":
        return _execute_claude_code_print(model_config, system_prompt, user_prompt)
    elif execution == "codex":
        return _execute_codex(model_config, system_prompt, user_prompt)
    else:
        raise ValueError(f"Unknown execution type: {execution}")


def _execute_llm_api(
    config: dict, system_prompt: str, user_prompt: str
) -> dict[str, Any]:
    """Direct LLM API call via LangChain adapters.

    Supports: anthropic, openai, google providers.
    """
    provider = config.get("provider", "anthropic")
    model = config.get("model", "claude-sonnet-4-20250514")
    temperature = config.get("temperature", 0.3)
    max_tokens = config.get("max_tokens", 8000)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    start_time = time.monotonic()

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            model=model, temperature=temperature, max_tokens=max_tokens
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=model, temperature=temperature, max_tokens=max_tokens
        )
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model=model, temperature=temperature, max_output_tokens=max_tokens
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    response = llm.invoke(messages)
    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    content = response.content if hasattr(response, "content") else str(response)

    # Extract token usage if available
    usage = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = {
            "input_tokens": response.usage_metadata.get("input_tokens", 0),
            "output_tokens": response.usage_metadata.get("output_tokens", 0),
        }

    return {
        "content": content,
        "provider": provider,
        "model": model,
        "temperature": temperature,
        "latency_ms": elapsed_ms,
        "usage": usage,
    }


def _execute_claude_code(
    config: dict,
    system_prompt: str,
    user_prompt: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Spawn Claude Code CLI as a subprocess with optional session resume.

    Claude Code can: read/write files, run bash commands, use MCP servers,
    take screenshots, run tests, git operations.

    On first call: creates a new session, returns session_id.
    On retry: resumes the existing session (--resume), retaining full context.
    Uses --output-format json to extract session_id from the response.
    """
    model = config.get("model", "sonnet")
    max_turns = config.get("max_turns", 50)
    allowed_tools = config.get("allowed_tools", [])
    working_dir = config.get("working_dir", ".")

    if session_id:
        # RESUME existing session — agent has full conversation history
        full_prompt = user_prompt  # No system prompt needed, it's in the session
        cmd = [
            "claude",
            "--resume", session_id,
            "-p",
            "--output-format", "json",
            "--model", model,
            "--max-turns", str(max_turns),
        ]
    else:
        # NEW session
        full_prompt = f"{system_prompt}\n\n---\n\nTASK:\n{user_prompt}"
        cmd = [
            "claude",
            "-p",
            "--output-format", "json",
            "--model", model,
            "--max-turns", str(max_turns),
        ]

    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])

    start_time = time.monotonic()

    result = subprocess.run(
        cmd,
        input=full_prompt,
        capture_output=True,
        text=True,
        cwd=working_dir,
        timeout=600,  # 10 min max
    )
    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    # Parse JSON envelope to extract session_id and result text
    content = ""
    returned_session_id = session_id  # preserve existing if parsing fails
    try:
        cli_output = json.loads(result.stdout)
        content = cli_output.get("result", "")
        returned_session_id = cli_output.get("session_id", session_id)
    except (json.JSONDecodeError, TypeError):
        content = result.stdout.strip() if result.stdout else ""

    if result.returncode != 0 and result.stderr:
        content = f"ERROR: {result.stderr}\n\n{content}"

    return {
        "content": content,
        "provider": "claude_code",
        "model": model,
        "temperature": 0.0,
        "latency_ms": elapsed_ms,
        "usage": {},
        "session_id": returned_session_id,
    }


def _execute_claude_code_print(
    config: dict, system_prompt: str, user_prompt: str
) -> dict[str, Any]:
    """Use Claude Code CLI in print mode for thinking/judging tasks.

    This is for Max subscription users who don't have separate API keys.
    Uses `claude -p` with no tools — pure LLM thinking, output as JSON.
    """
    model = config.get("model", "haiku")
    max_turns = config.get("max_turns", 1)

    full_prompt = f"{system_prompt}\n\n---\n\nTASK:\n{user_prompt}"

    cmd = [
        "claude",
        "-p",
        "--output-format", "text",
        "--model", model,
        "--max-turns", str(max_turns),
        "--disallowed-tools", "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Agent",
        "--append-system-prompt", "You MUST return ONLY a raw JSON object. No explanation. No asking for tools or permissions. Just output the JSON.",
    ]

    start_time = time.monotonic()

    result = subprocess.run(
        cmd,
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=300,  # 5 min max for thinking tasks
    )
    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    content = result.stdout.strip() if result.stdout else ""
    if result.returncode != 0 and result.stderr:
        content = f"ERROR: {result.stderr}\n\n{content}"

    return {
        "content": content,
        "provider": "claude_code_print",
        "model": model,
        "temperature": 0.0,
        "latency_ms": elapsed_ms,
        "usage": {},
    }


def _execute_codex(
    config: dict, system_prompt: str, user_prompt: str
) -> dict[str, Any]:
    """Call OpenAI Codex agent API for sandboxed code execution."""
    model = config.get("model", "codex-mini")

    start_time = time.monotonic()

    try:
        import openai

        response = openai.responses.create(
            model=model,
            instructions=system_prompt,
            input=user_prompt,
            tools=[{"type": "code_interpreter"}],
        )
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        content = response.output_text if hasattr(response, "output_text") else str(response)

        return {
            "content": content,
            "provider": "openai",
            "model": model,
            "temperature": 0.0,
            "latency_ms": elapsed_ms,
            "usage": {},
        }
    except ImportError:
        raise RuntimeError("openai package required for Codex execution")
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        return {
            "content": f"CODEX ERROR: {exc}",
            "provider": "openai",
            "model": model,
            "temperature": 0.0,
            "latency_ms": elapsed_ms,
            "usage": {},
        }


def parse_json_output(content: str) -> dict[str, Any]:
    """Parse JSON from LLM response content.

    Handles both raw JSON and JSON wrapped in markdown code fences.
    """
    text = content.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return {"_raw": content, "_parse_error": True}
