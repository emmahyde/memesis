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


def read_transcript_from(path: Path, byte_offset: int) -> tuple[list[dict], int]:
    """Parse a transcript starting from byte_offset, returning (messages, new_offset)."""
    file_size = path.stat().st_size
    if byte_offset >= file_size:
        return ([], file_size)

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

    return (messages, new_offset)


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
