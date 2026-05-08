"""Stdout/stderr discipline helpers for Claude Code hooks.

Claude Code hooks must only emit intentional content on stdout.
Any non-hook output (diagnostics, errors, debug text) must go to stderr
so it does not corrupt the hook protocol.

Usage:
    from hooks._safe import emit_stdout, emit_stderr

    emit_stdout(injected_text)          # plain-text injection content
    emit_stdout({"some": "json"})       # dict → JSON-serialized
    emit_stdout("")                     # explicit empty
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


def emit_stderr(msg) -> None:
    """Write diagnostic/error output to stderr."""
    print(str(msg), file=sys.stderr, flush=True)
