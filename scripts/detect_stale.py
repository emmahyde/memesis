#!/usr/bin/env python3
"""
LLM-powered staleness detector.

Finds consolidated/crystallized memories that haven't been reinforced
recently and checks them against recent session observations. Flags
memories whose content may be outdated, superseded, or no longer true.

Usage:
    python3 scripts/detect_stale.py --base-dir ~/.claude/memory
    python3 scripts/detect_stale.py --base-dir ~/.claude/memory --apply
    python3 scripts/detect_stale.py --base-dir ~/.claude/memory --days 14 --limit 5

Flags:
    --days N      Minimum days since last reinforcement to consider stale (default: 30)
    --limit N     Max memories to evaluate per run (default: 10, LLM cost control)
    --importance  Max importance to check — skip high-confidence memories (default: 0.8)
    --sessions N  Number of recent sessions to pull observations from (default: 5)
    --apply       Actually reduce importance of OUTDATED memories by 0.2
    --out PATH    Write report to file
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import shorten

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.database import init_db, close_db
from core.llm import call_llm
from core.models import Memory, Observation

_STALENESS_PROMPT = """\
You are reviewing a memory for staleness. The memory was recorded at some point,
but time has passed and new observations have come in. Your job is to judge whether
the memory is still accurate, possibly outdated, or clearly superseded.

MEMORY TO EVALUATE:
Title: {title}
Stage: {stage}
Content: {content}

RECENT SESSION OBSERVATIONS (most recent first):
{observations}

Respond with a JSON object containing exactly these fields:
{{
  "verdict": "VALID" | "OUTDATED" | "UNCERTAIN",
  "confidence": 0.0-1.0,
  "reasoning": "one sentence explanation"
}}

Verdicts:
- VALID: Memory content is still accurate and relevant based on recent observations
- OUTDATED: Recent observations contradict or supersede this memory's content
- UNCERTAIN: Cannot determine from available context

Be conservative — prefer UNCERTAIN over OUTDATED unless there is clear contradiction.
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _truncate(text: str | None, width: int = 100) -> str:
    if not text:
        return "(none)"
    return shorten(text.strip(), width=width, placeholder="…")


def _days_since(ts_str: str | None) -> float | None:
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        except ValueError:
            continue
    return None


def _stale_candidates(
    min_days: int,
    max_importance: float,
    limit: int,
) -> list[Memory]:
    candidates = list(
        Memory.select()
        .where(
            Memory.stage.in_(["consolidated", "crystallized"]),
            Memory.importance <= max_importance,
            Memory.archived_at.is_null(True),
        )
        .order_by(Memory.importance.asc())
        .limit(limit * 3)
    )

    # Filter by last_used_at or updated_at < cutoff
    filtered = []
    for m in candidates:
        last_activity = m.last_used_at or m.created_at
        age = _days_since(last_activity)
        if age is None or age >= min_days:
            filtered.append(m)
        if len(filtered) >= limit:
            break

    return filtered


def _recent_observations(n_sessions: int) -> str:
    """Return formatted recent observations as a string."""
    sessions: set[str] = set()
    for row in Observation.select(Observation.session_id).where(
        Observation.session_id.is_null(False)
    ).distinct():
        if row.session_id:
            sessions.add(row.session_id)

    recent_sessions = sorted(sessions, reverse=True)[:n_sessions]
    if not recent_sessions:
        return "(no recent observations)"

    obs = list(
        Observation.select()
        .where(Observation.session_id.in_(recent_sessions))
        .order_by(Observation.created_at.desc())
        .limit(30)
    )

    if not obs:
        return "(no observations found)"

    parts = []
    for o in obs:
        content = (o.filtered_content or o.content or "").strip()
        if content:
            parts.append(f"- {content[:200]}")

    return "\n".join(parts[:20]) if parts else "(no content in observations)"


def _check_memory(
    memory: Memory,
    observations_text: str,
) -> dict:
    prompt = _STALENESS_PROMPT.format(
        title=memory.title or "(untitled)",
        stage=memory.stage,
        content=(memory.content or "")[:800],
        observations=observations_text,
    )

    try:
        response = call_llm(
            model="claude-haiku-4-5-20251001",
            system="You are a memory system evaluator. Respond with valid JSON only.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        text = response.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        return {
            "verdict": result.get("verdict", "UNCERTAIN"),
            "confidence": float(result.get("confidence", 0.5)),
            "reasoning": result.get("reasoning", ""),
            "error": None,
        }
    except Exception as e:
        return {
            "verdict": "UNCERTAIN",
            "confidence": 0.0,
            "reasoning": "",
            "error": str(e),
        }


