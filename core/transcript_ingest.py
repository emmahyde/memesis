#!/usr/bin/env python3
"""
Cron-driven transcript delta ingestion pipeline.

Each tick:
  1. Discover JSONL transcripts modified within the last 25h
  2. For each session, read new content since last cursor byte offset
  3. Extract 0-3 durable observations via LLM
  4. Append observations to the project's ephemeral session buffer
  5. Advance the cursor

New sessions: cursor is created at EOF — nothing extracted on first contact.
Path rotation: cursor reset to EOF of new path.
"""

import fcntl
import json
import logging
import time
from datetime import date
from pathlib import Path

from core.transcript import read_transcript_from, summarize
from core.cursors import CursorStore
from core.llm import call_llm
from core.prompts import OBSERVATION_EXTRACT_PROMPT, OBSERVATION_TYPES, format_observation
from core.session_detector import detect_session_type

logger = logging.getLogger(__name__)


def discover_transcripts(max_age_hours: int = 25) -> list[Path]:
    """Glob JSONL transcripts modified within max_age_hours, sorted."""
    cutoff = time.time() - max_age_hours * 3600
    base = Path.home() / ".claude" / "projects"
    paths = [
        p for p in base.glob("*/*.jsonl")
        if p.stat().st_mtime >= cutoff
    ]
    return sorted(paths)


def project_memory_dir(jsonl_path: Path) -> Path:
    """Return the memory dir for the project containing jsonl_path."""
    return jsonl_path.parent / "memory"


def _write_ingest_trace(outcome: str, reason: str, raw_excerpt: str) -> None:
    """Append a skip/rejection trace to the validator observability stream.

    Local writer — does not depend on core/observability.py (WS-A).
    Refactor once WS-A lands.
    """
    import datetime
    trace_path = Path("backfill-output") / "observability" / "validator-trace.jsonl"
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            "stage": "ingest",
            "outcome": outcome,
            "field_errors": [reason],
            "raw_excerpt": raw_excerpt[:80],
        }
        with trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def extract_observations(rendered: str, session_type: str = "code") -> list[dict]:
    """Call LLM to extract observations; filter low-importance entries.

    Handles two LLM response formats:
      1. JSON array  []         — existing behavior; observation list (possibly empty).
      2. JSON object {"skipped": true, "reason": "..."}
                                — intentional skip signal (Stage-1 skip protocol, LLME-F5).
         Any other dict is treated as malformed and rejected.

    Args:
        rendered: Summarised transcript slice text.
        session_type: 'code', 'writing', or 'research' — injected into Stage 1
                      prompt context block so the LLM knows the session genre.
    """
    raw = call_llm(OBSERVATION_EXTRACT_PROMPT.format(transcript=rendered, session_type=session_type))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("extract_observations: failed to parse LLM response as JSON")
        return []

    if isinstance(parsed, list):
        # Existing behavior — array of observations (may be empty).
        return [o for o in parsed if o.get("importance", 0) >= 0.3]

    if isinstance(parsed, dict):
        if parsed.get("skipped") is True:
            # Intentional skip — log and return empty.
            reason = parsed.get("reason", "(no reason given)")
            logger.info(
                "extract_observations: intentional skip — %s", reason
            )
            _write_ingest_trace("skipped", reason, raw[:80])
            return []
        # Dict without "skipped" key — malformed response.
        logger.warning(
            "extract_observations: LLM returned a dict without 'skipped' key — treating as malformed"
        )
        _write_ingest_trace(
            "rejected",
            "dict response without skipped=true",
            raw[:80],
        )
        return []

    # Unexpected type (shouldn't happen after json.loads, but guard anyway).
    logger.warning(
        "extract_observations: unexpected parsed type %s — discarding",
        type(parsed).__name__,
    )
    return []


def append_to_ephemeral(
    memory_dir: Path,
    observations: list[dict],
    dry_run: bool = False,
) -> int:
    """Append formatted observations to today's ephemeral session buffer."""
    if not observations:
        return 0

    target = memory_dir / "ephemeral" / f"session-{date.today().isoformat()}.md"
    lines = []
    for obs in observations:
        mode = obs.get("mode")
        obs_type = mode if mode in OBSERVATION_TYPES else None
        lines.append(format_observation(obs["content"], obs_type=obs_type))

    formatted_text = "\n".join(lines) + "\n"

    if dry_run:
        print(f"[dry_run] would append to {target}:\n{formatted_text}")
        return len(observations)

    lock_path = target.parent / ".lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(target, "a", encoding="utf-8") as f:
                f.write(formatted_text)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)

    return len(observations)


def tick(dry_run: bool = False, max_sessions: int | None = None) -> dict:
    """Run one ingestion tick across all recently-modified transcripts."""
    results = {"processed": 0, "observations_total": 0, "skipped": 0}

    with CursorStore() as store:
        transcripts = discover_transcripts()
        if max_sessions is not None:
            transcripts = transcripts[:max_sessions]

        for path in transcripts:
            session_id = path.stem
            cursor = store.get(session_id)
            file_size = path.stat().st_size

            if cursor is None:
                logger.debug("tick: new session %s — seeding cursor at EOF", session_id)
                if not dry_run:
                    store.upsert(session_id, str(path), file_size)
                results["skipped"] += 1
                continue

            if cursor.transcript_path != str(path):
                logger.debug(
                    "tick: path rotated for %s — resetting cursor to EOF", session_id
                )
                if not dry_run:
                    store.upsert(session_id, str(path), file_size)
                results["skipped"] += 1
                continue

            entries, new_offset = read_transcript_from(path, cursor.last_byte_offset)

            if not entries:
                if not dry_run:
                    store.upsert(session_id, str(path), new_offset)
                continue

            rendered = summarize(entries)

            # Detect session type from cwd embedded in transcript entries + tool mix
            session_cwd: str | None = None
            tool_uses: list[dict] = []
            for entry in entries:
                msg = entry.get("message") or {}
                # cwd lives at top-level or inside message
                if not session_cwd:
                    session_cwd = entry.get("cwd") or msg.get("cwd")
                # Collect tool use entries for tool-mix heuristic
                if entry.get("type") == "tool_use" or msg.get("type") == "tool_use":
                    tool_name = entry.get("tool_name") or msg.get("name") or ""
                    file_path = entry.get("input", {}).get("file_path") or ""
                    if tool_name:
                        tool_uses.append({"tool_name": tool_name, "file_path": file_path})

            session_type = detect_session_type(session_cwd, tool_uses or None)
            obs_list = extract_observations(rendered, session_type=session_type)

            # Attach session_type to each observation for downstream validators
            for obs in obs_list:
                obs.setdefault("session_type", session_type)
            mem_dir = project_memory_dir(path)
            n = append_to_ephemeral(mem_dir, obs_list, dry_run=dry_run)

            if not dry_run:
                store.upsert(session_id, str(path), new_offset)

            logger.info(
                "tick: session %s — %d observation(s) appended", session_id, n
            )
            results["processed"] += 1
            results["observations_total"] += n

    return results
