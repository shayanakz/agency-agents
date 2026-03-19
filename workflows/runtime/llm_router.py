"""LLM execution router.

Routes agent execution to the correct backend based on the agent's model_config:
- claude_code_print: Uses `claude -p` for thinking tasks (Max subscription, no API key)
- llm_api: Direct API call via LangChain (Anthropic/OpenAI/Google, requires API keys)
- claude_code: Spawns Claude Code CLI with tools for coding tasks
- codex: Calls OpenAI Codex agent API
"""

import json
import logging
import shutil
import subprocess
import time
from typing import Any

from .events import EventType, PipelineEvent, get_event_bus

logger = logging.getLogger("pipeline.llm_router")


# ── MCP server registry ─────────────────────────────────────────
# Maps logical server names (used in agent model_config.mcp_servers)
# to how to launch them. Each entry is tried in order; first working
# one wins. If none work, the agent runs WITHOUT that MCP server and
# the caller gets mcp_unavailable=["browser"] in the result so QA
# can hard-fail instead of fabricating.

_MCP_REGISTRY: dict[str, list[dict]] = {
    "browser": [
        # Playwright MCP — preferred, works headless
        {
            "name": "playwright",
            "check": lambda: shutil.which("npx") is not None and _npx_package_available("@playwright/mcp"),
            "flag": lambda: ["--mcp-server", "npx @playwright/mcp --headless"],
        },
        # Puppeteer MCP — fallback
        {
            "name": "puppeteer",
            "check": lambda: shutil.which("npx") is not None and _npx_package_available("@modelcontextprotocol/server-puppeteer"),
            "flag": lambda: ["--mcp-server", "npx @modelcontextprotocol/server-puppeteer"],
        },
    ],
}


