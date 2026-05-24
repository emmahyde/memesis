"""One-shot: split existing project-mixed NarrativeThreads into per-project threads.

Background: prior thread clustering didn't respect project boundaries, so
sector + memesis + codemode-mcp memories got mixed into single threads under
broad abstract titles ("silent-failure surfaces", "trust the source"). New
clustering enforces project isolation (#thread-project-isolation in threads.py),
but historical threads need a migration.

Strategy per mixed thread:
  - Partition members by project.
  - Each project subgroup with ≥2 members becomes a new thread inheriting
    the original title with a "({project_short})" suffix and reusing the
    summary verbatim (still applicable, just narrower scope now).
  - Subgroups with <2 members get returned to the unthreaded pool.
  - Original thread is deleted.

Idempotent: a thread whose members already share one project is left alone.
Run via `uv run python scripts/rethread_by_project.py`.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.database import init_db
from core.models import NarrativeThread, ThreadMember, db


PROJECT_PREFIX = "-Users-emmahyde-projects-"


def _short(project: str | None) -> str:
    if not project:
        return "unscoped"
    if project.startswith(PROJECT_PREFIX):
        return project[len(PROJECT_PREFIX):]
    return project


def split_mixed_thread(thread: NarrativeThread) -> dict:
    """Split one thread by project. Returns stats {kept, created, freed}."""
    members = list(
        ThreadMember.select().where(ThreadMember.thread_id == thread.id)
        .order_by(ThreadMember.position)
    )
    if not members:
        return {"kept": 0, "created": 0, "freed": 0, "deleted_thread": False}

    # Map memory_id -> project via direct lookup (avoid loading full Memory rows)
    from core.models import Memory
    proj_by_id = {
        m.id: m.project
        for m in Memory.select(Memory.id, Memory.project)
        .where(Memory.id.in_([tm.memory_id for tm in members]))
    }

    buckets: dict[str | None, list[ThreadMember]] = defaultdict(list)
    for tm in members:
        buckets[proj_by_id.get(tm.memory_id)].append(tm)

    if len(buckets) <= 1:
        return {"kept": len(members), "created": 0, "freed": 0, "deleted_thread": False}

    stats = {"kept": 0, "created": 0, "freed": 0, "deleted_thread": True}

    with db.atomic():
        # Drop old members; we'll recreate under new thread IDs.
        ThreadMember.delete().where(ThreadMember.thread_id == thread.id).execute()

        for project, tms in buckets.items():
            if len(tms) < 2:
                stats["freed"] += len(tms)
                continue

            new_title = f"{thread.title} ({_short(project)})"
            new_thread = NarrativeThread.create(
                title=new_title[:200],
                summary=thread.summary,
                narrative=thread.narrative,
                created_at=thread.created_at,
                updated_at=thread.updated_at,
                last_surfaced_at=thread.last_surfaced_at,
                arc_affect=thread.arc_affect,
            )
            for pos, tm in enumerate(tms):
                ThreadMember.create(
                    thread_id=new_thread.id,
                    memory_id=tm.memory_id,
                    position=pos,
                )
            stats["created"] += 1
            stats["kept"] += len(tms)

        thread.delete_instance()

    return stats


def main() -> None:
    init_db()
    totals = {"threads_examined": 0, "threads_mixed": 0, "threads_replaced": 0,
              "new_threads": 0, "freed_members": 0}
    for t in list(NarrativeThread.select()):
        totals["threads_examined"] += 1
        res = split_mixed_thread(t)
        if res["deleted_thread"]:
            totals["threads_mixed"] += 1
            totals["threads_replaced"] += 1
            totals["new_threads"] += res["created"]
            totals["freed_members"] += res["freed"]
    print(f"Rethread done. {totals}")


if __name__ == "__main__":
    main()
