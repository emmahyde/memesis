#!/usr/bin/env python3
"""
Cron-driven transcript delta ingestion pipeline.

Each tick:
  1. Discover JSONL transcripts modified within the last 25h
  2. For each session, read new content since last cursor byte offset
  3. Extract durable observations via LLM (no quota; quality gate per Sprint A)
  4. Append observations to the project's ephemeral session buffer
  5. Advance the cursor

New sessions: cursor is created at EOF — nothing extracted on first contact.
Path rotation: cursor reset to EOF of new path.

This is Stage 1 of the two-stage memory pipeline. Stage 1 captures
*episodic* observations — temporally-tagged, session-bound (Tulving 1972).
Stage 2 (`core/consolidator.py`) elaborates these toward *semantic* memory
— context-free knowledge. The biological analog is hippocampal-to-neocortical
indexing, but functionally this implements elaborative curation
(Craik & Lockhart 1972), not biological consolidation.
"""

import fcntl
import hashlib
import json
import logging
import time
from datetime import date
from pathlib import Path

from core.transcript import (
    read_transcript_from,
    summarize,
    iter_windows,
    iter_user_anchored_windows,
)
from core.cursors import CursorStore
from core.llm import call_llm, call_llm_batch
from core.prompts import OBSERVATION_EXTRACT_PROMPT, OBSERVATION_TYPES, format_observation
from core.session_detector import detect_session_type
from core.extraction_affect import aggregate_window_affect, apply_affect_prior, WindowAffect
from core.issue_cards import synthesize_issue_cards
from core.self_reflection_extraction import (
    ExtractionRunStats,
    reflect_on_extraction,
    select_chunking,
    build_self_model_preamble,
)

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


