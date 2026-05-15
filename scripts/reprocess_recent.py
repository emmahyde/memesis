#!/usr/bin/env python3
"""Batch reprocess recent transcripts through the full memory pipeline.

Optimized for throughput: extraction runs in parallel via call_llm_batch
(haiku for speed/cost), consolidation runs sequentially (DB writes need order).

Usage:
    uv run python3 scripts/reprocess_recent.py --days 7
    uv run python3 scripts/reprocess_recent.py --days 7 --concurrency 10
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.crystallizer import Crystallizer
from core.database import close_db, init_db
from core.lifecycle import LifecycleManager
from core.llm import call_llm_batch
from core.models import Memory
from core.prompts import format_extract_prompt
from core.session_detector import detect_session_type
from core.transcript import read_transcript_from, summarize
from core.transcript_ingest import append_to_ephemeral, project_memory_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [reprocess] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)
# Quiet noisy peewee/anthropic logs
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("core.llm").setLevel(logging.WARNING)


def _stage_counts() -> dict[str, int]:
    return {
        s: Memory.select().where(Memory.stage == s, Memory.archived_at.is_null()).count()
        for s in ("ephemeral", "consolidated", "crystallized", "instinctive")
    }


def _prep_transcript(jsonl: Path) -> tuple[str, str, list[dict]] | None:
    """Read transcript, render, detect session type. Returns (rendered, session_type, []) or None."""
    entries, _, _ = read_transcript_from(jsonl, 0)
    if not entries:
        return None
    rendered = summarize(entries)
    session_cwd = None
    tool_uses = []
    for entry in entries:
        msg = entry.get("message") or {}
        if not session_cwd:
            session_cwd = entry.get("cwd") or msg.get("cwd")
        if entry.get("type") == "tool_use" or msg.get("type") == "tool_use":
            tool_name = entry.get("tool_name") or msg.get("name") or ""
            file_path = entry.get("input", {}).get("file_path") or ""
            if tool_name:
                tool_uses.append({"tool_name": tool_name, "file_path": file_path})
    session_type = detect_session_type(session_cwd, tool_uses or None)
    return rendered, session_type, []


def _parse_extract_response(raw: str, session_type: str) -> list[dict]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        kept = [o for o in parsed if o.get("importance", 0) >= 0.3]
        for o in kept:
            o.setdefault("session_type", session_type)
        return kept
    if isinstance(parsed, dict) and parsed.get("skipped"):
        return []
    return []


def _extract_all(prepped: list[tuple[Path, str, str]], model: str, concurrency: int) -> list[tuple[Path, list[dict]]]:
    """Run extraction LLM calls in parallel for all transcripts."""
    prompts = [
        format_extract_prompt(transcript=rendered, session_type=stype, affect_hint="")
        for _, rendered, stype in prepped
    ]
    logger.info("Extracting observations for %d transcripts (model=%s, concurrency=%d)",
                len(prompts), model, concurrency)
    raws = call_llm_batch(prompts, max_tokens=8192, model=model, max_concurrency=concurrency)
    results = []
    for (jsonl, _, stype), raw in zip(prepped, raws):
        obs = _parse_extract_response(raw, stype)
        results.append((jsonl, obs))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--project-slug", default="-Users-emmahyde-projects-memesis")
    parser.add_argument("--max", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=10, help="Parallel LLM calls during extract")
    parser.add_argument("--extract-model", default="claude-haiku-4-5-20251001", help="Cheap model for extraction")
    parser.add_argument("--skip-crystallize", action="store_true")
    args = parser.parse_args()

    project_dir = Path.home() / ".claude" / "projects" / args.project_slug
    if not project_dir.exists():
        sys.exit(f"project dir not found: {project_dir}")

    cutoff = datetime.now() - timedelta(days=args.days)
    transcripts = sorted(
        [p for p in project_dir.glob("*.jsonl") if datetime.fromtimestamp(p.stat().st_mtime) >= cutoff],
        key=lambda p: p.stat().st_mtime,
    )
    if args.max:
        transcripts = transcripts[: args.max]
    if not transcripts:
        sys.exit(f"no transcripts in last {args.days} days")

    logger.info("Found %d transcripts in last %d days", len(transcripts), args.days)

    mem_dir = project_memory_dir(transcripts[0])
    init_db(base_dir=str(mem_dir))

    # Phase 1: prep (cheap, sequential — reads jsonl files)
    t0 = time.time()
    prepped = []
    for jsonl in transcripts:
        res = _prep_transcript(jsonl)
        if res is None:
            continue
        rendered, stype, _ = res
        prepped.append((jsonl, rendered, stype))
    logger.info("Prep: %d non-empty transcripts in %.1fs", len(prepped), time.time() - t0)

    # Phase 2: parallel extract via call_llm_batch
    t1 = time.time()
    extracted = _extract_all(prepped, args.extract_model, args.concurrency)
    logger.info("Extract: done in %.1fs (%.1fs/transcript avg)",
                time.time() - t1, (time.time() - t1) / max(1, len(prepped)))

    # Phase 3: sequential consolidation (DB writes)
    before = _stage_counts()
    logger.info("Stage counts before consolidation: %s", before)
    lifecycle = LifecycleManager()
    consolidator = Consolidator(lifecycle=lifecycle)
    totals = {"extracted": 0, "appended": 0, "kept": 0, "pruned": 0, "promoted": 0, "errors": 0}

    t2 = time.time()
    for i, (jsonl, obs_list) in enumerate(extracted, 1):
        if not obs_list:
            continue
        totals["extracted"] += len(obs_list)
        try:
            eph_path = mem_dir / "ephemeral" / f"session-{date.today().isoformat()}.md"
            if eph_path.exists():
                eph_path.unlink()
            totals["appended"] += append_to_ephemeral(mem_dir, obs_list, dry_run=False)
            if not eph_path.exists():
                continue
            result = consolidator.consolidate_session(str(eph_path), session_id=jsonl.stem)
            for k in ("kept", "pruned", "promoted"):
                totals[k] += len(result.get(k, []))
        except Exception as exc:
            logger.exception("FAIL %s: %s", jsonl.name, exc)
            totals["errors"] += 1
            continue
        if i % 10 == 0:
            elapsed = time.time() - t2
            rate = i / elapsed
            eta = (len(extracted) - i) / rate
            logger.info("[%d/%d] kept=%d pruned=%d promoted=%d (eta %ds)",
                        i, len(extracted), totals["kept"], totals["pruned"], totals["promoted"], int(eta))

    logger.info("Consolidate: done in %.1fs", time.time() - t2)

    if not args.skip_crystallize:
        crystallizer = Crystallizer(lifecycle)
        crystallized = crystallizer.crystallize_candidates()
        logger.info("Crystallized %d memories", len(crystallized))

    after = _stage_counts()
    logger.info("Stage counts after: %s", after)
    logger.info("Totals: %s", totals)
    logger.info("Grand total elapsed: %.1fs", time.time() - t0)
    close_db()


if __name__ == "__main__":
    main()
