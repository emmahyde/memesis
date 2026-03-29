"""
Ingest native Claude Code memories into the memesis lifecycle.

The built-in Claude Code memory system writes memories to MEMORY.md (index)
and individual .md files with YAML frontmatter (name, description, type).
This module reads those files, deduplicates against the memesis store, and
creates consolidated memories so they participate in the normal lifecycle
(promotion, archival, relevance scoring, etc.).

Native memory types map to memesis observation types:
    user        → workflow_pattern (who the user is, how they work)
    feedback    → correction / preference_signal (what to do differently)
    project     → decision_context (ongoing work, goals, decisions)
    reference   → domain_knowledge (pointers to external systems)
"""

import logging
import re
from pathlib import Path
from typing import Optional

from .storage import MemoryStore

logger = logging.getLogger(__name__)

# Map native Claude Code memory types to memesis observation types.
NATIVE_TYPE_MAP = {
    "user": "workflow_pattern",
    "feedback": "correction",
    "project": "decision_context",
    "reference": "domain_knowledge",
}

# Importance defaults for native memory types.
# Feedback is highest — corrections are the most valuable signal.
NATIVE_IMPORTANCE = {
    "user": 0.65,
    "feedback": 0.75,
    "project": 0.60,
    "reference": 0.55,
}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Extract YAML frontmatter from markdown content.

    Returns:
        (metadata_dict, body_content)
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, text

    metadata = {}
    for line in lines[1:end_idx]:
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()

    body = "\n".join(lines[end_idx + 1:]).strip()
    return metadata, body


def find_native_memory_dir(project_context: str = None) -> Optional[Path]:
    """
    Locate the native Claude Code memory directory.

    Checks project-scoped first, then global.

    Args:
        project_context: Project directory path.

    Returns:
        Path to the memory directory, or None if not found.
    """
    candidates = []

    if project_context:
        # Match Claude Code's path hashing convention
        path_hash = re.sub(r'[^a-zA-Z0-9-]', '-', project_context)
        candidates.append(
            Path.home() / ".claude" / "projects" / path_hash / "memory"
        )

    candidates.append(Path.home() / ".claude" / "memory")

    for candidate in candidates:
        if (candidate / "MEMORY.md").exists():
            return candidate

    return None


def scan_native_memories(memory_dir: Path) -> list[dict]:
    """
    Scan a native Claude Code memory directory for .md files with frontmatter.

    Reads MEMORY.md to find linked files, then reads each file's frontmatter
    and body. Skips files that don't have the expected frontmatter format.

    Args:
        memory_dir: Path to the memory directory.

    Returns:
        List of dicts with keys: path, name, description, type, body, filename.
    """
    memories = []

    # Read MEMORY.md to find linked files
    memory_md = memory_dir / "MEMORY.md"
    if not memory_md.exists():
        return memories

    index_content = memory_md.read_text(encoding="utf-8")

    # Extract linked file paths from markdown links: [Title](filename.md)
    link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+\.md)\)')
    linked_files = set()
    for match in link_pattern.finditer(index_content):
        linked_files.add(match.group(2))

    # Also scan for any .md files at the root (not in subdirectories used by memesis)
    memesis_dirs = {"ephemeral", "consolidated", "crystallized", "instinctive", "archived", "meta"}
    for md_file in memory_dir.glob("*.md"):
        if md_file.name != "MEMORY.md":
            linked_files.add(md_file.name)

    # Read each file
    for filename in linked_files:
        file_path = memory_dir / filename
        if not file_path.exists():
            continue

        # Skip files in memesis subdirectories
        try:
            relative = file_path.relative_to(memory_dir)
            if relative.parts[0] in memesis_dirs:
                continue
        except (ValueError, IndexError):
            pass

        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError:
            continue

        metadata, body = parse_frontmatter(content)
        if not metadata.get("name"):
            continue

        memories.append({
            "path": str(file_path),
            "filename": filename,
            "name": metadata.get("name", ""),
            "description": metadata.get("description", ""),
            "type": metadata.get("type", ""),
            "body": body,
        })

    return memories


class NativeMemoryIngestor:
    """
    Ingests native Claude Code memories into the memesis store.

    Reads .md files written by the built-in auto-memory system, deduplicates
    against existing memesis memories (by content hash), and creates
    consolidated entries that participate in the normal lifecycle.
    """

    def __init__(self, store: MemoryStore):
        self.store = store

    def ingest(self, project_context: str = None) -> dict:
        """
        Scan for native memories and ingest any that aren't already in the store.

        Args:
            project_context: Project directory path.

        Returns:
            Summary dict with 'ingested' (list of titles) and 'skipped' count.
        """
        memory_dir = find_native_memory_dir(project_context)
        if memory_dir is None:
            logger.info("No native Claude Code memory directory found")
            return {"ingested": [], "skipped": 0, "source": None}

        native_memories = scan_native_memories(memory_dir)
        if not native_memories:
            return {"ingested": [], "skipped": 0, "source": str(memory_dir)}

        ingested = []
        skipped = 0

        for mem in native_memories:
            native_type = mem.get("type", "")
            obs_type = NATIVE_TYPE_MAP.get(native_type, "domain_knowledge")
            importance = NATIVE_IMPORTANCE.get(native_type, 0.5)

            # Build the content — include the original description as context
            content = mem["body"]
            if mem.get("description"):
                content = f"*{mem['description']}*\n\n{content}"

            # Build a safe filename from the original
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', mem["name"].lower())
            target_path = f"native/{safe_name}.md"

            tags = [f"type:{obs_type}", "source:native-claude-code"]
            if native_type:
                tags.append(f"native-type:{native_type}")

            try:
                memory_id = self.store.create(
                    path=target_path,
                    content=content,
                    metadata={
                        "stage": "consolidated",
                        "title": mem["name"],
                        "summary": (mem.get("description") or "")[:150],
                        "tags": tags,
                        "importance": importance,
                    },
                )
                ingested.append(mem["name"])
                logger.info("Ingested native memory: %s → %s", mem["name"], memory_id)
            except ValueError as e:
                # Duplicate content hash — already ingested
                skipped += 1
                logger.debug("Skipped duplicate native memory: %s (%s)", mem["name"], e)

        return {
            "ingested": ingested,
            "skipped": skipped,
            "source": str(memory_dir),
        }
