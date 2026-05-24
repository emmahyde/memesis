"""Bidirectional sync between memesis crystallized memories and Claude Code's
native file-based memory (~/.claude/projects/{slug}/memory/).

Design (task #27):
- Crystallized memories export to slug-named .md files under a per-type subdir.
- User edits to those files are authoritative: ingest re-parses + bumps rc.
- Demotion marks files with `archived: true` frontmatter (leave file on disk).
- Files lacking `metadata.memesis_id` mint new crystallized memories on ingest.

All persistence still flows through Peewee — this module only translates the
file surface. Atomic writes via tempfile + rename.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .models import Memory


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

NATIVE_TYPES = ("user", "feedback", "project", "reference")

# kind (memesis 7+2 taxonomy) → native Claude Code type.
# fact has a URL-ish escape hatch handled in kind_to_native_type().
KIND_TO_NATIVE = {
    "decision": "project",
    "goal": "project",
    "lesson": "feedback",
    "correction": "feedback",
    "directive": "feedback",
    "preference": "user",
    "fact": "project",
    "hypothesis": "project",
    "open_question": "project",
}

_URL_RE = re.compile(r"https?://|\bwww\.")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def native_memory_dir(
    project: Optional[str],
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Resolve the native memory directory for ``project``.

    ``base_dir`` overrides the default (used by tests). Returns None when no
    project slug is available — caller decides whether to skip silently.
    """
    if base_dir is not None:
        return Path(base_dir)
    if not project:
        return None
    return Path.home() / ".claude" / "projects" / project / "memory"


# ---------------------------------------------------------------------------
# Kind → type mapping
# ---------------------------------------------------------------------------


def kind_to_native_type(kind: Optional[str], content: Optional[str] = None) -> str:
    """Map a memesis kind to the native Claude Code memory type.

    fact + URL-ish content → reference (the only content-aware branch).
    Unknown kinds fall back to project.
    """
    if kind == "fact" and content and _URL_RE.search(content):
        return "reference"
    return KIND_TO_NATIVE.get(kind or "", "project")


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------

_SLUG_NONWORD = re.compile(r"[^a-z0-9]+")


def slugify_title(title: str, existing_slugs: Iterable[str]) -> str:
    """Kebab-case slug from title with -2/-3 collision suffix."""
    base = _SLUG_NONWORD.sub("-", (title or "").lower()).strip("-")
    if not base:
        base = "untitled"
    existing = set(existing_slugs)
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


