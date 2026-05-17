"""
Bundled-row decomposer — split memories that pack multiple unrelated atoms.

The consolidator sometimes produces a memory carrying several unrelated facts
(e.g. an API-key note buried inside a friction observation). Such rows retrieve
poorly — each atom dilutes the others (canvas review 2026-05-15 §2 / §6.6).

run_decomposer_sweep() audits long, not-yet-checked consolidated memories with
one LLM call each. A memory the LLM judges COHERENT is flagged and left alone;
one it judges SPLIT is replaced by >=2 self-contained child memories and
archived. The flag (memories.decompose_checked) keeps coherent rows from being
re-audited every cron run.

CLAUDE.md Rule 1 — all writes go through the Memory model + a ConsolidationLog
row. Rule 2 — the LLM call goes through core.llm.call_llm.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from peewee import fn

from core.importance import calibrate_importance
from core.llm import call_llm
from core.models import ConsolidationLog, Memory
from core.prompts import MEMORY_DECOMPOSITION_PROMPT
from core.validators import MEMORY_KIND_VALUES

logger = logging.getLogger(__name__)

# Only memories with a body at least this long can plausibly bundle atoms.
MIN_DECOMPOSE_LENGTH = 400
# A SPLIT must produce at least this many children — guards against the LLM
# "splitting" a coherent memory into a single fragment.
MIN_CHILDREN = 2


def _extract_json(raw: str) -> dict:
    """Extract the first {...} block from LLM output."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in LLM output: {(raw or '')[:200]!r}")
    return json.loads(match.group(0))


def _decompose_one(memory: Memory) -> list[dict] | None:
    """Audit one memory. Return a list of child specs to split into, or None.

    None means COHERENT (or an unusable verdict) — leave the memory intact.
    Each returned child spec is a sanitised {title, content, memory_kind} dict.
    """
    prompt = MEMORY_DECOMPOSITION_PROMPT.format(
        title=memory.title or "",
        content=(memory.content or "")[:4000],
    )
    raw = call_llm(prompt, max_tokens=1500, temperature=0)
    parsed = _extract_json(raw)

    if parsed.get("verdict") != "SPLIT":
        return None

    raw_children = parsed.get("children") or []
    children: list[dict] = []
    for child in raw_children:
        if not isinstance(child, dict):
            continue
        content = (child.get("content") or "").strip()
        if not content:
            continue
        kind = child.get("memory_kind")
        if kind not in MEMORY_KIND_VALUES:
            kind = None
        children.append({
            "title": (child.get("title") or "").strip() or "(untitled)",
            "content": content,
            "memory_kind": kind,
        })

    # Guard against over-splitting: a real split has >=2 self-contained children.
    if len(children) < MIN_CHILDREN:
        return None
    return children


def _apply_split(memory: Memory, children: list[dict]) -> None:
    """Create child memories from the specs and archive the bundled original."""
    now = datetime.now().isoformat()
    child_ids: list[str] = []

    for spec in children:
        child = Memory.create(
            stage="consolidated",
            title=spec["title"],
            summary=spec["title"],
            content=spec["content"],
            tags=memory.tags,
            memory_kind=spec["memory_kind"],
            importance=calibrate_importance(
                memory.importance, spec["memory_kind"], spec["content"]
            ),
            reinforcement_count=0,
            created_at=now,
            updated_at=now,
            project=memory.project,
            source_session=memory.source_session,
            commit_ref=memory.commit_ref,
            # Children are single-topic by construction — no need to re-audit.
            decompose_checked=1,
        )
        child_ids.append(child.id)

    Memory.update(archived_at=now, decompose_checked=1).where(
        Memory.id == memory.id
    ).execute()
    ConsolidationLog.create(
        timestamp=now,
        action="deprecated",
        memory_id=memory.id,
        from_stage=memory.stage,
        to_stage="archived",
        rationale=f"Bundled row decomposed into {len(child_ids)} memories: "
                  f"{', '.join(cid[:8] for cid in child_ids)}",
    )
    logger.info(
        "decomposer: split %s (%s) into %d memories",
        memory.title or "untitled", memory.id, len(child_ids),
    )


def run_decomposer_sweep(limit: int = 10) -> dict:
    """Audit up to `limit` un-checked, long consolidated memories for bundling.

    Returns a counts dict: ``checked``, ``split``, ``coherent``, ``errors``.
    """
    counts = {"checked": 0, "split": 0, "coherent": 0, "errors": 0}

    candidates = list(
        Memory.select()
        .where(
            Memory.archived_at.is_null(),
            Memory.stage == "consolidated",
            (Memory.decompose_checked == 0) | Memory.decompose_checked.is_null(),
            fn.LENGTH(fn.COALESCE(Memory.content, "")) >= MIN_DECOMPOSE_LENGTH,
        )
        .limit(limit)
    )

    for memory in candidates:
        counts["checked"] += 1
        try:
            children = _decompose_one(memory)
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the sweep
            counts["errors"] += 1
            logger.warning("decomposer: audit failed for %s: %s", memory.id, exc)
            continue

        if children is None:
            Memory.update(decompose_checked=1).where(Memory.id == memory.id).execute()
            counts["coherent"] += 1
        else:
            _apply_split(memory, children)
            counts["split"] += 1

    logger.info("decomposer sweep: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return counts
