#!/usr/bin/env python3
"""
One-shot runner for hand-selected transcripts — bypasses the 25h discovery
window of `transcript_cron.py` so historical sessions can be ingested after
their cursors are reset to 0.

Supports two extraction modes:
  --mode flat          (default) one summarize() truncated rendering, one LLM call
  --mode hierarchical  overlapping-window map-reduce per Wu 2021 / Beltagy 2020 / Liu 2023
                       (see core.transcript_ingest.extract_observations_hierarchical)
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.cursors import CursorStore  # noqa: E402
from core.transcript import read_transcript_from, summarize  # noqa: E402
from core.transcript_ingest import (  # noqa: E402
    extract_observations,
    extract_observations_hierarchical,
    append_to_ephemeral,
    project_memory_dir,
)
from core.session_detector import detect_session_type  # noqa: E402
from core.self_reflection_extraction import (  # noqa: E402
    ExtractionRunStats,
    reflect_on_extraction,
    self_model_path,
    select_chunking,
    aggregate_audit,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [selected] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SELECTED_PREFIXES = ("8fcc5ec0", "418d1c86", "22d10440")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["flat", "hierarchical"], default="flat")
    parser.add_argument(
        "--chunking",
        choices=["stride", "user_anchored", "auto"],
        default="auto",
        help="Window selection: stride / user_anchored / auto (consult self-model)",
    )
    parser.add_argument("--window-chars", type=int, default=16000)
    parser.add_argument("--stride-chars", type=int, default=12800)
    parser.add_argument("--max-windows", type=int, default=12)
    parser.add_argument("--context-before", type=int, default=2)
    parser.add_argument("--context-after", type=int, default=8)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to dump per-session JSON results for diff analysis",
    )
    args = parser.parse_args()

    report: dict[str, dict] = {}

    with CursorStore() as store:
        rows = store._conn.execute(
            "SELECT session_id, transcript_path, last_byte_offset "
            "FROM transcript_cursors"
        ).fetchall()
        targets = [
            r for r in rows
            if any(r["session_id"].startswith(p) for p in SELECTED_PREFIXES)
        ]
        logger.info("Selected %d cursors mode=%s", len(targets), args.mode)
        for row in targets:
            session_id = row["session_id"]
            path = Path(row["transcript_path"])
            cursor_offset = row["last_byte_offset"]
            if not path.exists():
                logger.warning("skip %s — path missing", session_id[:12])
                continue
            file_size = path.stat().st_size
            logger.info(
                "session %s offset=%d size=%d delta=%d",
                session_id[:12], cursor_offset, file_size,
                file_size - cursor_offset,
            )
            entries, new_offset, cwd = read_transcript_from(path, cursor_offset)
            if not entries:
                logger.info("  no parsable entries — advancing cursor")
                store.upsert(session_id, str(path), new_offset, cwd=cwd)
                continue

            tool_uses: list[dict] = []
            for entry in entries:
                msg = entry.get("message") or {}
                if entry.get("type") == "tool_use" or msg.get("type") == "tool_use":
                    tool_name = entry.get("tool_name") or msg.get("name") or ""
                    if tool_name:
                        tool_uses.append({
                            "tool_name": tool_name,
                            "file_path": entry.get("input", {}).get("file_path") or "",
                        })
            session_type = detect_session_type(cwd, tool_uses or None)
            ntu = sum(
                1 for e in entries
                if e.get("role") == "user"
                and len(e.get("text", "")) >= 60
            )
            chosen_chunking = args.chunking
            if args.chunking == "auto":
                chosen_chunking = select_chunking(ntu, len(entries))
            logger.info(
                "  cwd=%s session_type=%s entries=%d ntu=%d chunking=%s",
                cwd, session_type, len(entries), ntu, chosen_chunking,
            )

            entry_record: dict = {
                "session_id": session_id,
                "path": str(path),
                "size_bytes": file_size,
                "entries": len(entries),
                "cwd": cwd,
                "session_type": session_type,
                "mode": args.mode,
            }

            cards: list[dict] = []
            if args.mode == "flat":
                rendered = summarize(entries)
                logger.info("  flat: rendered=%d chars", len(rendered))
                obs_list = extract_observations(rendered, session_type=session_type)
                entry_record.update({
                    "rendered_chars": len(rendered),
                    "windows": 1,
                    "raw_count": len(obs_list),
                    "dropped_duplicates": 0,
                })
            else:
                result = extract_observations_hierarchical(
                    entries,
                    session_type=session_type,
                    window_chars=args.window_chars,
                    stride_chars=args.stride_chars,
                    max_windows=args.max_windows,
                    chunking=chosen_chunking,
                    context_before=args.context_before,
                    context_after=args.context_after,
                )
                obs_list = result["observations"]
                cards = result.get("issue_cards", [])
                entry_record.update({
                    "rendered_chars": None,
                    "windows": result["windows"],
                    "raw_count": result["raw_count"],
                    "dropped_duplicates": result["dropped_duplicates"],
                    "post_dedupe_count": result.get("post_dedupe_count", 0),
                    "issue_cards": cards,
                    "synthesis": result.get("synthesis", {}),
                    "affect_signals": result.get("affect_signals", []),
                    "productive_windows": result.get("productive_windows", 0),
                    "parse_errors": result.get("parse_errors", 0),
                    "cost_calls": result.get("cost_calls", 0),
                    "skips": result["skips"],
                })
                logger.info(
                    "  hierarchical: %d windows raw=%d deduped→%d cards=%d orphans=%d",
                    result["windows"], result["raw_count"],
                    result.get("post_dedupe_count", 0),
                    len(cards), len(obs_list),
                )

            for obs in obs_list:
                obs.setdefault("session_type", session_type)
            mem_dir = project_memory_dir(path)
            n = append_to_ephemeral(mem_dir, obs_list, dry_run=False)
            store.upsert(session_id, str(path), new_offset, cwd=cwd)
            entry_record["appended"] = n
            entry_record["observations"] = obs_list
            report[session_id] = entry_record
            logger.info(
                "  → %d orphan observation(s) appended (cards held in report)",
                n,
            )

            # ---- self-reflection on this session's run ----
            if args.mode == "hierarchical":
                affect_quotes_used = sum(
                    1 for c in entry_record.get("issue_cards", [])
                    if c.get("user_reaction")
                )
                affect_signals_total = sum(
                    1 for a in entry_record.get("affect_signals", [])
                    if a.get("max_boost", 0) > 0
                )
                stats = ExtractionRunStats(
                    session_id=session_id,
                    session_type=session_type,
                    chunking=chosen_chunking,
                    windows=entry_record.get("windows", 0),
                    productive_windows=entry_record.get("productive_windows", 0),
                    raw_observations=entry_record.get("raw_count", 0),
                    final_observations=len(obs_list) + len(cards),
                    issue_cards=len(cards),
                    orphans=len(obs_list),
                    skipped_windows=len(entry_record.get("skips", [])),
                    parse_errors=entry_record.get("parse_errors", 0),
                    affect_signals_total=affect_signals_total,
                    affect_quotes_used=affect_quotes_used,
                    nontrivial_user_turn_count=ntu,
                    entry_count=len(entries),
                    cost_calls=entry_record.get("cost_calls", 0),
                )
                reflections = reflect_on_extraction(stats)
                entry_record["self_observations"] = [r.to_dict() for r in reflections]
                if reflections:
                    logger.info(
                        "  self-reflection: %d rule(s) fired (%s)",
                        len(reflections),
                        ", ".join(r.rule_id for r in reflections),
                    )
                else:
                    logger.info("  self-reflection: no rules fired")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, default=str))
        logger.info("report written to %s", args.report)

    if args.mode == "hierarchical":
        smp = self_model_path()
        if smp.exists():
            logger.info("self-model refreshed at %s", smp)
        agg = aggregate_audit()
        if agg:
            confirmed = sum(1 for s in agg.values() if s.get("confidence") == "confirmed")
            tentative = sum(1 for s in agg.values() if s.get("confidence") == "tentative")
            logger.info(
                "self-model aggregate: %d rule(s) — %d confirmed, %d tentative",
                len(agg), confirmed, tentative,
            )
            for rid in sorted(agg.keys()):
                slot = agg[rid]
                logger.info(
                    "  %s: %d fires (%s)",
                    rid, slot["fire_count"], slot["confidence"],
                )


if __name__ == "__main__":
    main()
