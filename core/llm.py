"""
Shared LLM transport — client selection and response cleaning.

Centralizes the Anthropic API call pattern used across consolidator,
crystallizer, threads, and self_reflection.  Handles Bedrock vs direct
client selection and markdown fence stripping.  Does NOT parse JSON or
implement retry — those are caller responsibilities because the expected
response shape and error policy differ per caller.

Transport selection (in order):
  1. CLAUDE_CODE_USE_BEDROCK=1 → AnthropicBedrock
  2. claude-agent-sdk present  → ClaudeAgentOptions / query() (subscription OAuth)
  3. fallback                  → `claude -p` subprocess

Note: API-key path (`ANTHROPIC_API_KEY`) is intentionally disabled. All
non-Bedrock calls go through subscription OAuth credentials in `~/.claude/`.
The env var is stripped from spawned subprocesses to prevent the CLI from
falling into API-key mode.

The agent-SDK path is preferred over raw subprocess because the SDK
internally serializes OAuth token refreshes, eliminating the rc=1 races
that occur when multiple parallel `claude -p` invocations contend for the
same OAuth token store. It also enables proper asyncio.gather concurrency
via `call_llm_batch`.
"""

import asyncio
import hashlib
import os
import shutil
import subprocess
from typing import Optional

import anthropic

try:  # optional — only used on the OAuth subscription path
    from claude_agent_sdk import ClaudeAgentOptions, query as _agent_query
    from claude_agent_sdk.types import ResultMessage
    _AGENT_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    ClaudeAgentOptions = None  # type: ignore[assignment]
    _agent_query = None  # type: ignore[assignment]
    ResultMessage = None  # type: ignore[assignment]
    _AGENT_SDK_AVAILABLE = False

# Model constants — one place to update when the model changes.
DEFAULT_MODEL = "claude-sonnet-4-6"
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6"


try:
    from core.trace import get_active_writer
except ImportError:  # pragma: no cover
    def get_active_writer() -> None:  # type: ignore[misc]
        return None


def _emit_llm_envelope(
    model: Optional[str],
    prompt: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    response_chars: int,
) -> None:
    """Emit an ``llm_envelope`` trace event if a TraceWriter is active.

    No-ops silently if no writer is set.
    """
    try:
        writer = get_active_writer()
        if writer is None:
            return
        model_key = model if model is not None else ""
        prompt_hash = hashlib.sha256((model_key + prompt).encode("utf-8")).hexdigest()[:16]
        writer.emit(
            stage="llm",
            event="llm_envelope",
            payload={
                "prompt_hash": prompt_hash,
                "model": model_key or DEFAULT_MODEL,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "response_chars": response_chars,
            },
        )
    except Exception:  # noqa: BLE001
        # Trace emission must never crash the caller.
        pass


def _repair_json(raw: str, drop_stats: dict | None = None) -> str | None:
    """Attempt to repair common JSON truncation and trailing-comma errors.

    Two repair strategies applied in order:
      1. Trailing-comma removal: strip commas before `}` or `]` (regex replace).
      2. Truncated-array repair: find the last complete `}`, append `]`.

    Returns the repaired JSON string if successful, or None if repair fails.
    On success, increments drop_stats["parse_errors_repaired"] if drop_stats is provided.

    Args:
        raw: The raw (possibly malformed) JSON string to repair.
        drop_stats: Optional mutable dict for instrumentation.

    Returns:
        Repaired JSON string, or None if all repair strategies fail.
    """
    import re
    import json as _json

    # Strategy 1: trailing-comma repair
    cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        _json.loads(cleaned)
        if drop_stats is not None:
            drop_stats["parse_errors_repaired"] = drop_stats.get("parse_errors_repaired", 0) + 1
        return cleaned
    except _json.JSONDecodeError:
        pass

    # Strategy 2: truncated-array repair — find last complete `}`, close array
    last_brace = cleaned.rfind("}")
    if last_brace != -1:
        candidate_text = cleaned[: last_brace + 1] + "]"
        # Prepend `[` if not already an array
        if not candidate_text.lstrip().startswith("["):
            candidate_text = "[" + candidate_text
        try:
            candidate = _json.loads(candidate_text)
            if isinstance(candidate, list):
                if drop_stats is not None:
                    drop_stats["parse_errors_repaired"] = (
                        drop_stats.get("parse_errors_repaired", 0) + 1
                    )
                return candidate_text
        except _json.JSONDecodeError:
            pass

    return None


