#!/usr/bin/env python3
"""
Embed all existing memories via Bedrock Titan v2 and store in vec_memories.

Usage:
    python3 scripts/embed_backfill.py                    # Embed all
    python3 scripts/embed_backfill.py --dry-run           # Count only
    python3 scripts/embed_backfill.py --project-context /path  # Project-specific
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.embeddings import embed_for_memory
from core.storage import MemoryStore


def main():
    project_context = None
    dry_run = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--project-context" and i + 1 < len(args):
            project_context = args[i + 1]; i += 2
        elif args[i] == "--dry-run":
            dry_run = True; i += 1
        else:
            print(f"Unknown: {args[i]}", file=sys.stderr); sys.exit(1)

    store = MemoryStore(project_context=project_context)

    if not store._vec_available:
        print("sqlite-vec not available. Run from plugin venv.", file=sys.stderr)
        sys.exit(1)

    # Count memories needing embeddings
    total = 0
    need_embedding = []
    for stage in ("consolidated", "crystallized", "instinctive"):
        memories = store.list_by_stage(stage)
        for mem in memories:
            total += 1
            existing = store.get_embedding(mem["id"])
            if existing is None:
                need_embedding.append(mem)

    print(f"Total memories: {total}", file=sys.stderr)
    print(f"Need embedding: {len(need_embedding)}", file=sys.stderr)

    if dry_run:
        store.close()
        return

    embedded = 0
    failed = 0
    for i, mem in enumerate(need_embedding):
        full = store.get(mem["id"])
        title = full.get("title", "")
        summary = full.get("summary", "")
        content = full.get("content", "")

        print(f"  [{i+1}/{len(need_embedding)}] {title[:50]}... ", end="", file=sys.stderr, flush=True)

        embedding = embed_for_memory(title, summary, content)
        if embedding:
            store.store_embedding(mem["id"], embedding)
            embedded += 1
            print("OK", file=sys.stderr)
        else:
            failed += 1
            print("FAILED", file=sys.stderr)

        time.sleep(0.1)  # Light rate limiting

    print(f"\nEmbedded: {embedded}, Failed: {failed}", file=sys.stderr)
    store.close()


if __name__ == "__main__":
    main()