def _npx_package_available(package: str) -> bool:
    """Check if an npx package is locally available without installing it.

    Uses `npm list` to check local + global installs. Falls back to checking
    if the binary is on PATH directly. Does NOT trigger npm install.
    """
    try:
        # Check local node_modules first (fast)
        result = subprocess.run(
            ["npm", "list", package, "--depth=0"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and package in result.stdout:
            return True
        # Check global install
        result = subprocess.run(
            ["npm", "list", "-g", package, "--depth=0"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and package in result.stdout:
            return True
        # Check if CLI binary is directly on PATH (e.g. brew-installed playwright)
        binary = package.split("/")[-1].replace("server-", "")
        return shutil.which(binary) is not None
    except Exception:
        return False


def resolve_mcp_servers(requested: list[str]) -> tuple[list[list[str]], list[str]]:
    """Resolve requested MCP server names to CLI flags.

    Returns:
        (flags, unavailable)
        flags       — list of CLI flag pairs to extend the command with
        unavailable — names that couldn't be resolved (no working provider)
    """
    flags: list[list[str]] = []
    unavailable: list[str] = []

    from .events import EventType, PipelineEvent, get_event_bus
    bus = get_event_bus()

    for name in requested:
        providers = _MCP_REGISTRY.get(name, [])
        resolved = False
        for provider in providers:
            try:
                if provider["check"]():
                    flags.append(provider["flag"]())
                    resolved = True
                    bus.emit(PipelineEvent(
                        event_type=EventType.MCP_RESOLVED,
                        data={"server": name, "provider": provider["name"]},
                    ))
                    break
            except Exception as exc:
                logger.debug("MCP provider %s/%s check failed: %s", name, provider.get("name"), exc)
                continue
        if not resolved:
            unavailable.append(name)
            bus.emit(PipelineEvent(
                event_type=EventType.MCP_UNAVAILABLE,
                data={"server": name, "tried_providers": [p["name"] for p in providers]},
            ))

    return flags, unavailable


def execute_agent(
    agent_config: dict,
    system_prompt: str,
    user_prompt: str,
    session_id: str | None = None,
    working_dir: str | None = None,
) -> dict[str, Any]:
    """Route to the correct execution backend based on model_config.execution.

    Args:
        session_id: For claude_code agents, resume an existing session.
        working_dir: Override the working directory for claude_code agents.
                     Takes precedence over the static config value.

    Returns dict always includes:
        mcp_unavailable: list[str] — MCP servers that were requested but
            couldn't be resolved. Empty list means all servers connected.
            Callers MUST treat this as a hard failure for evidence-dependent
            agents (QA) rather than silently continuing.
    """
    model_config = agent_config.get("model_config", {})
    execution = model_config.get("execution", "llm_api")

    if execution == "llm_api":
        return _execute_llm_api(model_config, system_prompt, user_prompt)
    elif execution == "claude_code":
        return _execute_claude_code(
            model_config, system_prompt, user_prompt,
            session_id=session_id, working_dir=working_dir,
        )
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
        "mcp_unavailable": [],
    }


def _execute_claude_code(
    config: dict,
    system_prompt: str,
    user_prompt: str,
    session_id: str | None = None,
    working_dir: str | None = None,
) -> dict[str, Any]:
    """Spawn Claude Code CLI as a subprocess with optional session resume.

    Claude Code can: read/write files, run bash commands, use MCP servers,
    take screenshots, run tests, git operations.

    On first call: creates a new session, returns session_id.
    On retry: resumes the existing session (--resume), retaining full context.

    MCP servers listed in config.mcp_servers are resolved at call time.
    If a server can't be connected, it is listed in mcp_unavailable in the
    return value — it is NOT silently skipped.
    """
    model = config.get("model", "sonnet")
    max_turns = config.get("max_turns", 50)
    allowed_tools = config.get("allowed_tools", [])
    requested_mcp = config.get("mcp_servers", [])
    # working_dir passed by caller takes precedence over static config
    working_dir = working_dir or config.get("working_dir", ".")

    # Resolve MCP servers — find working provider for each requested server
    mcp_flags, mcp_unavailable = resolve_mcp_servers(requested_mcp)

    if session_id:
        full_prompt = user_prompt
        cmd = [
            "claude",
            "--resume", session_id,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model,
            "--max-turns", str(max_turns),
        ]
    else:
        full_prompt = f"{system_prompt}\n\n---\n\nTASK:\n{user_prompt}"
        cmd = [
            "claude",
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model,
            "--max-turns", str(max_turns),
        ]

    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])

    # Attach resolved MCP server flags
    for flag_pair in mcp_flags:
        cmd.extend(flag_pair)

    start_time = time.monotonic()
    bus = get_event_bus()

    # Stream output line by line for real-time progress
    content = ""
    returned_session_id = session_id
    result_text = ""

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered for real-time streaming
            cwd=working_dir,
        )
        if full_prompt:
            proc.stdin.write(full_prompt)
            proc.stdin.close()

        # Read stream-json lines and emit progress events
        # Format: {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", ...}]}}
        #         {"type": "result", "result": "...", "session_id": "..."}
        # Use readline() instead of iterator — the iterator buffers internally
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if not line:
                continue
            line = line.strip()
            if not line:
                continue
            result_text += line + "\n"
            try:
                event = json.loads(line)
                etype = event.get("type", "")

                if etype == "assistant":
                    msg = event.get("message", {})
                    if isinstance(msg, dict):
                        for block in msg.get("content", []):
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type", "")
                            if btype == "tool_use":
                                tool_name = block.get("name", "")
                                # Show meaningful tool descriptions
                                tool_input = block.get("input", {})
                                detail = ""
                                if tool_name == "Write" and isinstance(tool_input, dict):
                                    detail = tool_input.get("file_path", "")
                                elif tool_name == "Edit" and isinstance(tool_input, dict):
                                    detail = tool_input.get("file_path", "")
                                elif tool_name == "Bash" and isinstance(tool_input, dict):
                                    detail = tool_input.get("command", "")[:80]
                                elif tool_name == "Read" and isinstance(tool_input, dict):
                                    detail = tool_input.get("file_path", "")
                                msg_text = f"Using {tool_name}" + (f": {detail}" if detail else "")
                                bus.emit(PipelineEvent(
                                    event_type=EventType.LLM_PROGRESS,
                                    data={"message": msg_text[:200], "type": "tool_use", "tool": tool_name},
                                ))
                            elif btype == "text" and block.get("text", "").strip():
                                text = block["text"].strip()
                                if len(text) > 10:  # skip tiny fragments
                                    bus.emit(PipelineEvent(
                                        event_type=EventType.LLM_PROGRESS,
                                        data={"message": text[:300], "type": "text"},
                                    ))

                elif etype == "result":
                    content = event.get("result", content)
                    returned_session_id = event.get("session_id", returned_session_id)

            except json.JSONDecodeError:
                pass

        proc.wait(timeout=600)
        stderr = proc.stderr.read()

        if proc.returncode != 0 and stderr:
            content = f"ERROR: {stderr}\n\n{content}"

    except subprocess.TimeoutExpired:
        proc.kill()
        content = "ERROR: Claude Code timed out after 600 seconds"
    except Exception as exc:
        content = f"ERROR: {exc}"

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    # If we didn't get content from stream, try parsing the full output
    if not content and result_text:
        for line in reversed(result_text.strip().split("\n")):
            try:
                event = json.loads(line)
                if event.get("type") == "result":
                    content = event.get("result", "")
                    returned_session_id = event.get("session_id", returned_session_id)
                    break
            except json.JSONDecodeError:
                continue

    return {
        "content": content,
        "provider": "claude_code",
        "model": model,
        "temperature": 0.0,
        "latency_ms": elapsed_ms,
        "usage": {},
        "session_id": returned_session_id,
        "mcp_unavailable": mcp_unavailable,
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
        "mcp_unavailable": [],
    }


def _execute_codex(
    config: dict, system_prompt: str, user_prompt: str
) -> dict[str, Any]:
    """Call OpenAI Codex agent API for sandboxed code execution."""
    model = config.get("model", "codex-mini")

    start_time = time.monotonic()

    try:
        import openai

        client = openai.OpenAI()
        response = client.responses.create(
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
            "mcp_unavailable": [],
        }
    except ImportError:
        raise RuntimeError("openai package required for Codex execution")
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        logger.error("Codex execution failed: %s", exc)
        # Return error as structured JSON so parse_json_output produces
        # a dict with _error=True instead of opaque error text being
        # treated as valid LLM output downstream
        error_json = json.dumps({
            "_error": True,
            "_error_type": type(exc).__name__,
            "_error_message": str(exc),
        })
        return {
            "content": error_json,
            "provider": "openai",
            "model": model,
            "temperature": 0.0,
            "latency_ms": elapsed_ms,
            "usage": {},
            "mcp_unavailable": [],
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