def strip_markdown_fences(text: str) -> str:
    """
    Remove markdown code fences from LLM response text.

    Handles both ````` ```json ````` and bare ````` ``` ````` opening fences.
    Returns the text unchanged if no fences are present.

    Args:
        text: Raw text from the LLM, possibly wrapped in fences.

    Returns:
        Cleaned text with fences stripped.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening fence (```json or ```)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _make_client():
    """
    Create the appropriate Anthropic client based on environment.

    Returns:
        anthropic.AnthropicBedrock if CLAUDE_CODE_USE_BEDROCK is set,
        anthropic.Anthropic otherwise.
    """
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
        return anthropic.AnthropicBedrock()
    return anthropic.Anthropic()


def _have_api_key() -> bool:
    """Always False — API-key transport is intentionally disabled.

    All non-Bedrock calls must use subscription OAuth credentials in
    `~/.claude/`. Returning False forces `call_llm` / `call_llm_async`
    onto the agent-SDK path (or CLI fallback), both of which strip
    ANTHROPIC_API_KEY from the spawned subprocess so a stale env key
    cannot leak into the request.
    """
    return False


def _call_via_claude_cli(
    prompt: str,
    *,
    timeout: int = 180,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
) -> str:
    """Call `claude -p` subprocess using OAuth subscription credentials.

    The subprocess inherits the parent env minus ANTHROPIC_API_KEY and
    ANTHROPIC_BASE_URL — those would force the CLI into API-key mode and
    cause "Invalid API key" if the parent env carries a stale key. With
    them stripped, the CLI falls back to its OAuth credentials.

    Tools and slash commands are disabled so the model can only emit text.

    Retries with exponential backoff on rc!=0 — the OAuth path can
    transiently fail under burst load (rate-limit, token-refresh races).
    """
    import time

    if shutil.which("claude") is None:
        raise RuntimeError(
            "No ANTHROPIC_API_KEY set and `claude` CLI not on PATH — "
            "cannot reach the Anthropic API."
        )

    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")}

    last_err = ""
    for attempt in range(1, max_attempts + 1):
        proc = subprocess.run(
            [
                "claude", "-p",
                "--output-format", "text",
                "--disable-slash-commands",
                "--disallowed-tools",
                "Bash Edit Write Read Glob Grep Task WebFetch WebSearch",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
        last_err = (proc.stderr.strip() or proc.stdout.strip())[:500]
        if attempt < max_attempts:
            time.sleep(backoff_base ** attempt)
    raise RuntimeError(
        f"claude -p subprocess failed after {max_attempts} attempts "
        f"(last rc={proc.returncode}): {last_err}"
    )


def _strip_oauth_env() -> dict[str, str]:
    """Return a copy of os.environ with API-key vars removed.

    Used when the SDK or subprocess path needs to fall back on OAuth
    credentials in `~/.claude/`. ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL in
    the parent env force API-key mode and break OAuth-only login.
    """
    return {
        k: v for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")
    }


async def _call_via_agent_sdk(prompt: str) -> str:
    """Single-shot LLM call via claude-agent-sdk on OAuth credentials.

    The SDK serializes OAuth refresh internally, so concurrent gather()
    over many calls does not race on token storage the way raw subprocess
    invocations do.
    """
    if not _AGENT_SDK_AVAILABLE or _agent_query is None or ClaudeAgentOptions is None:
        raise RuntimeError("claude-agent-sdk is not installed")

    # SDK-spawned subprocess inherits parent env. Strip API-key vars so
    # the spawned `claude` CLI uses OAuth, not the (possibly stale) key.
    saved = {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")}
    for k in saved:
        os.environ.pop(k, None)

    options = ClaudeAgentOptions(
        allowed_tools=[],
        permission_mode="bypassPermissions",
        max_turns=1,
    )
    try:
        result_text = ""
        async for msg in _agent_query(prompt=prompt, options=options):
            if ResultMessage is not None and isinstance(msg, ResultMessage):
                # ResultMessage.result holds the final assistant text in
                # current SDK versions; older builds expose .text. Try both.
                result_text = (
                    getattr(msg, "result", None)
                    or getattr(msg, "text", None)
                    or ""
                )
                # Do not break — exhaust the generator so it completes naturally.
                # Breaking mid-generator leaves ag_running=True, causing
                # "aclose(): asynchronous generator is already running" when
                # asyncio.run() shuts down the event loop.
        return result_text
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def call_llm_async(
    prompt: str,
    *,
    max_tokens: int = 8192,
    temperature: float = 0,
    model: str | None = None,
) -> str:
    """Async variant of call_llm — uses agent-SDK on the OAuth path.

    On the API-key / Bedrock paths this is just an asyncio-compat wrapper
    around the synchronous SDK; concurrency gain is real only on the OAuth
    path because that's where we get the SDK's token-refresh serialization.
    """
    use_bedrock = bool(os.environ.get("CLAUDE_CODE_USE_BEDROCK"))

    if not use_bedrock and not _have_api_key() and _AGENT_SDK_AVAILABLE:
        raw = await _call_via_agent_sdk(prompt)
        return strip_markdown_fences(raw)

    return await asyncio.to_thread(
        call_llm,
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model,
    )


def call_llm_batch(
    prompts: list[str],
    *,
    max_concurrency: int = 5,
    max_tokens: int = 8192,
    temperature: float = 0,
    model: str | None = None,
) -> list[str]:
    """Run N prompts concurrently and return results in input order.

    Uses asyncio.gather under a semaphore to bound concurrency so we don't
    spawn 30 CLI subprocesses at once. The agent-SDK path benefits most;
    the direct API-key path also wins because we can run independent HTTP
    requests in parallel.

    On individual prompt failure, the corresponding entry is the exception
    message string (prefixed "[ERROR]") rather than raising — callers
    using a fan-out pattern usually want partial success.
    """
    if not prompts:
        return []

    sem = asyncio.Semaphore(max_concurrency)

    async def _bounded(p: str) -> str:
        async with sem:
            try:
                return await call_llm_async(
                    p, max_tokens=max_tokens, temperature=temperature, model=model,
                )
            except Exception as exc:  # noqa: BLE001
                return f"[ERROR] {type(exc).__name__}: {exc}"[:500]

    async def _runner() -> list[str]:
        return await asyncio.gather(*(_bounded(p) for p in prompts))

    return asyncio.run(_runner())


def call_llm(
    prompt: str,
    *,
    max_tokens: int = 8192,
    temperature: float = 0,
    model: str | None = None,
) -> str:
    """
    Call the Anthropic Messages API and return the response text.

    Handles transport selection (Bedrock / API key / agent-SDK / subprocess)
    and strips markdown fences from the response. Does NOT parse JSON or
    retry — callers own their response parsing and error handling.

    Args:
        prompt: The full prompt to send.
        max_tokens: Maximum tokens in the response (default 8192; ignored on
                    the OAuth paths where the CLI uses its own defaults).
        temperature: Sampling temperature (ignored on OAuth paths).
        model: Override model ID. If None, selects based on
               CLAUDE_CODE_USE_BEDROCK env var. Ignored on OAuth paths.

    Returns:
        The response text with markdown fences stripped.

    Raises:
        anthropic.APIError: On direct/Bedrock API failures.
        RuntimeError: On OAuth subprocess failure or missing CLI.
    """
    use_bedrock = bool(os.environ.get("CLAUDE_CODE_USE_BEDROCK"))

    if not use_bedrock and not _have_api_key():
        if _AGENT_SDK_AVAILABLE:
            try:
                result = strip_markdown_fences(asyncio.run(_call_via_agent_sdk(prompt)))
                _emit_llm_envelope(model, prompt, None, None, len(result))
                return result
            except RuntimeError as exc:
                # Common failure: nested asyncio.run from already-running loop.
                # Fall through to subprocess path in that case.
                if "asyncio.run" not in str(exc) and "running" not in str(exc).lower():
                    raise
        raw = _call_via_claude_cli(prompt)
        result = strip_markdown_fences(raw)
        _emit_llm_envelope(model, prompt, None, None, len(result))
        return result

    client = _make_client()

    if model is None:
        model = BEDROCK_MODEL if use_bedrock else DEFAULT_MODEL

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text
    result = strip_markdown_fences(raw)
    input_tokens: int | None = None
    output_tokens: int | None = None
    if hasattr(response, "usage") and response.usage is not None:
        input_tokens = getattr(response.usage, "input_tokens", None)
        output_tokens = getattr(response.usage, "output_tokens", None)
    _emit_llm_envelope(model, prompt, input_tokens, output_tokens, len(result))
    return result