def _apply_importance_decay(memory: Memory, decay: float = 0.2) -> None:
    new_importance = max(0.0, round((memory.importance or 0.5) - decay, 3))
    Memory.update(importance=new_importance).where(Memory.id == memory.id).execute()


def _render(
    candidates: list[Memory],
    results: list[dict],
    applied: bool,
    min_days: int,
) -> str:
    lines: list[str] = []

    def h(level: int, text: str) -> None:
        lines.append(f"\n{'#' * level} {text}")

    h(1, f"Staleness Detection Report — {_now_iso()}")
    lines.append(f"\nCandidates evaluated: **{len(candidates)}** · "
                 f"Threshold: ≥{min_days} days since last use · "
                 f"Apply: {'yes (importance reduced)' if applied else 'dry-run'}")

    if not candidates:
        lines.append("\n✅ No stale candidates found matching criteria.")
        return "\n".join(lines) + "\n"

    outdated = sum(1 for r in results if r["verdict"] == "OUTDATED")
    valid = sum(1 for r in results if r["verdict"] == "VALID")
    uncertain = sum(1 for r in results if r["verdict"] == "UNCERTAIN")
    errors = sum(1 for r in results if r.get("error"))

    lines.append(f"\n**OUTDATED:** {outdated} · **VALID:** {valid} · **UNCERTAIN:** {uncertain}"
                 + (f" · **Errors:** {errors}" if errors else ""))

    h(2, "Results")
    for memory, result in zip(candidates, results):
        verdict = result["verdict"]
        icon = {"OUTDATED": "🔴", "VALID": "✅", "UNCERTAIN": "🟡"}.get(verdict, "❓")
        lines.append(f"\n{icon} **{verdict}** (confidence: {result['confidence']:.2f})")
        lines.append(f"   - Title: _{_truncate(memory.title, 80)}_")
        lines.append(f"   - Stage: `{memory.stage}` · Importance: `{memory.importance:.2f}`")
        lines.append(f"   - Reasoning: {result['reasoning'] or '(none)'}")
        if result.get("error"):
            lines.append(f"   - ⚠ LLM error: `{result['error']}`")
        if applied and verdict == "OUTDATED":
            new_imp = max(0.0, round((memory.importance or 0.5) - 0.2, 3))
            lines.append(f"   - ✏ Importance reduced: `{memory.importance:.2f}` → `{new_imp:.2f}`")

    if not applied and outdated > 0:
        lines.append(f"\n_Run with `--apply` to reduce importance of {outdated} OUTDATED memory/memories._")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-dir", default=os.path.expanduser("~/.claude/memory"))
    parser.add_argument("--days", type=int, default=30, metavar="N")
    parser.add_argument("--limit", type=int, default=10, metavar="N")
    parser.add_argument("--importance", type=float, default=0.8, metavar="F",
                        help="Max importance score to consider (default: 0.8)")
    parser.add_argument("--sessions", type=int, default=5, metavar="N")
    parser.add_argument("--apply", action="store_true",
                        help="Apply importance decay to OUTDATED memories")
    parser.add_argument("--out", type=str, default=None, metavar="PATH")
    args = parser.parse_args()

    init_db(args.base_dir)
    try:
        candidates = _stale_candidates(args.days, args.importance, args.limit)
        if not candidates:
            print(f"No stale candidates (consolidated/crystallized, ≥{args.days} days idle, "
                  f"importance ≤ {args.importance}).")
            return

        print(f"Evaluating {len(candidates)} candidates via LLM…", file=sys.stderr)
        observations_text = _recent_observations(args.sessions)

        results = []
        for i, memory in enumerate(candidates, 1):
            print(f"  [{i}/{len(candidates)}] {_truncate(memory.title, 60)}", file=sys.stderr)
            result = _check_memory(memory, observations_text)
            results.append(result)

            if args.apply and result["verdict"] == "OUTDATED":
                _apply_importance_decay(memory)

        report = _render(candidates, results, args.apply, args.days)

        if args.out:
            Path(args.out).write_text(report)
            print(f"Report written to {args.out}")
        else:
            print(report)
    finally:
        close_db()


if __name__ == "__main__":
    main()
