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
from core.llm import call_llm, call_llm_batch, _repair_json  # noqa: F401
from core.prompts import (
    OBSERVATION_EXTRACT_PROMPT,
    OBSERVATION_TYPES,
    format_observation,
    format_extract_prompt,
    _OBSERVATION_EXTRACT_PROMPT_TEMPLATE,
    SESSION_TYPE_GUIDANCE,
)
from core.session_detector import detect_session_type, RESEARCH_PATH_HINTS_PREPEND
from core.extraction_affect import aggregate_window_affect, apply_affect_prior, format_affect_hint, WindowAffect
from core.issue_cards import synthesize_issue_cards
from core.card_validators import _card_evidence_load_bearing
from core.rule_registry import ParameterOverrides
from core.trace import get_active_writer

# Module-level prefilter knobs are deprecated — settings now live on
# `ParameterOverrides` (core.rule_registry) so the closed-loop registry can
# flip them off if a future rule (e.g. prefilter_dropping_signal) confirms
# the gate is over-aggressive. Constants retained for backward compatibility
# with any external caller still importing them; the pipeline reads from
# `overrides.prefilter_*` exclusively.
PREFILTER_RESEARCH_NEUTRAL: bool = True

# Reframe A — stateful incremental extraction.
# When True, each window's prompt is augmented with top-K similar prior
# observations extracted from earlier windows in the same session. This
# reduces cross-window paraphrase re-extraction by giving Stage 1 explicit
# dedup context. Default: False (opt-in) until validated end-to-end.
# See .context/PLAN-tier2-audit-fixes.md Task 4.1 and CONTEXT Item 18.
REFRAME_A_ENABLED: bool = True
PREFILTER_DENSITY_THRESHOLD_CHARS: int = 200
PREFILTER_TTR_THRESHOLD: float = 0.25
PREFILTER_OBSERVER_DENSITY_THRESHOLD_CHARS: int = 400

logger = logging.getLogger(__name__)


def _avg_chars_per_entry(entries: list[dict]) -> float:
    if not entries:
        return 0.0
    total = 0
    for e in entries:
        content = e.get("content") or e.get("text") or ""
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += len(part.get("text") or "")
    return total / len(entries)


def _is_research_observer(cwd: str | None) -> bool:
    if not cwd:
        return False
    return any(hint in cwd for hint in RESEARCH_PATH_HINTS_PREPEND)


def _compute_ttr(text: str) -> float:
    if not text:
        return 0.0
    tokens = text.split()
    if not tokens:
        return 0.0
    return len(set(tokens)) / len(tokens)

