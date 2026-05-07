"""
Ghost coherence check — validates self-model claims against memory evidence.

Compares instinctive-tier memories (the system's beliefs about the user)
against consolidated/crystallized evidence. Flags divergences where the
self-model claims something that recent evidence contradicts.

Rate-limited to once per day per project to control LLM costs.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .llm import call_llm, strip_markdown_fences
from .models import Memory

logger = logging.getLogger(__name__)

COHERENCE_PROMPT = """You are checking whether a system's self-model (its beliefs about a user) is consistent with recent evidence from memory.

## Self-Model Claims (instinctive memories)
{claims_block}

## Recent Evidence (consolidated + crystallized memories, last 30 days)
{evidence_block}

For each self-model claim, determine:
- "consistent" — evidence supports or doesn't contradict the claim
- "divergent" — evidence contradicts or conflicts with the claim
- "unsupported" — no evidence either way (claim may be stale)

Return a JSON array. Each element: {{"claim_id": "...", "status": "consistent|divergent|unsupported", "evidence": "one-sentence explanation"}}

Only return the JSON array."""


def check_coherence(
    project_context: str = None,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """Run ghost coherence check.

    Returns:
        {
            "consistent": [{"id": ..., "title": ..., "evidence": ...}, ...],
            "divergent": [{"id": ..., "title": ..., "evidence": ...}, ...],
            "unsupported": [{"id": ..., "title": ..., "evidence": ...}, ...],
            "checked_at": ISO timestamp,
        }
    """
    from .flags import get_flag
    from .database import get_base_dir

    if not get_flag("ghost_coherence"):
        # DEFERRED: Coherence → compression feedback loop — when a memory is flagged
        # as contradictory, reduce compression aggression for that memory in the next
        # injection cycle. Deferred because the coherence check itself isn't battle-tested
        # (behind feature flag, rate-limited to once/day). Building a feedback loop on
        # unverified signal compounds uncertainty. Revisit after 30+ days of verified
        # accuracy with <5% false-positive rate.
        # See: .context/DEFERRED-COMPRESSION.md #5
        return {"consistent": [], "divergent": [], "unsupported": [], "checked_at": None}

    # Rate limit: once per day
    base_dir = get_base_dir()
    if base_dir and _is_rate_limited(base_dir):
        logger.info("Ghost coherence check skipped — already ran today")
        return {"consistent": [], "divergent": [], "unsupported": [], "checked_at": None, "skipped": "rate_limited"}

    # Load self-model claims (instinctive tier)
    claims = list(Memory.by_stage("instinctive"))
    if not claims:
        return {"consistent": [], "divergent": [], "unsupported": [], "checked_at": None}

    # Load recent evidence (consolidated + crystallized, last 30 days)
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    evidence = list(
        Memory.select()
        .where(
            Memory.stage.in_(["consolidated", "crystallized"]),
            Memory.archived_at.is_null(),
            Memory.updated_at >= cutoff,
        )
        .order_by(Memory.updated_at.desc())
        .limit(30)
    )

    if not evidence:
        return {"consistent": [], "divergent": [], "unsupported": [], "checked_at": None}

    # Build prompt blocks
    claims_block = "\n\n".join(
        f"### [{c.id}] {c.title or 'Untitled'}\n{(c.content or '')[:300]}"
        for c in claims
    )
    evidence_block = "\n\n".join(
        f"### {e.title or 'Untitled'} ({e.stage})\n{(e.content or '')[:200]}"
        for e in evidence
    )

    prompt = COHERENCE_PROMPT.format(
        claims_block=claims_block,
        evidence_block=evidence_block,
    )

    try:
        raw = call_llm(prompt, model=model)
        cleaned = strip_markdown_fences(raw)
        decisions = json.loads(cleaned)
    except Exception as e:
        logger.warning("Ghost coherence LLM call failed: %s", e)
        return {"consistent": [], "divergent": [], "unsupported": [], "checked_at": None}

    # Process results
    claim_by_id = {c.id: c for c in claims}
    result = {"consistent": [], "divergent": [], "unsupported": []}
    now = datetime.now().isoformat()

    for decision in decisions:
        cid = decision.get("claim_id", "")
        status = decision.get("status", "consistent")
        evidence_text = decision.get("evidence", "")

        claim = claim_by_id.get(cid)
        if not claim:
            continue

        entry = {"id": cid, "title": claim.title, "evidence": evidence_text}
        result.setdefault(status, []).append(entry)

        # Flag divergent claims on the memory record
        if status == "divergent":
            tags = claim.tag_list
            if "coherence_divergent" not in tags:
                tags.append("coherence_divergent")
                claim.tag_list = tags
                claim.save()
                logger.warning("Ghost coherence: divergent claim '%s' — %s", claim.title, evidence_text)

    result["checked_at"] = now

    # Record timestamp for rate limiting
    if base_dir:
        _record_check(base_dir)

    return result


def _is_rate_limited(base_dir: Path) -> bool:
    """Check if coherence was already run today."""
    marker = base_dir / ".coherence-last-check"
    if not marker.exists():
        return False
    try:
        last = datetime.fromisoformat(marker.read_text().strip())
        return (datetime.now() - last) < timedelta(days=1)
    except Exception:
        return False


def _record_check(base_dir: Path):
    """Record that a coherence check was performed."""
    marker = base_dir / ".coherence-last-check"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now().isoformat())
