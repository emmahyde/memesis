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


def _clean_text(text: str) -> str:
    """Strip noise from a text block, keeping only conversational signal."""
    # XML tags: system reminders, command wrappers, skill loads
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-(?:name|message|args)>.*?</command-(?:name|message|args)>', '', text, flags=re.DOTALL)
    text = re.sub(r'<local-command-(?:stdout|caveat)>.*?</local-command-(?:stdout|caveat)>', '', text, flags=re.DOTALL)
    text = re.sub(r'<available-deferred-tools>.*?</available-deferred-tools>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[a-zA-Z_-]+>.*?</[a-zA-Z_-]+>', '', text, flags=re.DOTALL)

    # Code blocks: remove large ones, keep small ones (might contain key snippets)
    text = re.sub(r'```[\s\S]{300,}?```', '[code block removed]', text)

    # File paths and tool-mechanical lines
    text = re.sub(r'^(?:Base directory|File:?|Path:?) (?:for this skill: )?/\S+.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+→.*$', '', text, flags=re.MULTILINE)  # cat -n style file content

    # Markdown headers that are just skill/doc content being loaded (not conversation)
    # Keep ## headers (likely assistant structuring) but strip long doc-like content after them
    text = re.sub(r'^#{1,3} (?:Core Capabilities|Usage|Installation|Configuration|API Reference|Parameters)\b.*?(?=^#{1,3} |\Z)', '', text, flags=re.MULTILINE | re.DOTALL)

    # Loaded content detection: if a block has 3+ headers and is long, it's a
    # skill/doc/spec being injected — not conversation. Collapse to one line.
    headers = re.findall(r'^#{1,4} .+', text, re.MULTILINE)
    if len(headers) >= 3 and len(text) > 500:
        text = f"[loaded content: {headers[0].strip('# ').strip()[:60]}]"

    # Repeated whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_tool_summary(content: list) -> str:
    """Summarize tool usage from an assistant message's content blocks."""
    tools_used = []
    for block in content:
        if block.get("type") == "tool_use":
            name = block.get("name", "?")
            # Extract key info without raw output
            inp = block.get("input", {})
            if name in ("Read", "read"):
                tools_used.append(f"[read {inp.get('file_path', '?').split('/')[-1]}]")
            elif name in ("Edit", "edit"):
                tools_used.append(f"[edited {inp.get('file_path', '?').split('/')[-1]}]")
            elif name in ("Write", "write"):
                tools_used.append(f"[wrote {inp.get('file_path', '?').split('/')[-1]}]")
            elif name in ("Bash", "bash"):
                cmd = str(inp.get("command", "?"))[:60]
                tools_used.append(f"[ran: {cmd}]")
            elif name in ("Grep", "grep"):
                tools_used.append(f"[searched for: {inp.get('pattern', '?')[:40]}]")
            elif name == "Agent":
                tools_used.append(f"[spawned agent: {inp.get('description', '?')[:40]}]")
            # Skip other tools — not worth summarizing
    if tools_used:
        return " ".join(tools_used[:5])  # Cap at 5 tool mentions
    return ""


def read_transcript(path: Path) -> list[dict]:
    """Extract conversational signal from a Claude Code transcript.

    Filters aggressively:
    - Only user and assistant messages (no progress, system, tool_result)
    - Only text blocks (tool_use/tool_result content stripped)
    - Tool usage summarized as compact one-liners instead of raw output
    - XML tags, system injections, skill loads, file content all removed
    - Large code blocks replaced with placeholder
    """
    messages = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") not in ("user", "assistant"):
                continue

            content = msg.get("message", {}).get("content", "")
            role = msg["type"]

            if isinstance(content, list):
                # Extract text and thinking blocks — skip tool_use, tool_result
                parts = []
                for b in content:
                    if b.get("type") == "text":
                        parts.append(b.get("text", "").strip())
                    elif b.get("type") == "thinking":
                        thinking = b.get("thinking", "").strip()
                        if thinking:
                            parts.append(f"[thinking] {thinking}")
                text = "\n".join(t for t in parts if t)

                # For assistant messages, add a compact tool summary
                if role == "assistant":
                    tool_summary = _extract_tool_summary(content)
                    if tool_summary and not text:
                        text = tool_summary  # Tool-only turn — keep the summary
                    elif tool_summary:
                        text = f"{text}\n{tool_summary}"
            elif isinstance(content, str):
                text = content.strip()
            else:
                continue

            text = _clean_text(text)

            if len(text) < 10:
                continue

            messages.append({"role": role, "text": text})
    return messages


def summarize(messages: list[dict], max_chars: int = 12000) -> str:
    if not messages:
        return ""
    lines, chars = [], 0
    for msg in messages:
        role = "USER" if msg["role"] == "user" else "CLAUDE"
        limit = 800 if role == "USER" else 300
        text = msg["text"][:limit]
        if len(msg["text"]) > limit:
            text += "..."
        entry = f"[{role}] {text}"
        if chars + len(entry) > max_chars:
            lines.append("[... session truncated ...]")
            break
        lines.append(entry)
        chars += len(entry)
    return "\n\n".join(lines)


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
