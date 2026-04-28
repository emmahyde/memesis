"""
Transcript parsing utilities for Claude Code JSONL session files.

Promoted from scripts/scan.py so both the backfill pipeline and the
live cron share one implementation.

Public API:
    read_transcript(path)              — full file parse
    read_transcript_from(path, offset) — delta parse from byte offset
    summarize(messages)                — format [USER]/[CLAUDE] text block
"""

import json
import re
from pathlib import Path


def _detect_cwd(path: Path) -> str | None:
    """Walk the JSONL once looking for a top-level 'cwd' field on any entry.

    cwd lives on attachment entries that read_transcript_from() filters out,
    so we have to scan the raw file separately. Scans first 200 lines only.
    """
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 200:
                    break
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                cwd = entry.get("cwd")
                if cwd:
                    return cwd
    except OSError:
        pass
    return None


def _clean_text(text: str) -> str:
    """Strip noise from a text block, keeping only conversational signal."""
    # XML tags: system reminders, command wrappers, skill loads
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-(?:name|message|args)>.*?</command-(?:name|message|args)>', '', text, flags=re.DOTALL)
    text = re.sub(r'<local-command-(?:stdout|caveat)>.*?</local-command-(?:stdout|caveat)>', '', text, flags=re.DOTALL)
    text = re.sub(r'<available-deferred-tools>.*?</available-deferred-tools>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[a-zA-Z_-]+>.*?</[a-zA-Z_-]+>', '', text, flags=re.DOTALL)
    text = re.sub(r'```[\s\S]{300,}?```', '[code block removed]', text)
    text = re.sub(r'^(?:Base directory|File:?|Path:?) (?:for this skill: )?/\S+.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+→.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^#{1,3} (?:Core Capabilities|Usage|Installation|Configuration|API Reference|Parameters)\b.*?(?=^#{1,3} |\Z)', '', text, flags=re.MULTILINE | re.DOTALL)
    headers = re.findall(r'^#{1,4} .+', text, re.MULTILINE)
    if len(headers) >= 3 and len(text) > 500:
        text = f"[loaded content: {headers[0].strip('# ').strip()[:60]}]"
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_tool_summary(content: list) -> str:
    """Summarize tool usage from an assistant message's content blocks."""
    tools_used = []
    for block in content:
        if block.get("type") == "tool_use":
            name = block.get("name", "?")
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
    if tools_used:
        return " ".join(tools_used[:5])
    return ""