def extract_observations(
    rendered: str,
    session_type: str = "code",
    drop_stats: dict | None = None,
) -> list[dict]:
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
        drop_stats: optional mutable dict; if provided, the count of obs
                    filtered by the importance < 0.3 gate is added to
                    drop_stats["low_importance_dropped"] (key created if absent).
                    Phase E audit (2026-04-28) added this so the silent
                    importance drop is observable downstream.
    """
    raw = call_llm(OBSERVATION_EXTRACT_PROMPT.format(transcript=rendered, session_type=session_type))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("extract_observations: failed to parse LLM response as JSON")
        return []

    if isinstance(parsed, list):
        # Existing behavior — array of observations (may be empty).
        kept = [o for o in parsed if o.get("importance", 0) >= 0.3]
        if drop_stats is not None:
            drop_stats["low_importance_dropped"] = (
                drop_stats.get("low_importance_dropped", 0)
                + (len(parsed) - len(kept))
            )
        return kept

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


def _parse_extract_response(
    raw: str,
    *,
    affect=None,
    drop_stats: dict | None = None,
    skip_reason: str | None = None,
) -> tuple[list[dict], str | None]:
    """Parse a Stage 1 LLM response with the same semantics as extract_observations.

    Returns (observation_list, skip_reason_text) where skip_reason_text is
    set when the response is an intentional skip, otherwise None. The
    observation list is possibly empty after filter / on skip.

    On JSONDecodeError for an array-shaped response, attempts truncate-at-last-}
    plus append-] repair before falling back to []. Successful repair is logged at
    WARNING with parse_error_repaired=True and increments
    drop_stats["parse_errors_repaired"].

    drop_stats: optional mutable dict; receives:
      - "low_importance_dropped": count of obs filtered at importance < 0.3
      - "parse_errors_repaired": count of successfully repaired truncated arrays
      (Phase E audit instrumentation, 2026-04-28).
    """
    parsed = None
    repaired = False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Attempt repair: truncate to last complete object, close array
        last_brace = raw.rfind("}")
        if last_brace != -1:
            repaired_text = raw[: last_brace + 1] + "]"
            try:
                candidate = json.loads(repaired_text)
                if isinstance(candidate, list):
                    logger.warning(
                        "_parse_extract_response: JSON repaired (truncated array) — "
                        "%d obs recovered", len(candidate),
                    )
                    if drop_stats is not None:
                        drop_stats["parse_errors_repaired"] = (
                            drop_stats.get("parse_errors_repaired", 0) + 1
                        )
                    parsed = candidate
                    repaired = True
            except json.JSONDecodeError:
                pass
        if not repaired:
            logger.warning("_parse_extract_response: JSON decode failed: %s", exc)
            return [], None

    if isinstance(parsed, list):
        kept = [o for o in parsed if o.get("importance", 0) >= 0.3]
        if drop_stats is not None:
            drop_stats["low_importance_dropped"] = (
                drop_stats.get("low_importance_dropped", 0)
                + (len(parsed) - len(kept))
            )
        return kept, None

    if isinstance(parsed, dict):
        if parsed.get("skipped") is True:
            reason = parsed.get("reason", "(no reason given)")
            logger.info("_parse_extract_response: intentional skip — %s", reason)
            _write_ingest_trace("skipped", reason, raw[:80])
            return [], reason
        logger.warning(
            "_parse_extract_response: LLM returned a dict without 'skipped' key — treating as malformed"
        )
        _write_ingest_trace("rejected", "dict response without skipped=true", raw[:80])
        return [], None

    logger.warning(
        "_parse_extract_response: unexpected parsed type %s — discarding",
        type(parsed).__name__,
    )
    return [], None


def _normalize_for_dedupe(text: str) -> frozenset[str]:
    """Lower-case word-set for dedup. Drops short words and punctuation."""
    import re
    words = re.findall(r"[a-z][a-z0-9_]{2,}", text.lower())
    return frozenset(words)


def _dedupe_observations(observations: list[dict]) -> tuple[list[dict], int]:
    """Deduplicate observations across windows using normalized content hash. Keeps highest-importance copy.

    Builds hash key via _normalize_for_dedupe so case and punctuation differences
    are collapsed before comparison. Only catches exact / normalized-exact duplicates.
    Paraphrases are intentionally passed through to Stage 1.5 synthesis which handles
    semantic dedup.

    Returns (deduped, n_dropped).
    """
    seen: dict[str, int] = {}  # hash -> index in kept
    kept: list[dict] = []
    dropped = 0
    for obs in observations:
        content = obs.get("content", "")
        facts_str = " ".join(obs.get("facts", []) or [])
        normalized = _normalize_for_dedupe(f"{content} {facts_str}")
        if not normalized:
            kept.append(obs)
            continue
        key = hashlib.md5(",".join(sorted(normalized)).encode()).hexdigest()
        if key not in seen:
            seen[key] = len(kept)
            kept.append(obs)
        else:
            if obs.get("importance", 0) > kept[seen[key]].get("importance", 0):
                kept[seen[key]] = obs
            dropped += 1
    return kept, dropped


def extract_observations_hierarchical(
    entries: list[dict],
    session_type: str = "code",
    *,
    window_chars: int = 16000,
    stride_chars: int = 12800,
    max_windows: int = 10,
    refine: bool = True,
    chunking: str = "stride",
    context_before: int = 2,
    context_after: int = 8,
) -> dict:
    """Map-reduce extraction over overlapping windows.

    Method (academically grounded):
      - Map: extract_observations() per overlapping window (Beltagy 2020,
        Wu 2021 hierarchical book summarization).
      - Reduce: content-hash exact-duplicate dedup across windows,
        keeping the highest-importance copy of each duplicate cluster.

    Window sizing avoids the lost-in-the-middle U-shape (Liu 2023) by
    keeping each call below ~30% of the model's effective attention range.

    Returns:
        {
          "observations": list[dict],         # deduped final set
          "windows": int,                     # how many LLM calls made
          "raw_count": int,                   # pre-dedup observation count
          "dropped_duplicates": int,
          "skips": list[dict],                # per-window skip records
        }
    """
    if chunking == "user_anchored":
        windows = iter_user_anchored_windows(
            entries,
            context_before=context_before,
            context_after=context_after,
            max_chars_per_window=window_chars,
            max_windows=max_windows,
        )
    else:
        windows = iter_windows(
            entries,
            window_chars=window_chars,
            stride_chars=stride_chars,
            max_windows=max_windows,
        )
    if not windows:
        return {
            "observations": [],
            "windows": 0,
            "raw_count": 0,
            "dropped_duplicates": 0,
            "skips": [],
        }

    all_obs: list[dict] = []
    skips: list[dict] = []
    affect_signals: list[WindowAffect] = [
        aggregate_window_affect(w) for w in windows
    ]
    productive_windows = 0
    parse_errors = 0
    cost_calls = 0
    drop_stats: dict = {"low_importance_dropped": 0}

    # Concurrent LLM batch — agent-SDK serializes OAuth refresh internally
    # so this no longer races against itself the way raw subprocess did.
    prompts = [
        OBSERVATION_EXTRACT_PROMPT.format(transcript=w, session_type=session_type)
        for w in windows
    ]
    raw_responses = call_llm_batch(prompts, max_concurrency=4)

    for i, (w, raw, affect) in enumerate(zip(windows, raw_responses, affect_signals)):
        if raw.startswith("[ERROR]"):
            logger.warning("hierarchical: window %d/%d failed: %s",
                           i + 1, len(windows), raw[:200])
            skips.append({
                "window_index": i,
                "outcome": "exception",
                "reason": raw[:300],
                "affect_intensity": affect.max_boost,
                "affect_valence": affect.valence,
            })
            parse_errors += 1
            continue
        cost_calls += 1
        try:
            obs, skip_reason = _parse_extract_response(raw, affect=affect, drop_stats=drop_stats)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hierarchical: window %d/%d parse failed: %s",
                           i + 1, len(windows), exc)
            skips.append({"window_index": i, "outcome": "parse_error", "reason": str(exc)})
            parse_errors += 1
            continue
        if obs:
            productive_windows += 1
            apply_affect_prior(obs, affect)
        else:
            skip_record: dict = {
                "window_index": i,
                "outcome": "empty_or_skipped",
                "affect_intensity": affect.max_boost,
                "affect_valence": affect.valence,
            }
            if skip_reason is not None:
                skip_record["reason"] = skip_reason
            skips.append(skip_record)
        all_obs.extend(obs)
        logger.info(
            "hierarchical: window %d/%d → %d obs (affect=%s/%.2f)",
            i + 1, len(windows), len(obs), affect.valence, affect.max_boost,
        )

    deduped, dropped = _dedupe_observations(all_obs)

    # Stage 1.5 — issue-card synthesis (replaces Wu 2021 refine)
    cards: list[dict] = []
    orphans: list[dict] = deduped
    synthesis_stats: dict = {"outcome": "skipped"}
    affect_summary = _aggregate_session_affect(affect_signals)
    if refine and deduped:
        synopsis = summarize(entries, max_chars=6000)
        cards, orphans, synthesis_stats = synthesize_issue_cards(
            deduped, synopsis, session_affect_summary=affect_summary
        )
        # Merge LLM-derived card affect into session affect (Bug 2 fix)
        affect_summary = _merge_card_affect(cards, affect_summary)
        if synthesis_stats.get("outcome") == "ok":
            cost_calls += 1
            logger.info(
                "issue_synthesis: %d→%d cards + %d orphans",
                len(deduped), synthesis_stats.get("card_count", 0),
                synthesis_stats.get("orphan_count", 0),
            )
        else:
            logger.info("issue_synthesis: %s — keeping flat obs as orphans",
                        synthesis_stats.get("outcome"))

    return {
        "observations": orphans,  # flat fallback / orphans
        "issue_cards": cards,
        "windows": len(windows),
        "raw_count": len(all_obs),
        "dropped_duplicates": dropped,
        "low_importance_dropped": drop_stats.get("low_importance_dropped", 0),
        "parse_errors_repaired": drop_stats.get("parse_errors_repaired", 0),
        "post_dedupe_count": len(deduped),
        "synthesis": synthesis_stats,
        "skips": skips,
        "affect_signals": [a.to_dict() for a in affect_signals],
        "session_affect": affect_summary,
        "productive_windows": productive_windows,
        "parse_errors": parse_errors,
        "cost_calls": cost_calls,
    }


def _merge_card_affect(cards: list[dict], base: dict) -> dict:
    """Reconcile LLM-derived affect fields from issue cards into the session affect summary.

    Solves the somatic affect blind spot: somatic detectors cannot see compiler errors,
    behavioral corrections, or non-lexical pushback — but LLM issue cards already carry
    that signal in user_reaction and user_affect_valence. No new LLM call required.

    Args:
        cards: Issue cards returned by synthesize_issue_cards (list of dicts).
        base: Session affect dict from _aggregate_session_affect.

    Returns:
        A new dict (copy of base) with card-derived affect merged in.
    """
    if not cards:
        return base

    result = dict(base)

    # Collect card valences and reactions
    card_valences = [
        c["user_affect_valence"]
        for c in cards
        if c.get("user_affect_valence") is not None
    ]
    card_reactions_raw = [
        c["user_reaction"]
        for c in cards
        if c.get("user_reaction") is not None
    ]

    # Merge valence: if base is neutral and cards carry non-neutral signal, override
    base_valence = result.get("dominant_valence", "neutral")
    non_neutral = [v for v in card_valences if v != "neutral"]
    if non_neutral:
        # Count occurrences to find mode
        counts: dict[str, int] = {}
        for v in non_neutral:
            counts[v] = counts.get(v, 0) + 1
        dominant_card_valence = max(counts.items(), key=lambda x: x[1])[0]

        if base_valence == "neutral":
            result["dominant_valence"] = dominant_card_valence
        elif base_valence != dominant_card_valence and base_valence != "mixed":
            # Both base and cards carry distinct non-neutral valences
            result["dominant_valence"] = "mixed"
        # If base_valence == dominant_card_valence, no change needed

    # Accumulate card reactions (deduped, preserve order, cap at 8)
    seen_reactions: set[str] = set()
    deduped_reactions: list[str] = []
    for r in card_reactions_raw:
        if r not in seen_reactions:
            seen_reactions.add(r)
            deduped_reactions.append(r)
            if len(deduped_reactions) >= 8:
                break
    result["card_reactions"] = deduped_reactions

    return result


def _aggregate_session_affect(signals: list[WindowAffect]) -> dict:
    """Roll up per-window affect into a session-level summary.

    Used as input to issue-card synthesis so the LLM can attribute
    user_reaction even on cards drawn from multiple windows.
    """
    if not signals:
        return {"valence": "neutral", "intensity": 0.0}
    valences = [s.valence for s in signals if s.valence != "neutral"]
    counts: dict[str, int] = {}
    for v in valences:
        counts[v] = counts.get(v, 0) + 1
    dominant = (
        max(counts.items(), key=lambda x: x[1])[0]
        if counts else "neutral"
    )
    if "friction" in counts and "delight" in counts:
        dominant = "mixed"
    quotes: list[str] = []
    for s in signals:
        for q in s.evidence_quotes:
            if q not in quotes:
                quotes.append(q)
            if len(quotes) >= 6:
                break
        if len(quotes) >= 6:
            break
    return {
        "dominant_valence": dominant,
        "max_intensity": max(s.max_boost for s in signals),
        "any_repetition": any(s.has_repetition for s in signals),
        "any_pushback": any(s.has_pushback for s in signals),
        "evidence_quotes": quotes,
        "windows_with_signal": sum(1 for s in signals if s.max_boost > 0),
        "windows_total": len(signals),
    }


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
        # W5 lean schema: facts[] is the canonical field. obs["content"] is
        # the legacy pre-W5 single-string field. Support both for backward
        # compatibility with any in-flight ephemeral writers.
        if "facts" in obs and isinstance(obs["facts"], list):
            content_text = "\n".join(f"- {f}" for f in obs["facts"] if f)
        elif "content" in obs:
            content_text = obs["content"]
        else:
            continue  # malformed observation — skip rather than crash
        kind = obs.get("kind")
        mode = obs.get("mode")
        # Prefer W5 'kind'; fall back to legacy 'mode' for ephemeral header tag.
        obs_type = kind if kind in OBSERVATION_TYPES else (
            mode if mode in OBSERVATION_TYPES else None
        )
        lines.append(format_observation(content_text, obs_type=obs_type))

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

            entries, new_offset, _ = read_transcript_from(path, cursor.last_byte_offset)

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
