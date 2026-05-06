#!/usr/bin/env python3
"""
Inspect memesis pipeline trace JSONL files.

Traces are written by ``core/trace.py`` to
``~/.claude/memesis/traces/<session_id>.jsonl`` — one JSON event per line:
``{"ts": "<iso>", "stage": "...", "event": "...", "payload": {...}}``.

Usage
-----
    # List all sessions (sorted by mtime)
    python3 scripts/trace_query.py --list-sessions

    # All events for a session, pretty-printed
    python3 scripts/trace_query.py --session <id>

    # Filter by event type
    python3 scripts/trace_query.py --session <id> --event kensinger_bump

    # Filter by pipeline stage
    python3 scripts/trace_query.py --session <id> --stage consolidate

    # Raw JSONL (grep-friendly)
    python3 scripts/trace_query.py --session <id> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterator

_TRACES_DIR = Path.home() / ".claude" / "memesis" / "traces"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _iter_events(session_path: Path) -> Iterator[dict]:
    """Yield parsed event dicts from a JSONL trace file in file order (chronological)."""
    with open(session_path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as exc:
                print(
                    f"  [warn] line {lineno} in {session_path.name}: {exc}",
                    file=sys.stderr,
                )


def _pretty_event(ev: dict) -> str:
    ts = ev.get("ts", "?")
    stage = ev.get("stage", "?")
    event = ev.get("event", "?")
    payload = ev.get("payload", {})
    payload_str = json.dumps(payload, ensure_ascii=False)
    if len(payload_str) > 120:
        payload_str = payload_str[:117] + "..."
    return f"  [{ts}] {stage} / {event}  {payload_str}"


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_list_sessions(traces_dir: Path) -> None:
    """Print all sessions sorted by mtime, with event counts."""
    if not traces_dir.exists():
        traces_dir.mkdir(parents=True, exist_ok=True)
        print("No traces found.")
        return

    jsonl_files = sorted(
        traces_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jsonl_files:
        print("No traces found.")
        return

    print(f"{'session_id':<52}  {'events':>6}  mtime")
    print("-" * 72)
    for path in jsonl_files:
        session_id = path.stem
        try:
            count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            count = -1
        import datetime
        mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{session_id:<52}  {count:>6}  {mtime}")


def cmd_query(
    traces_dir: Path,
    session_id: str,
    event_filter: str | None,
    stage_filter: str | None,
    emit_json: bool,
) -> None:
    """Query events for a session, with optional filters."""
    if not traces_dir.exists():
        traces_dir.mkdir(parents=True, exist_ok=True)
        print("No traces found.")
        return

    session_path = traces_dir / f"{session_id}.jsonl"
    if not session_path.exists():
        # Try partial match
        candidates = sorted(traces_dir.glob(f"{session_id}*.jsonl"))
        if len(candidates) == 1:
            session_path = candidates[0]
            print(f"[matched] {session_path.name}", file=sys.stderr)
        elif len(candidates) > 1:
            print(
                f"Ambiguous session id '{session_id}': matches {[p.stem for p in candidates]}",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            print(f"No trace file found for session '{session_id}'.", file=sys.stderr)
            sys.exit(1)

    found = 0
    for ev in _iter_events(session_path):
        if event_filter and ev.get("event") != event_filter:
            continue
        if stage_filter and ev.get("stage") != stage_filter:
            continue
        found += 1
        if emit_json:
            print(json.dumps(ev, ensure_ascii=False))
        else:
            print(_pretty_event(ev))

    if found == 0 and not emit_json:
        filters = []
        if event_filter:
            filters.append(f"event={event_filter!r}")
        if stage_filter:
            filters.append(f"stage={stage_filter!r}")
        msg = "No events found"
        if filters:
            msg += f" matching {', '.join(filters)}"
        print(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Query memesis pipeline trace JSONL files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--session",
        metavar="SESSION_ID",
        help="Session id to inspect (stem of the .jsonl file).",
    )
    ap.add_argument(
        "--event",
        metavar="EVENT_TYPE",
        default=None,
        help="Filter to a specific event type (e.g. kensinger_bump, llm_envelope).",
    )
    ap.add_argument(
        "--stage",
        metavar="STAGE_NAME",
        default=None,
        help="Filter to a specific pipeline stage.",
    )
    ap.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit raw JSONL lines instead of pretty-printed output.",
    )
    ap.add_argument(
        "--list-sessions",
        action="store_true",
        help="List all sessions under the traces directory, sorted by mtime.",
    )
    ap.add_argument(
        "--traces-dir",
        metavar="PATH",
        default=None,
        help=f"Override the traces directory (default: {_TRACES_DIR}).",
    )
    args = ap.parse_args()

    traces_dir = Path(args.traces_dir) if args.traces_dir else _TRACES_DIR

    if args.list_sessions:
        cmd_list_sessions(traces_dir)
        return

    if not args.session:
        ap.error("Provide --session SESSION_ID or --list-sessions.")

    cmd_query(
        traces_dir=traces_dir,
        session_id=args.session,
        event_filter=args.event,
        stage_filter=args.stage,
        emit_json=args.emit_json,
    )


if __name__ == "__main__":
    main()