def read_transcript(path: Path) -> list[dict]:
    """Extract conversational signal from a Claude Code transcript."""
    messages = []
    with open(path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") not in ("user", "assistant"):
                continue
            content = msg.get("message", {}).get("content", "")
            role = msg["type"]
            if isinstance(content, list):
                parts = []
                for b in content:
                    if b.get("type") == "text":
                        parts.append(b.get("text", "").strip())
                    elif b.get("type") == "thinking":
                        thinking = b.get("thinking", "").strip()
                        if thinking:
                            parts.append(f"[thinking] {thinking}")
                text = "\n".join(t for t in parts if t)
                if role == "assistant":
                    tool_summary = _extract_tool_summary(content)
                    if tool_summary and not text:
                        text = tool_summary
                    elif tool_summary:
                        text = f"{text}\n{tool_summary}"
            elif isinstance(content, str):
                text = content.strip()
            else:
                continue
            text = _clean_text(text)
            if len(text) < 10:
                continue
            messages.append({"role": role, "text": text, "line": line_num})
    return messages


def read_transcript_from(path: Path, byte_offset: int) -> tuple[list[dict], int, str | None]:
    """Parse a transcript starting from byte_offset, returning (messages, new_offset, cwd).

    The third element is the detected working directory from the JSONL file, or None
    if no cwd field is found. cwd detection always scans the full file from the start,
    regardless of byte_offset, because cwd entries are filtered out by the message parser.
    """
    file_size = path.stat().st_size
    cwd = _detect_cwd(path)
    if byte_offset >= file_size:
        return ([], file_size, cwd)

    messages = []
    with open(path, "rb") as f:
        f.seek(byte_offset)
        if byte_offset > 0:
            f.readline()
        raw = f.read()
        new_offset = f.tell()

    for line in raw.splitlines():
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if msg.get("type") not in ("user", "assistant"):
            continue
        content = msg.get("message", {}).get("content", "")
        role = msg["type"]
        if isinstance(content, list):
            parts = []
            for b in content:
                if b.get("type") == "text":
                    parts.append(b.get("text", "").strip())
                elif b.get("type") == "thinking":
                    thinking = b.get("thinking", "").strip()
                    if thinking:
                        parts.append(f"[thinking] {thinking}")
            text = "\n".join(t for t in parts if t)
            if role == "assistant":
                tool_summary = _extract_tool_summary(content)
                if tool_summary and not text:
                    text = tool_summary
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

    return (messages, new_offset, cwd)


def summarize(messages: list[dict], max_chars: int | None = None) -> str:
    if not messages:
        return ""
    if max_chars is None:
        max_chars = 12000 + max(0, len(messages) - 40) * 100
    lines, chars = [], 0
    for msg in messages:
        role = "USER" if msg["role"] == "user" else "CLAUDE"
        text = msg["text"]
        line_ref = f":L{msg['line']}" if "line" in msg else ""
        entry = f"[{role}{line_ref}] {text}"
        if chars + len(entry) > max_chars:
            lines.append("[... session truncated ...]")
            break
        lines.append(entry)
        chars += len(entry)
    return "\n\n".join(lines)


def iter_user_anchored_windows(
    messages: list[dict],
    *,
    context_before: int = 2,
    context_after: int = 8,
    max_chars_per_window: int = 16000,
    max_windows: int = 12,
    min_user_turn_chars: int = 60,
) -> list[str]:
    """Yield windows centered on each substantive user turn.

    Implements semantic-unit chunking per Wu et al. 2021 ("Recursively
    Summarizing Books", arXiv 2109.10862) — chunks aligned to natural
    discourse boundaries beat fixed-size windows on long-document
    summarization. The user turn is the natural decision/pivot boundary
    in a Claude Code session: questions are asked, directions given,
    corrections issued. The surrounding assistant turns supply the
    answer/execution context needed to extract a durable observation.

    Skips trivial user turns (one-word acks, "ok", "yes") via the
    min_user_turn_chars threshold.

    Args:
        messages: list[{"role","text","line"?}] from read_transcript_from.
        context_before: assistant turns to include preceding the user turn.
        context_after: assistant turns to include following the user turn.
        max_chars_per_window: hard cap on rendered chars per window.
        max_windows: cap total windows for cost control (Park 2023 plateau).
        min_user_turn_chars: skip user turns shorter than this (acks,
                             single-word replies — no decision content).

    Returns:
        list of rendered window strings in [USER]/[CLAUDE] format.
    """
    if not messages:
        return []

    user_indices = [
        i for i, m in enumerate(messages)
        if m["role"] == "user" and len(m.get("text", "")) >= min_user_turn_chars
    ]
    if not user_indices:
        return []

    n = len(messages)
    windows: list[str] = []
    last_end = -1

    for ui in user_indices:
        if len(windows) >= max_windows:
            break
        # backfill some preceding assistant context
        start = ui
        before = 0
        while start > 0 and before < context_before:
            start -= 1
            if messages[start]["role"] == "assistant":
                before += 1
        # forward to context_after assistant turns or next user turn
        end = ui + 1
        after = 0
        while end < n and after < context_after:
            if messages[end]["role"] == "user":
                # absorb the next user turn's reply only if it's an ack;
                # otherwise stop here so that turn anchors its own window
                if len(messages[end].get("text", "")) >= min_user_turn_chars:
                    break
            else:
                after += 1
            end += 1

        # avoid emitting a window that's strictly contained in the previous one
        if start <= last_end and end <= last_end:
            continue
        last_end = max(last_end, end)

        block = []
        used = 0
        for m in messages[start:end]:
            role = "USER" if m["role"] == "user" else "CLAUDE"
            line_ref = f":L{m['line']}" if "line" in m else ""
            entry = f"[{role}{line_ref}] {m['text']}"
            if used + len(entry) + 2 > max_chars_per_window:
                break
            block.append(entry)
            used += len(entry) + 2
        if block:
            windows.append("\n\n".join(block))

    return windows[:max_windows]


def iter_windows(
    messages: list[dict],
    *,
    window_chars: int = 16000,
    stride_chars: int = 12800,
    max_windows: int = 10,
) -> list[str]:
    """Yield overlapping rendered windows over a message list.

    Implements the Beltagy 2020 (Longformer, arXiv 2004.05150) overlapping
    stride pattern adapted to char budgets, and caps total windows per
    Park 2023 (Generative Agents) extraction-quality plateau at 5-10 calls.

    Default 16000-char window keeps each LLM call well below the
    "lost-in-the-middle" U-shape failure mode (Liu 2023, arXiv 2307.03172),
    where attention degrades for content placed past ~30% of context.

    20% overlap (stride = 0.8 × window) preserves discourse boundaries
    so that a decision in window N and its reversal in window N+1 land in
    the same chunk at least once.

    Args:
        messages: list[{"role","text","line"?}] from read_transcript_from.
        window_chars: target rendered chars per window.
        stride_chars: chars to advance between window starts (must be < window_chars).
        max_windows: hard cap to bound subscription cost.

    Returns:
        list of rendered window strings. Each is a [USER]/[CLAUDE] block
        in the same format as summarize() so the existing prompt accepts it.
    """
    if not messages:
        return []
    if stride_chars >= window_chars:
        raise ValueError("stride_chars must be < window_chars for overlap")

    rendered: list[tuple[str, int]] = []
    for msg in messages:
        role = "USER" if msg["role"] == "user" else "CLAUDE"
        line_ref = f":L{msg['line']}" if "line" in msg else ""
        entry = f"[{role}{line_ref}] {msg['text']}"
        rendered.append((entry, len(entry) + 2))

    total_chars = sum(n for _, n in rendered)
    if total_chars <= window_chars:
        return ["\n\n".join(e for e, _ in rendered)]

    windows: list[str] = []
    n = len(rendered)
    cum = [0] * (n + 1)
    for i, (_, c) in enumerate(rendered):
        cum[i + 1] = cum[i] + c

    cursor = 0
    start_idx = 0
    while start_idx < n and len(windows) < max_windows:
        budget = window_chars
        idx = start_idx
        used = 0
        while idx < n and used + rendered[idx][1] <= budget:
            used += rendered[idx][1]
            idx += 1
        if idx == start_idx:
            idx = start_idx + 1
            used = rendered[start_idx][1]
        windows.append("\n\n".join(e for e, _ in rendered[start_idx:idx]))

        target = cum[start_idx] + stride_chars
        next_start = start_idx + 1
        while next_start < idx and cum[next_start] < target:
            next_start += 1
        if next_start <= start_idx:
            next_start = start_idx + 1
        if next_start >= n:
            break
        start_idx = next_start
        cursor = cum[start_idx]
        if cum[n] - cursor < window_chars * 0.2:
            tail = "\n\n".join(e for e, _ in rendered[start_idx:])
            if tail and tail not in windows[-1]:
                windows.append(tail)
            break

    return windows[:max_windows]