# ---------------------------------------------------------------------------
# Frontmatter (hand-rolled — no PyYAML dep)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse leading ``---``-fenced YAML frontmatter. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def _render_frontmatter(meta: dict) -> str:
    """Render frontmatter dict to ``---``-fenced YAML.

    sort_keys=False preserves caller ordering; default_flow_style=False keeps
    metadata as a nested block (matching native Claude Code memory format).
    """
    body = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False).rstrip()
    return f"---\n{body}\n---"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        shutil.move(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Export — memesis → native file
# ---------------------------------------------------------------------------


def _existing_slugs(type_dir: Path) -> set[str]:
    if not type_dir.is_dir():
        return set()
    return {p.stem for p in type_dir.glob("*.md")}


def _find_by_memesis_id(base_dir: Path, memory_id: str) -> Optional[Path]:
    """Locate the native file carrying ``metadata.memesis_id == memory_id``."""
    for type_name in NATIVE_TYPES:
        type_dir = base_dir / type_name
        if not type_dir.is_dir():
            continue
        for p in type_dir.glob("*.md"):
            try:
                meta, _ = _parse_frontmatter(p.read_text())
            except OSError:
                continue
            if meta.get("metadata", {}).get("memesis_id") == memory_id:
                return p
    return None


def export_memory_to_native(
    memory: Memory,
    base_dir: Path,
) -> Optional[Path]:
    """Write or refresh a native slug file from a crystallized memory.

    Returns the file path written, or None if the memory should not be
    exported (no title, archived, wrong stage).
    """
    if memory.archived_at:
        return None
    if memory.stage != "crystallized":
        return None
    if not (memory.title or memory.summary):
        return None

    native_type = kind_to_native_type(memory.kind, memory.content or memory.summary)
    type_dir = base_dir / native_type
    base_dir.mkdir(parents=True, exist_ok=True)
    type_dir.mkdir(parents=True, exist_ok=True)

    # If we've exported this memory before, refresh in place.
    existing = _find_by_memesis_id(base_dir, memory.id)
    if existing is not None:
        slug = existing.stem
        target = existing
        # type may have shifted — move file if so
        if existing.parent.name != native_type:
            target = type_dir / f"{slug}.md"
    else:
        slug = slugify_title(memory.title or memory.summary or "untitled",
                             _existing_slugs(type_dir))
        target = type_dir / f"{slug}.md"

    meta = {
        "name": slug,
        "description": (memory.summary or memory.title or "").replace("\n", " ")[:200],
        "metadata": {
            "type": native_type,
            "memesis_id": memory.id,
            "memesis_kind": memory.kind or "",
            "memesis_stage": memory.stage,
        },
    }
    body = memory.content or memory.summary or ""
    text = _render_frontmatter(meta) + "\n\n" + body.rstrip() + "\n"
    _atomic_write(target, text)

    # If we moved across type dirs, remove the stale file.
    if existing is not None and existing != target:
        try:
            existing.unlink()
        except OSError:
            pass

    # Stamp memory.updated_at to the file's mtime so a subsequent ingest sees
    # "no edit" rather than treating the just-exported file as a user change.
    mtime_iso = datetime.fromtimestamp(target.stat().st_mtime).isoformat()
    memory.updated_at = mtime_iso
    memory.save()

    return target


# ---------------------------------------------------------------------------
# Ingest — native file → memesis
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().isoformat()


def ingest_native_memories(
    base_dir: Path,
    project: Optional[str] = None,
) -> dict:
    """Scan native memory files and reconcile against the memesis DB.

    Returns a stats dict: {refreshed, minted, skipped, archived_skipped}.
    """
    stats = {"refreshed": 0, "minted": 0, "skipped": 0, "archived_skipped": 0}
    if not base_dir.is_dir():
        return stats

    for type_name in NATIVE_TYPES:
        type_dir = base_dir / type_name
        if not type_dir.is_dir():
            continue
        for path in sorted(type_dir.glob("*.md")):
            try:
                text = path.read_text()
            except OSError:
                stats["skipped"] += 1
                continue
            meta, body = _parse_frontmatter(text)
            if meta.get("archived") is True:
                stats["archived_skipped"] += 1
                continue

            memesis_id = meta.get("metadata", {}).get("memesis_id")
            mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat()

            if memesis_id:
                try:
                    mem = Memory.get_by_id(memesis_id)
                except Memory.DoesNotExist:
                    # File references a deleted/missing memory: mint fresh.
                    _mint_from_file(path, type_name, meta, body, project)
                    stats["minted"] += 1
                    continue
                # Refresh if file is newer than DB row.
                if (mem.updated_at or "") < mtime:
                    mem.content = body.strip()
                    mem.summary = meta.get("description", mem.summary)
                    mem.updated_at = mtime
                    mem.reinforcement_count = (mem.reinforcement_count or 0) + 1
                    mem.save()
                    stats["refreshed"] += 1
                else:
                    stats["skipped"] += 1
            else:
                # Hand-authored file with no memesis_id — mint and stamp it.
                new_id = _mint_from_file(path, type_name, meta, body, project)
                # Write the memesis_id back so future passes recognise it.
                meta.setdefault("metadata", {})["memesis_id"] = new_id
                meta["metadata"].setdefault("type", type_name)
                meta["metadata"]["memesis_kind"] = meta["metadata"].get(
                    "memesis_kind", _native_type_to_kind(type_name)
                )
                meta["metadata"]["memesis_stage"] = "crystallized"
                meta.setdefault("name", path.stem)
                meta.setdefault("description", meta.get("description") or path.stem)
                text = _render_frontmatter(meta) + "\n\n" + body.rstrip() + "\n"
                _atomic_write(path, text)
                stats["minted"] += 1

    return stats


def _native_type_to_kind(native_type: str) -> str:
    """Inverse of KIND_TO_NATIVE for newly-minted hand-authored files.

    Conservative default — caller can refine later via consolidator.
    """
    return {
        "user": "preference",
        "feedback": "lesson",
        "project": "fact",
        "reference": "fact",
    }.get(native_type, "fact")


def _mint_from_file(
    path: Path,
    native_type: str,
    meta: dict,
    body: str,
    project: Optional[str],
) -> str:
    """Create a crystallized memory row from a hand-authored native file."""
    now = _now_iso()
    new_id = str(uuid.uuid4())
    Memory.create(
        id=new_id,
        stage="crystallized",
        title=meta.get("name") or path.stem,
        summary=meta.get("description") or "",
        content=body.strip(),
        kind=_native_type_to_kind(native_type),
        importance=0.75,
        reinforcement_count=1,
        created_at=now,
        updated_at=now,
        project=project,
        source="human",
    )
    return new_id


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------


def mark_native_archived(memory: Memory, base_dir: Path) -> Optional[Path]:
    """Flip ``archived: true`` on the native file for ``memory``. Leave on disk."""
    if not base_dir.is_dir():
        return None
    path = _find_by_memesis_id(base_dir, memory.id)
    if path is None:
        return None
    try:
        text = path.read_text()
    except OSError:
        return None
    meta, body = _parse_frontmatter(text)
    if meta.get("archived") is True:
        return path
    meta["archived"] = True
    new_text = _render_frontmatter(meta) + "\n\n" + body.rstrip() + "\n"
    _atomic_write(path, new_text)
    return path


# ---------------------------------------------------------------------------
# MEMORY.md index rebuild
# ---------------------------------------------------------------------------


def rebuild_memory_index(base_dir: Path) -> Optional[Path]:
    """Regenerate ``base_dir/MEMORY.md`` from all non-archived native files."""
    if not base_dir.is_dir():
        return None

    sections: dict[str, list[str]] = {t: [] for t in NATIVE_TYPES}
    for type_name in NATIVE_TYPES:
        type_dir = base_dir / type_name
        if not type_dir.is_dir():
            continue
        for path in sorted(type_dir.glob("*.md")):
            try:
                meta, _ = _parse_frontmatter(path.read_text())
            except OSError:
                continue
            if meta.get("archived") is True:
                continue
            title = meta.get("name", path.stem)
            desc = meta.get("description", "").strip()
            rel = f"{type_name}/{path.name}"
            line = f"- [{title}]({rel})" + (f" — {desc}" if desc else "")
            sections[type_name].append(line)

    parts = ["# Memory", ""]
    for type_name in NATIVE_TYPES:
        if not sections[type_name]:
            continue
        parts.append(f"## {type_name.capitalize()}")
        parts.append("")
        parts.extend(sections[type_name])
        parts.append("")

    index_path = base_dir / "MEMORY.md"
    _atomic_write(index_path, "\n".join(parts).rstrip() + "\n")
    return index_path
