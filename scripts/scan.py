#!/usr/bin/env python3
"""
Scan Claude Code transcripts and produce session summaries.

Reads JSONL transcript files, extracts user/assistant conversation flow,
and writes summaries to backfill-output/summaries.jsonl.

Usage:
    python3 scripts/scan.py 30d                       # Last 30 days, all projects
    python3 scripts/scan.py 2w --project app           # Last 2 weeks, projects matching "app"
    python3 scripts/scan.py 7d --limit 20              # Cap at 20 sessions
    python3 scripts/scan.py 30d --min-size 50          # Skip sessions under 50KB
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.transcript import read_transcript, summarize  # type: ignore[import]

PROJECTS_DIR = Path.home() / ".claude" / "projects"
OUTPUT_DIR = Path(__file__).parent.parent / "backfill-output"


def project_slug(dirname: str) -> str:
    """Derive canonical project name from Claude project dir name.

    Examples:
        -Users-emma-hyde-work-ai-tools -> ai-tools
        -Users-emma-hyde-worktrees-ai-tools-RETIRE-4689 -> ai-tools
        -Users-emma-hyde-projects-memesis -> memesis
        -Users-emma-hyde-work-ai-tools--claude-worktrees-foo -> ai-tools
    """
    name = dirname.lstrip('-')
    # Strip worktree clone suffixes
    if '--claude-worktrees' in name:
        name = name.split('--claude-worktrees')[0]
    # Find the repo name after known parent markers
    for marker in ('-work-', '-projects-', '-personal-', '-worktrees-'):
        idx = name.find(marker)
        if idx != -1:
            rest = name[idx + len(marker):]
            # Worktree branches append UPPERCASE or ticket-number segments
            parts = rest.split('-')
            repo_parts = []
            for p in parts:
                if p.isupper() or (p.isdigit() and len(p) >= 4):
                    break
                repo_parts.append(p)
            return '-'.join(repo_parts) if repo_parts else rest
    return name


def parse_duration(s: str) -> timedelta:
    match = re.match(r'^(\d+)([dhwm])$', s.strip().lower())
    if not match:
        raise ValueError(f"Invalid duration: '{s}'. Use 30d, 2w, 4h, 6m (months).")
    n, unit = int(match.group(1)), match.group(2)
    if unit == 'h': return timedelta(hours=n)
    if unit == 'd': return timedelta(days=n)
    if unit == 'w': return timedelta(weeks=n)
    if unit == 'm': return timedelta(days=n * 30)
    raise ValueError(f"Unhandled unit: {unit!r}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/scan.py <duration> [--project NAME] [--limit N] [--min-size KB]")
        sys.exit(1)

    since = parse_duration(sys.argv[1])
    cutoff = datetime.now() - since

    project_filter, limit, min_size_kb = None, None, 10.0
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project_filter = args[i + 1]; i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--min-size" and i + 1 < len(args):
            min_size_kb = float(args[i + 1]); i += 2
        else:
            print(f"Unknown: {args[i]}", file=sys.stderr); sys.exit(1)

    # Find sessions
    sessions = []
    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        if project_filter and project_filter not in project_dir.name:
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            stat = jsonl.stat()
            if stat.st_size / 1024 < min_size_kb:
                continue
            modified = datetime.fromtimestamp(stat.st_mtime)
            if modified < cutoff:
                continue
            sessions.append({
                "path": str(jsonl),
                "project": project_dir.name,
                "session_id": jsonl.stem,
                "size_kb": stat.st_size / 1024,
                "modified": modified.isoformat(),
            })

    sessions.sort(key=lambda s: s["modified"], reverse=True)
    if limit:
        sessions = sessions[:limit]

    print(f"Scanning {len(sessions)} sessions (since {cutoff.strftime('%Y-%m-%d')})...", file=sys.stderr)

    # Summarize
    results = []
    for i, sess in enumerate(sessions):
        print(f"  [{i+1}/{len(sessions)}] {sess['session_id'][:8]}... "
              f"({sess['size_kb']:.0f}KB, {sess['project'][:30]})", file=sys.stderr)
        messages = read_transcript(Path(sess["path"]))
        if len(messages) < 3:
            continue
        summary = summarize(messages)
        if not summary:
            continue
        results.append({
            "session_id": sess["session_id"],
            "project": sess["project"],
            "modified": sess["modified"],
            "summary": summary,
            "message_count": len(messages),
            "size_kb": sess["size_kb"],
        })

    # Group by canonical project slug, write per-project files
    by_slug: dict[str, list] = {}
    for r in results:
        slug = project_slug(r["project"])
        by_slug.setdefault(slug, []).append(r)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug, items in sorted(by_slug.items()):
        output_path = OUTPUT_DIR / f"summaries-{slug}.jsonl"
        with open(output_path, "w") as f:
            for r in items:
                f.write(json.dumps(r) + "\n")
        print(f"  {slug}: {len(items)} sessions → {output_path.name}", file=sys.stderr)

    total = sum(len(v) for v in by_slug.values())
    total_chars = sum(len(r["summary"]) for r in results)
    print(f"\n{total} sessions across {len(by_slug)} projects", file=sys.stderr)
    print(f"Avg summary: {total_chars // max(total, 1):,} chars/session", file=sys.stderr)


if __name__ == "__main__":
    main()
