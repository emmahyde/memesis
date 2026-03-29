#!/usr/bin/env python3
"""
Seed consolidation results into the memesis memory store.

Reads consolidation-results.jsonl, extracts KEEP decisions, and creates
consolidated memories. Deduplicates by content hash.

Usage:
    python3 scripts/seed.py                                    # Seed into global store
    python3 scripts/seed.py --project-context /path/to/project # Seed into project store
    python3 scripts/seed.py --project app                      # Only seed sessions matching "app"
    python3 scripts/seed.py --dry-run                          # Show what would be seeded
    python3 scripts/seed.py --report                           # Print quality report only
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.storage import MemoryStore

OUTPUT_DIR = Path(__file__).parent.parent / "backfill-output"


def load_results(results_path: Path, project_filter: str = None) -> list[dict]:
    """Load kept decisions from consolidation results.

    If project_filter is set, only include sessions whose project field
    contains the filter string (case-insensitive substring match).
    """
    kept = []
    with open(results_path) as f:
        for line in f:
            r = json.loads(line)
            if not r.get("ok"):
                continue
            if project_filter and project_filter.lower() not in r.get("project", "").lower():
                continue
            for d in r.get("decisions", []):
                if d.get("action") == "keep":
                    d["_session"] = r.get("session_id", "")
                    d["_project"] = r.get("project", "")
                    d["_focus"] = r.get("focus", "")
                    kept.append(d)
    return kept


def print_report(kept: list[dict], project_filter: str = None):
    """Print a quality report on consolidation output."""
    total_results = 0
    total_pruned = 0
    results_path = OUTPUT_DIR / "consolidation-results.jsonl"
    with open(results_path) as f:
        for line in f:
            r = json.loads(line)
            if not r.get("ok"):
                continue
            if project_filter and project_filter.lower() not in r.get("project", "").lower():
                continue
            for d in r.get("decisions", []):
                total_results += 1
                if d.get("action") == "prune":
                    total_pruned += 1

    print(f"{'=' * 55}")
    print(f"  CONSOLIDATION REPORT")
    print(f"{'=' * 55}")
    print(f"  Total decisions: {total_results}")
    print(f"  Kept:   {len(kept):4d} ({len(kept)/max(total_results,1):.0%})")
    print(f"  Pruned: {total_pruned:4d} ({total_pruned/max(total_results,1):.0%})")

    # Type distribution
    types = {}
    for d in kept:
        t = d.get("observation_type") or "(untyped)"
        types[t] = types.get(t, 0) + 1

    print(f"\n  Observation types:")
    for t, count in sorted(types.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * (count * 2)
        print(f"    {t:25s} {count:3d}  {bar}")

    print(f"\n  Observations:")
    for d in kept:
        obs_type = d.get("observation_type", "?")
        title = d.get("title", "(no title)")
        print(f"    [{obs_type:20s}] {title}")

    print(f"{'=' * 55}")


def _load_reinforcements() -> dict[str, int]:
    """Load reinforcement counts from the consolidation sidecar file."""
    path = OUTPUT_DIR / "reinforcements.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _load_session_date_range() -> tuple[str, str] | None:
    """Read summaries.jsonl to get the earliest and latest session dates."""
    summaries_path = OUTPUT_DIR / "summaries.jsonl"
    if not summaries_path.exists():
        return None
    dates = []
    with open(summaries_path) as f:
        for line in f:
            try:
                s = json.loads(line)
                if s.get("modified"):
                    dates.append(s["modified"])
            except json.JSONDecodeError:
                continue
    if not dates:
        return None
    return (min(dates), max(dates))


def _distribute_timestamps(count: int, date_range: tuple[str, str] | None) -> list[str]:
    """Generate evenly distributed timestamps across the session date range.

    Returns a list of ISO timestamps, one per observation to seed.
    If no date range is available, returns empty list (caller uses now()).
    """
    if not date_range or count == 0:
        return []
    try:
        start = datetime.fromisoformat(date_range[0])
        end = datetime.fromisoformat(date_range[1])
    except (ValueError, TypeError):
        return []
    if start >= end or count == 1:
        return [start.isoformat()] * count
    step = (end - start) / (count - 1)
    return [(start + step * i).isoformat() for i in range(count)]


def seed(kept: list[dict], project_context: str = None, dry_run: bool = False):
    """Seed kept decisions into the store.

    Reads reinforcement counts from the consolidation sidecar — observations
    that were independently reinforced across sessions start with a higher
    reinforcement_count, making them eligible for crystallization sooner.

    Uses original session dates from summaries.jsonl to set created_at,
    distributing memories evenly across the date range so that thread
    detection, relevance decay, and temporal ordering work correctly.
    """
    reinforcements = _load_reinforcements()
    date_range = _load_session_date_range()
    timestamps = _distribute_timestamps(len(kept), date_range)

    if dry_run:
        print(f"DRY RUN — {len(kept)} observations would be seeded:")
        for d in kept:
            title = d.get("title", "?")
            r = reinforcements.get(title, 0)
            r_str = f" (reinforced x{r})" if r > 0 else ""
            print(f"  [{d.get('observation_type', '?'):20s}] {title}{r_str}")
        return

    store = MemoryStore(project_context=project_context)
    seeded, skipped = 0, 0

    for idx, d in enumerate(kept):
        title = d.get("title", "Untitled")
        summary = (d.get("summary") or "")[:150]
        obs_type = d.get("observation_type", "")
        tags = list(d.get("tags") or [])
        observation = d.get("observation", "")

        if obs_type and f"type:{obs_type}" not in tags:
            tags.append(f"type:{obs_type}")
        tags.append("source:backfill")
        if d.get("_focus"):
            tags.append(f"focus:{d['_focus'][:30]}")

        content = observation
        rationale = d.get("rationale", "")
        if rationale:
            content += f"\n\n**Why this matters:** {rationale}"

        reinforcement_count = reinforcements.get(title, 0)
        importance = min(0.65 + (reinforcement_count * 0.03), 0.85)

        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', title.lower())[:60]

        created_at = timestamps[idx] if idx < len(timestamps) else None
        meta = {
            "stage": "consolidated",
            "title": title,
            "summary": summary,
            "tags": tags,
            "importance": importance,
            "reinforcement_count": reinforcement_count,
            "source_session": d.get("_session", ""),
        }
        if created_at:
            meta["created_at"] = created_at

        try:
            store.create(
                path=f"backfill/{safe_name}.md",
                content=content,
                metadata=meta,
            )
            seeded += 1
            r_str = f" (reinforced x{reinforcement_count})" if reinforcement_count > 0 else ""
            print(f"  + [{obs_type or '?':20s}] {title}{r_str}")
        except ValueError:
            skipped += 1

    print(f"\nSeeded: {seeded}, Skipped (duplicate): {skipped}")
    print(f"Store: {store.base_dir}")
    store.close()


def main():
    results_path = OUTPUT_DIR / "consolidation-results.jsonl"
    if not results_path.exists():
        print("No consolidation-results.jsonl found. Run scripts/consolidate.py first.", file=sys.stderr)
        sys.exit(1)

    project_context, project_filter, dry_run, report_only = None, None, False, False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--project-context" and i + 1 < len(args):
            project_context = args[i + 1]; i += 2
        elif args[i] == "--project" and i + 1 < len(args):
            project_filter = args[i + 1]; i += 2
        elif args[i] == "--dry-run":
            dry_run = True; i += 1
        elif args[i] == "--report":
            report_only = True; i += 1
        else:
            print(f"Unknown: {args[i]}", file=sys.stderr); sys.exit(1)

    kept = load_results(results_path, project_filter=project_filter)
    if project_filter:
        print(f"Filtered to project matching '{project_filter}': {len(kept)} observations", file=sys.stderr)

    if report_only or dry_run:
        print_report(kept, project_filter=project_filter)
        if dry_run and not report_only:
            print()
            seed(kept, project_context, dry_run=True)
        return

    seed(kept, project_context)


if __name__ == "__main__":
    main()
