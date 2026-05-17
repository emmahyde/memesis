"""Stdout/stderr discipline helpers for Claude Code hooks.

Claude Code hooks must only emit intentional content on stdout.
Any non-hook output (diagnostics, errors, debug text) must go to stderr
so it does not corrupt the hook protocol.

Usage:
    from hooks._safe import emit_stdout, emit_stderr, emit_context

    emit_stdout(injected_text)          # plain-text injection content
    emit_stdout({"some": "json"})       # dict → JSON-serialized
    emit_stdout("")                     # explicit empty
    emit_context(panel, "SessionStart") # visible to BOTH user and model
    emit_stderr(f"Error: {e}")          # always stderr
"""
import json
import sys


def emit_stdout(data=None) -> None:
    """Write intentional hook output to stdout.

    - dict  → JSON-serialized line
    - str   → written as-is (may be empty)
    - None  → empty line

    Any routing failure (e.g. non-serializable dict) sends a diagnostic to
    stderr and writes an empty line to stdout so the hook protocol stays valid.
    """
    if data is None or data == "":
        print("", flush=True)
        return

    if isinstance(data, dict):
        try:
            print(json.dumps(data), flush=True)
        except (TypeError, ValueError) as exc:
            print(f"[hook] non-serializable stdout payload: {exc}", file=sys.stderr, flush=True)
            print("", flush=True)
        return

    if isinstance(data, str):
        print(data, flush=True)
        return

    # Unexpected type — route to stderr, emit empty stdout
    print(f"[hook] invalid stdout type: {type(data).__name__}", file=sys.stderr, flush=True)
    print("", flush=True)


def emit_context(text, hook_event: str, *, to_user: bool = True) -> None:
    """Emit hook content to BOTH the model and the user.

    Claude Code routes the two audiences through separate JSON fields:

    - ``hookSpecificOutput.additionalContext`` is added to the model context.
    - ``systemMessage`` is displayed to the user in the UI; the model never
      reads it.

    Plain stdout reaches only the model, so a hook that wants its content
    visible to the user as well must emit this JSON shape. Empty/whitespace
    content emits an empty line — no message either way.

    Args:
        text: The content to surface.
        hook_event: Hook event name (e.g. "SessionStart", "UserPromptSubmit").
        to_user: When False, send to the model only (skip ``systemMessage``).
    """
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text:
        print("", flush=True)
        return

    payload = {
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": text,
        }
    }
    if to_user:
        payload["systemMessage"] = text

    try:
        print(json.dumps(payload), flush=True)
    except (TypeError, ValueError) as exc:
        print(f"[hook] non-serializable context payload: {exc}", file=sys.stderr, flush=True)
        print("", flush=True)


def emit_stderr(msg) -> None:
    """Write diagnostic/error output to stderr."""
    print(str(msg), file=sys.stderr, flush=True)