REFINE_PROMPT = """You are reviewing observations extracted from overlapping windows of a single session.
Your job: identify cross-window patterns, merge near-duplicates that the
content-hash deduper missed (same finding, different phrasing), and assign the
final session-level importance score.

INCOMING IMPORTANCE FIELD IS WINDOW-LOCAL SALIENCE.
Stage 1 produced each observation's `importance` based on what it could see in
ONE window. You have the full session synopsis, the session-level affect summary,
and every observation across every window. You own the final importance score.

PROMOTE importance when:
  - The same finding appears across multiple windows (recurrence = durability)
  - The session affect summary shows pushback / repetition / friction near it
  - Later windows reinforce or build on the observation
  - It interacts with another observation (e.g., a constraint that shapes a fix)

DEMOTE importance when:
  - The window-local score was inflated for a passing aside
  - The finding was contradicted or revised in a later window
  - It is subsumed by a stronger observation you are merging into

Final importance MUST be in [0.0, 1.0] using the same anchors Stage 1 used:
  0.2 routine, 0.5 useful, 0.8 load-bearing, 0.95 correction/hard constraint.

DO NOT invent new observations. Only refine, merge, or re-score existing ones.
DO NOT drop observations unless they are confirmed duplicates of others in
the same input list (in which case keep the higher-importance copy or merge
their facts into one observation).

SESSION SYNOPSIS:
{synopsis}

SESSION AFFECT (for importance calibration):
{affect_summary}

OBSERVATIONS FROM HIERARCHICAL EXTRACTION (JSON list, `importance` = window-local salience):
{observations_json}

Output ONLY a JSON object:
{{
  "refined": [/* observations in same schema as input; `importance` is now session-level */],
  "merges": [/* {{"merged_into_index": int, "from_indices": [int,...], "reason": str}} */],
  "rescores": [/* {{"index": int, "old": float, "new": float, "reason": str}} */]
}}

If no refinement is warranted, return {{"refined": <input observations unchanged>, "merges": [], "rescores": []}}.
"""


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
      2. JSON object {"skipped": true, "failed_gate": "...", "reason": "..."}
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
    # #33: use format_extract_prompt() to inject per-session-type guidance
    raw = call_llm(format_extract_prompt(
        transcript=rendered, session_type=session_type, affect_hint=""
    ))
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
            failed_gate = parsed.get("failed_gate", "unspecified")
            logger.info(
                "extract_observations: intentional skip [gate=%s] — %s",
                failed_gate, reason,
            )
            _write_ingest_trace("skipped", f"[{failed_gate}] {reason}", raw[:80])
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

    On JSONDecodeError for an array-shaped response, delegates to _repair_json
    (trailing-comma + truncated-array repair) before falling back to [].
    Successful repair is logged at WARNING and increments
    drop_stats["parse_errors_repaired"].

    Skip records returned by the caller now include:
      - raw_response_excerpt: first 500 chars of the raw LLM response
      - llm_reason: parsed reason string if the LLM emitted {"reason": "..."}

    drop_stats: optional mutable dict; receives:
      - "low_importance_dropped": count of obs filtered at importance < 0.3
      - "parse_errors_repaired": count of successfully repaired truncated arrays
      - "parse_errors": count of unrecoverable parse failures
      (Phase E audit instrumentation, 2026-04-28).
    """
    raw_excerpt = raw[:500]
    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        repaired_text = _repair_json(raw, drop_stats=drop_stats)
        if repaired_text is not None:
            try:
                candidate = json.loads(repaired_text)
                if isinstance(candidate, list):
                    logger.warning(
                        "_parse_extract_response: JSON repaired — %d obs recovered",
                        len(candidate),
                    )
                    parsed = candidate
            except json.JSONDecodeError:
                repaired_text = None
        if parsed is None:
            logger.warning("_parse_extract_response: JSON decode failed: %s", exc)
            if drop_stats is not None:
                drop_stats["parse_errors"] = drop_stats.get("parse_errors", 0) + 1
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
            failed_gate = parsed.get("failed_gate", "unspecified")
            tagged = f"[{failed_gate}] {reason}"
            considered = parsed.get("considered")
            # Task #12: if skip lacks `considered` field or affect shows signal,
            # downgrade to warning — don't fully trust the skip.
            affect_boost = getattr(affect, "max_boost", 0.0) if affect is not None else 0.0
            if not considered or affect_boost > 0:
                logger.warning(
                    "_parse_extract_response: skip downgraded — considered=%r affect_boost=%.2f — %s",
                    considered, affect_boost, tagged,
                )
            else:
                logger.info("_parse_extract_response: intentional skip — %s", tagged)
            _write_ingest_trace("skipped", tagged, raw_excerpt[:80])
            return [], tagged
        logger.warning(
            "_parse_extract_response: LLM returned a dict without 'skipped' key — treating as malformed"
        )
        _write_ingest_trace("rejected", "dict response without skipped=true", raw_excerpt[:80])
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


def _refine_observations(
    observations: list[dict],
    synopsis: str,
    affect_summary: dict,
) -> tuple[list[dict], dict]:
    """Run the Wu-2021 refine pass: merge cross-window paraphrases and rescore importance.

    Empty input returns immediately without an LLM call.
    Lists of ≤3 observations are skipped (no merges possible on tiny sets).

    Returns (refined_observations, stats_dict).
    """
    if not observations:
        return [], {"merges": 0, "rescores": 0, "outcome": "empty"}

    if len(observations) <= 3:
        return observations, {"merges": 0, "rescores": 0, "outcome": "skipped_too_few"}

    prompt = REFINE_PROMPT.format(
        synopsis=synopsis[:6000],
        affect_summary=json.dumps(affect_summary),
        observations_json=json.dumps(observations, indent=2),
    )

    try:
        raw = call_llm(prompt, max_tokens=8192)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_refine_observations: LLM call failed: %s", exc)
        return observations, {"merges": 0, "rescores": 0, "outcome": "llm_error", "error": str(exc)}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("_refine_observations: failed to parse LLM response as JSON")
        return observations, {"merges": 0, "rescores": 0, "outcome": "parse_error"}

    refined = parsed.get("refined")
    if not isinstance(refined, list):
        logger.warning("_refine_observations: response missing 'refined' list key")
        return observations, {"merges": 0, "rescores": 0, "outcome": "missing_refined"}

    merges = parsed.get("merges", [])
    rescores = parsed.get("rescores", [])
    return refined, {
        "merges": len(merges),
        "rescores": len(rescores),
        "outcome": "ok",
        "merges_detail": merges,
        "rescores_detail": rescores,
    }


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
    overrides: ParameterOverrides | None = None,
    cwd: str | None = None,
    session_id: str | None = None,
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
    # Apply confirmed-rule overrides (Stage 2 closed-loop). When `overrides`
    # is provided, it wins over kwargs — caller (run_selected_sessions) is
    # expected to pass either kwargs OR overrides, not both. See
    # `core/rule_registry.py` for how overrides are composed from confirmed
    # rules in the audit log.
    if overrides is None:
        overrides = ParameterOverrides()
    else:
        window_chars = overrides.window_chars
        stride_chars = overrides.stride_chars
        max_windows = overrides.max_windows
        if overrides.chunking_strategy is not None:
            chunking = overrides.chunking_strategy
    assert overrides is not None  # narrow for type checker

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

    # Three-stage cheapest-first prefilter. Each gate records distinct outcome.
    #   affect_gate    — neutral + no pushback/repetition + low session density
    #   observer_gate  — research + observer/agent cwd + tighter density
    #   entropy_gate   — per-window TTR below threshold (self-summarizing)
    avg_chars = _avg_chars_per_entry(entries)
    is_observer = _is_research_observer(cwd)
    density_low = avg_chars < overrides.prefilter_density_threshold_chars
    observer_density_low = avg_chars < overrides.prefilter_observer_density_threshold_chars
    prefiltered: list[int] = []
    if overrides.prefilter_research_neutral and session_type == "research":
        for i, a in enumerate(affect_signals):
            outcome: str | None = None
            reason: str | None = None
            affect_neutral = (
                a.max_boost == 0.0
                and not a.has_pushback
                and not a.has_repetition
            )
            if is_observer and observer_density_low and affect_neutral:
                outcome = "pre_filtered_observer"
                reason = "[observer_gate] research observer cwd + neutral affect + low density"
            elif density_low and affect_neutral:
                outcome = "pre_filtered_low_affect"
                reason = "[affect_gate] neutral research window + low session density"
            else:
                ttr = _compute_ttr(windows[i])
                if affect_neutral and ttr < overrides.prefilter_ttr_threshold:
                    outcome = "pre_filtered_low_entropy"
                    reason = f"[entropy_gate] low diversity ttr={ttr:.2f}"
            if outcome:
                prefiltered.append(i)
                skips.append({
                    "window_index": i,
                    "outcome": outcome,
                    "reason": reason,
                    "affect_intensity": a.max_boost,
                    "affect_valence": a.valence,
                    "avg_chars_per_entry": avg_chars,
                })
        if prefiltered:
            _keep = set(range(len(windows))) - set(prefiltered)
            windows = [w for i, w in enumerate(windows) if i in _keep]
            affect_signals = [a for i, a in enumerate(affect_signals) if i in _keep]

    # stage1_extract_start trace event
    _tw = get_active_writer()
    if _tw:
        _tw.emit("stage1", "stage1_extract_start", {
            "n_windows": len(windows),
            "session_id": session_id or "",
        })

    # Reframe A — stateful incremental extraction.
    # When REFRAME_A_ENABLED=True, each window's prompt is built sequentially
    # with top-K similar prior observations injected. This requires a sequential
    # loop (not batch) because each window's index state depends on prior outputs.
    # When False (default), the existing concurrent batch flow runs unchanged.
    cross_window_dedup_hits = 0
    svec = None  # SessionVecStore instance, or None when Reframe A is disabled

    if REFRAME_A_ENABLED and session_id is not None:
        from core.database import get_db_path
        from core.embeddings import embed_text
        from core.session_vec import SessionVecStore

        _db_path = get_db_path()
        if _db_path is not None:
            svec = SessionVecStore(_db_path, session_id)
            if not svec.available:
                logger.warning(
                    "hierarchical: SessionVecStore unavailable for session %s — running without Reframe A",
                    session_id,
                )
                svec = None

    if svec is not None:
        # --- Reframe A: sequential per-window loop ---
        # Stores (obs_idx, text) pairs for the index; obs_idx monotonically
        # increases across windows within this session.
        _reframe_obs_idx = 0
        # _session_obs maps obs_idx -> observation text (joined facts or content)
        _session_obs_texts: dict[int, str] = {}

        for i, (w, affect) in enumerate(zip(windows, affect_signals)):
            # Skip windows with zero affect when affect_pre_filter is on
            if overrides.affect_pre_filter and affect.max_boost == 0.0:
                skips.append({
                    "window_index": i,
                    "outcome": "pre_filtered_low_affect",
                    "affect_intensity": affect.max_boost,
                    "affect_valence": affect.valence,
                })
                continue

            # Query in-session index for top-3 similar prior observations
            prior_block = ""
            win_embedding = embed_text(w[:4000])
            if win_embedding is not None:
                prior_indices = svec.query_similar(win_embedding, k=3)
                if prior_indices:
                    cross_window_dedup_hits += 1
                    bullets = [
                        f"- {_session_obs_texts[idx]}"
                        for idx in prior_indices
                        if idx in _session_obs_texts
                    ]
                    if bullets:
                        prior_block = (
                            "PRIOR EXTRACTIONS from this session"
                            " (do not duplicate — these facts have already been captured"
                            " from earlier windows in this session):\n"
                            + "\n".join(bullets)
                        )

            prompt = format_extract_prompt(
                transcript=w,
                session_type=session_type,
                affect_hint=format_affect_hint(affect),
                prior_extractions=prior_block,
                recurrent_failure_patterns=overrides.recurrent_failure_patterns,
            )

            if len(w) > 12000:
                logger.warning(
                    "hierarchical (reframe_a): window %d/%d is large (%d chars)",
                    i + 1, len(windows), len(w),
                )

            raw_list = call_llm_batch(
                [prompt], max_concurrency=1, max_tokens=overrides.max_tokens_stage1,
            )
            raw = raw_list[0] if raw_list else "[ERROR] empty batch response"
            cost_calls += 1

            if raw.startswith("[ERROR]"):
                logger.warning(
                    "hierarchical (reframe_a): window %d/%d failed: %s",
                    i + 1, len(windows), raw[:200],
                )
                skips.append({
                    "window_index": i,
                    "outcome": "exception",
                    "reason": raw[:300],
                    "raw_response_excerpt": raw[:500],
                    "affect_intensity": affect.max_boost,
                    "affect_valence": affect.valence,
                })
                parse_errors += 1
                continue

            try:
                obs, skip_reason = _parse_extract_response(raw, affect=affect, drop_stats=drop_stats)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "hierarchical (reframe_a): window %d/%d parse failed: %s",
                    i + 1, len(windows), exc,
                )
                skips.append({
                    "window_index": i,
                    "outcome": "parse_error",
                    "reason": str(exc),
                    "raw_response_excerpt": raw[:500],
                })
                parse_errors += 1
                continue

            if obs:
                productive_windows += 1
                apply_affect_prior(obs, affect)

                # Embed each new observation and add to the in-session index.
                # Join facts[] or fall back to content for embedding text.
                for ob in obs:
                    facts = ob.get("facts") or []
                    obs_text = " ".join(facts) if facts else (ob.get("content") or "")
                    if obs_text:
                        obs_embedding = embed_text(obs_text[:4000])
                        if obs_embedding is not None:
                            svec.add(_reframe_obs_idx, obs_embedding)
                            _session_obs_texts[_reframe_obs_idx] = obs_text
                        _reframe_obs_idx += 1
            else:
                llm_reason: str | None = None
                try:
                    _parsed_raw = json.loads(raw)
                    if isinstance(_parsed_raw, dict) and "reason" in _parsed_raw:
                        llm_reason = _parsed_raw["reason"]
                except (json.JSONDecodeError, TypeError):
                    pass
                skip_record: dict = {
                    "window_index": i,
                    "outcome": "empty_or_skipped",
                    "raw_response_excerpt": raw[:500],
                    "affect_intensity": affect.max_boost,
                    "affect_valence": affect.valence,
                }
                if skip_reason is not None:
                    skip_record["reason"] = skip_reason
                if llm_reason is not None:
                    skip_record["llm_reason"] = llm_reason
                skips.append(skip_record)

            all_obs.extend(obs)
            logger.info(
                "hierarchical (reframe_a): window %d/%d → %d obs (affect=%s/%.2f)",
                i + 1, len(windows), len(obs), affect.valence, affect.max_boost,
            )

        # Drop the ephemeral session table after extraction
        svec.drop()

    else:
        # --- Default: concurrent LLM batch (Reframe A disabled) ---
        # agent-SDK serializes OAuth refresh internally so this no longer
        # races against itself the way raw subprocess did.
        # #33: use format_extract_prompt() to inject per-session-type guidance
        prompts = [
            format_extract_prompt(
                transcript=w,
                session_type=session_type,
                affect_hint=format_affect_hint(a),
                recurrent_failure_patterns=overrides.recurrent_failure_patterns,
            )
            for w, a in zip(windows, affect_signals)
        ]
        # affect_pre_filter: when `low_productive_rate` is confirmed, skip the
        # LLM call entirely on windows with no somatic affect signal. Recorded
        # as a `pre_filtered_low_affect` skip so we still account for them.
        if overrides.affect_pre_filter:
            active_indices: list[int] = [
                i for i, a in enumerate(affect_signals) if a.max_boost > 0.0
            ]
            for i, a in enumerate(affect_signals):
                if a.max_boost == 0.0:
                    skips.append({
                        "window_index": i,
                        "outcome": "pre_filtered_low_affect",
                        "affect_intensity": a.max_boost,
                        "affect_valence": a.valence,
                    })
            active_prompts = [prompts[i] for i in active_indices]
            active_responses = call_llm_batch(
                active_prompts, max_concurrency=4, max_tokens=overrides.max_tokens_stage1,
            )
            # Reassemble full-length response list with empty strings for skipped windows
            raw_responses = [""] * len(prompts)
            for src_i, dst_i in enumerate(active_indices):
                raw_responses[dst_i] = active_responses[src_i]
        else:
            raw_responses = call_llm_batch(
                prompts, max_concurrency=4, max_tokens=overrides.max_tokens_stage1,
            )

        for i, (_w, raw, affect) in enumerate(zip(windows, raw_responses, affect_signals)):
            # Pre-call warning: log if rendered window is unusually large
            if len(_w) > 12000:
                logger.warning(
                    "hierarchical: window %d/%d is large (%d chars) — may exceed effective attention range",
                    i + 1, len(windows), len(_w),
                )
            if raw.startswith("[ERROR]"):
                logger.warning("hierarchical: window %d/%d failed: %s",
                               i + 1, len(windows), raw[:200])
                skips.append({
                    "window_index": i,
                    "outcome": "exception",
                    "reason": raw[:300],
                    "raw_response_excerpt": raw[:500],
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
                skips.append({
                    "window_index": i,
                    "outcome": "parse_error",
                    "reason": str(exc),
                    "raw_response_excerpt": raw[:500],
                })
                parse_errors += 1
                continue
            if obs:
                productive_windows += 1
                apply_affect_prior(obs, affect)
            else:
                # Attempt to extract llm_reason from the raw response (if LLM emitted {"reason": ...})
                llm_reason: str | None = None
                try:
                    _parsed_raw = json.loads(raw)
                    if isinstance(_parsed_raw, dict) and "reason" in _parsed_raw:
                        llm_reason = _parsed_raw["reason"]
                except (json.JSONDecodeError, TypeError):
                    pass
                skip_record: dict = {
                    "window_index": i,
                    "outcome": "empty_or_skipped",
                    "raw_response_excerpt": raw[:500],
                    "affect_intensity": affect.max_boost,
                    "affect_valence": affect.valence,
                }
                if skip_reason is not None:
                    skip_record["reason"] = skip_reason
                if llm_reason is not None:
                    skip_record["llm_reason"] = llm_reason
                skips.append(skip_record)
            all_obs.extend(obs)
            logger.info(
                "hierarchical: window %d/%d → %d obs (affect=%s/%.2f)",
                i + 1, len(windows), len(obs), affect.valence, affect.max_boost,
            )

    deduped, dropped = _dedupe_observations(all_obs)

    # stage1_extract_end trace event (after dedup + drop-gate)
    _tw = get_active_writer()
    if _tw:
        _tw.emit("stage1", "stage1_extract_end", {
            "n_obs_pre_dedup": len(all_obs),
            "n_obs_post_dedup": len(deduped),
            "n_dropped": dropped,
        })

    # Stage 1.5 — Wu-2021 refine pass + issue-card synthesis
    cards: list[dict] = []
    orphans: list[dict] = deduped
    synthesis_stats: dict = {"outcome": "skipped"}
    refine_stats: dict = {"outcome": "skipped"}
    affect_summary = _aggregate_session_affect(affect_signals)
    if refine and deduped:
        synopsis = summarize(entries, max_chars=6000)
        deduped, refine_stats = _refine_observations(deduped, synopsis, affect_summary)
        if refine_stats.get("outcome") == "ok":
            cost_calls += 1
            logger.info(
                "refine: %d merges, %d rescores",
                refine_stats.get("merges", 0),
                refine_stats.get("rescores", 0),
            )
        _tw = get_active_writer()
        if _tw:
            _tw.emit("stage1.5", "stage15_synthesis_start", {
                "n_obs_input": len(deduped),
            })
        cards, orphans, synthesis_stats = synthesize_issue_cards(
            deduped,
            synopsis,
            session_affect_summary=affect_summary,
            synthesis_strict=overrides.synthesis_strict,
        )
        _tw = get_active_writer()
        if _tw:
            _tw.emit("stage1.5", "stage15_synthesis_end", {
                "n_cards": len(cards),
                "n_orphans": len(orphans),
                "n_invalid_indices_demoted": synthesis_stats.get("cards_invalid_indices_demoted", 0),
            })
        # Circular-evidence demotion: single-quote cards whose lone quote merely
        # restates the card body add no retrieval value — demote them to orphans.
        cards_demoted_circular = 0
        surviving_cards: list[dict] = []
        for card in cards:
            if len(card.get("evidence_quotes") or []) == 1 and not _card_evidence_load_bearing(card):
                orphans.append({
                    "kind": card.get("kind") or "fact",
                    "facts": [card["evidence_quotes"][0]],
                    "importance": card.get("importance", 0.4),
                    "knowledge_type": card.get("knowledge_type", "unknown"),
                    "scope": card.get("scope", "session"),
                    "demoted_from_card": True,
                })
                cards_demoted_circular += 1
            else:
                surviving_cards.append(card)
        cards = surviving_cards
        synthesis_stats["cards_demoted_circular"] = cards_demoted_circular
        # Merge LLM-derived card affect into session affect (Bug 2 fix)
        affect_summary = _merge_card_affect(cards, affect_summary)
        if synthesis_stats.get("outcome") == "ok":
            cost_calls += 1
            demoted_suffix = f" + {cards_demoted_circular} demoted" if cards_demoted_circular else ""
            logger.info(
                "issue_synthesis: %d→%d cards + %d orphans%s",
                len(deduped), synthesis_stats.get("card_count", 0),
                synthesis_stats.get("orphan_count", 0),
                demoted_suffix,
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
        "refine": refine_stats,
        "synthesis": synthesis_stats,
        "skips": skips,
        "affect_signals": [a.to_dict() for a in affect_signals],
        "session_affect": affect_summary,
        "productive_windows": productive_windows,
        "parse_errors": parse_errors,
        "cost_calls": cost_calls,
        "prefilter_skipped_count": len(prefiltered),
        "cross_window_dedup_hits": cross_window_dedup_hits,
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
