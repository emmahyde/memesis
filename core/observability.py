"""
Observability foundation for the Memesis memory lifecycle.

Sprint A WS-A — implements C4 (retrieval baseline instrumentation)
and DS-F3 (shadow-prune logger) from the W5 panel consensus.

## Activation formula

    activation = importance × exp(-age_hrs / τ) × (1 + log(1 + access_count))

Design choices:
- Ebbinghaus-style exponential decay: `R = e^(-t/S)` per Zhong et al. 2023
  (MemoryBank, arXiv 2305.10250 §2.2). τ is the *time constant* — the age at
  which recency = 1/e ≈ 0.368. It is NOT a half-life; the actual half-life is
  τ × ln(2) ≈ 0.693τ (panel C6 correction).
- Access reinforcement: `1 + log(1 + access_count)` is sub-linear (prevents
  Matthew Effect runaway) per MemoryBank access-boost semantics. Not derived
  from ACT-R base-level activation (`A = ln(Σ t_i^-d)`, Anderson 1983 ch. 4),
  which uses per-timestamp power-law sums — the two are structurally different
  (panel C1 correction: do not cite ACT-R for this formula).
- Multiplicative combination (importance × recency × access_boost) is a
  deliberate heuristic design choice. Whether additive (Park 2023 §3.2:
  importance + recency + relevance) or multiplicative is empirically unresolved.
  Panel consensus: A/B test required (C1 open decision, OD-A).
- Formula is NOT bounded to [0,1]; normalization before combining with cosine
  similarity scores is caller responsibility.

Zhong et al. 2023: https://arxiv.org/abs/2305.10250
Park et al. 2023: https://arxiv.org/abs/2304.03442

## Naming convention (panel NS-F8)

Future access-reinforcement function (called on retrieval to bump access_count
and last_accessed_at) MUST be named `recency_reinforcement()`, NOT `on_access()`.
Per Collins & Loftus 1975, "spreading activation" propagates through semantic
networks to neighbor nodes — incrementing one node's count is recency
reinforcement, not spreading activation. Reserve "spreading activation" for the
graph-traversal propagation along `linked_observation_ids[]` edges.

## Shadow-prune (DS-F3)

log_shadow_prune() computes what WOULD be pruned under the §9 activation/decay
model but performs NO deletions. Logs go to backfill-output/observability/
shadow-prune.jsonl. The false-prune rate dataset is collected over W5 and
analyzed in W6 before any destructive pruning is enabled.
"""

import json
import math
import os
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# C3 — 30-day dry-run flag (Decision C3)
#
# SHADOW_ONLY = True  → log_shadow_prune() only writes to shadow-prune.jsonl;
#                        no database mutations (dry-run mode).
# SHADOW_ONLY = False → after logging, soft-archives the memory by setting
#                        archived_at = <now_iso> WHERE id=? AND archived_at IS NULL.
#
# Flip to False after 30 days of clean shadow-prune logs confirm the false-prune
# rate is acceptable (see Decision C3 in .context/CONTEXT-agentic-memory-blockers.md).
# ---------------------------------------------------------------------------

SHADOW_ONLY: bool = True

# ---------------------------------------------------------------------------
# Log file locations — relative to repo root, resolved at import time
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(os.environ.get("MEMESIS_REPO_ROOT", Path(__file__).parent.parent))
_OBS_DIR = _REPO_ROOT / "backfill-output" / "observability"


def _obs_dir() -> Path:
    """Return the observability output directory, creating it if needed.

    Re-reads MEMESIS_OBS_DIR / MEMESIS_REPO_ROOT env vars on every call so
    tests (and other isolated contexts) can redirect output without re-importing.
    """
    override = os.environ.get("MEMESIS_OBS_DIR")
    if override:
        target = Path(override)
    else:
        repo_root = Path(os.environ.get("MEMESIS_REPO_ROOT", _REPO_ROOT))
        target = repo_root / "backfill-output" / "observability"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _append_jsonl(path: Path, record: dict) -> None:
    """Append a single JSON record (newline-delimited) to path."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ---------------------------------------------------------------------------
# Pure activation formula
# ---------------------------------------------------------------------------


def compute_activation(
    importance: float,
    age_hours: float,
    decay_tau_hours: float,
    access_count: int,
) -> float:
    """Compute the salience activation score for a memory.

    Parameters
    ----------
    importance:
        Static importance score, 0.0–1.0 (set at extract time).
    age_hours:
        Hours since the memory was last accessed (or created if never accessed).
    decay_tau_hours:
        Time constant τ in hours. At age=τ, recency factor = 1/e ≈ 0.368.
        Derived from salience tier (T1: 720h, T2: 168h, T3: 48h, T4: 12h).
    access_count:
        Number of times this memory has been retrieved (not counting admin
        queries — only user/agent-initiated retrievals per §9).

    Returns
    -------
    float
        Activation score. Not bounded to [0,1]; caller normalizes if combining
        with cosine similarity. Returns 0.0 if decay_tau_hours is zero.
    """
    if decay_tau_hours <= 0:
        return 0.0
    recency = math.exp(-age_hours / decay_tau_hours)
    access_boost = 1.0 + math.log(1.0 + access_count)
    return importance * recency * access_boost


# ---------------------------------------------------------------------------
# C4 — retrieval boundary instrumentation
# ---------------------------------------------------------------------------


def log_retrieval(
    query: str,
    candidate_ids: list[str],
    returned_ids: list[str],
    scores: dict[str, float],
    context: dict,
) -> str:
    """Log a retrieval event to retrieval-trace.jsonl.

    Called at the retrieval boundary — captures the full candidate set,
    which subset was returned, and per-candidate scores. Each call gets
    a unique retrieval_id used to correlate with log_acceptance() calls.

    Parameters
    ----------
    query:
        The query string used for retrieval.
    candidate_ids:
        All memory IDs considered (before any top-k / threshold cut).
    returned_ids:
        Memory IDs actually returned to the caller.
    scores:
        Dict mapping memory_id → final retrieval score.
    context:
        Caller-supplied context dict (session_id, project_context, etc.).
        Missing fields are tolerated.

    Returns
    -------
    str
        A unique retrieval_id (UUID4) for use in log_acceptance().
    """
    retrieval_id = str(uuid.uuid4())
    record = {
        "ts": _now_iso(),
        "retrieval_id": retrieval_id,
        "session_id": context.get("session_id"),
        "query": query,
        "candidate_count": len(candidate_ids),
        "returned_ids": returned_ids,
        "scores": scores,
        "context": {k: v for k, v in context.items() if k != "session_id"},
    }
    _append_jsonl(_obs_dir() / "retrieval-trace.jsonl", record)
    return retrieval_id


def log_acceptance(
    retrieval_id: str,
    accepted_ids: list[str],
    rejected_ids: list[str],
) -> None:
    """Log which retrieved memories were accepted (used) downstream.

    Acceptance signal: a memory is "accepted" if it influenced a downstream
    response — e.g. it appears in consolidator reinforces[], was referenced in
    session injection feedback, or was explicitly flagged as used by the caller.

    STUB NOTE: As of W5, no automatic downstream acceptance signal exists.
    Callers should invoke this function when:
    - consolidator.py processes a reinforcement and lists reinforced memory IDs
    - feedback.py FeedbackLoop.track_usage() marks memories as used
    - Any future session-injection feedback hook fires

    The stub is safe to call with empty lists — it logs the event and produces
    the acceptance-trace.jsonl dataset for future precision@k / acceptance-rate
    analysis (DS-F1 baseline measurement).

    Parameters
    ----------
    retrieval_id:
        The UUID returned by log_retrieval() for the corresponding retrieval.
    accepted_ids:
        Memory IDs that were used/accepted downstream.
    rejected_ids:
        Memory IDs that were returned but not used.
    """
    record = {
        "ts": _now_iso(),
        "retrieval_id": retrieval_id,
        "accepted_ids": accepted_ids,
        "rejected_ids": rejected_ids,
        "accepted_count": len(accepted_ids),
        "rejected_count": len(rejected_ids),
    }
    _append_jsonl(_obs_dir() / "acceptance-trace.jsonl", record)


# ---------------------------------------------------------------------------
# C4 — consolidation decision instrumentation
# ---------------------------------------------------------------------------


def log_consolidation_decision(
    observation_id: str,
    decision: str,
    importance: float,
    kind: str | None,
    knowledge_type: str | None,
    rationale: str,
) -> None:
    """Log a consolidation decision (KEEP/PRUNE/PROMOTE) to consolidation-decisions.jsonl.

    Captures the per-observation KEEP/PRUNE/PROMOTE outcome with enough
    metadata to compute inter-rater agreement (fleiss-kappa) across multiple
    runs once that data exists (DS baseline plan Phase 1).

    Parameters
    ----------
    observation_id:
        Stable ID for the observation being decided on.
    decision:
        One of "KEEP", "PRUNE", "PROMOTE" (case-insensitive; stored as-is).
    importance:
        Importance score at decision time (0.0–1.0).
    kind:
        Memory kind (decision/finding/preference/constraint/correction/open_question
        or None if not yet classified).
    knowledge_type:
        Bloom-Revised knowledge type (factual/conceptual/procedural/metacognitive
        or None if not yet classified).
    rationale:
        Human-readable rationale from the LLM or rule engine.
    """
    record = {
        "ts": _now_iso(),
        "observation_id": observation_id,
        "decision": decision,
        "importance": importance,
        "kind": kind,
        "knowledge_type": knowledge_type,
        "rationale": rationale,
    }
    _append_jsonl(_obs_dir() / "consolidation-decisions.jsonl", record)


# ---------------------------------------------------------------------------
# DS-F3 — shadow-prune logger (NO deletions)
# ---------------------------------------------------------------------------


def log_shadow_prune(
    memory_id: str,
    computed_activation: float,
    threshold: float,
    would_prune: bool,
    importance: float,
    age_hours: float,
    access_count: int,
    tier: str,
) -> None:
    """Log what WOULD be pruned under the §9 activation/decay model.

    DOES NOT DELETE ANYTHING. This function is safe to call on every lifecycle
    sweep — it only writes to shadow-prune.jsonl. Destructive pruning is
    deferred to W6 after false-prune rate analysis.

    Call site: LifecycleManager.sweep_decayed() (core/lifecycle.py) should call
    this for every memory evaluated in a sweep pass. Example:

        for memory in Memory.active():
            age_hrs = (now - last_accessed).total_seconds() / 3600
            activation = compute_activation(
                memory.importance, age_hrs, memory.decay_tau_hours, memory.access_count
            )
            log_shadow_prune(
                memory_id=memory.id,
                computed_activation=activation,
                threshold=PRUNE_THRESHOLD,
                would_prune=(activation < PRUNE_THRESHOLD and tier in ("T3","T4")),
                importance=memory.importance,
                age_hours=age_hrs,
                access_count=memory.access_count,
                tier=tier,
            )

    Parameters
    ----------
    memory_id:
        Memory UUID.
    computed_activation:
        Result of compute_activation() for this memory right now.
    threshold:
        Pruning threshold used (§9 default: 0.05 — uncalibrated, see DS-F3).
    would_prune:
        True if activation < threshold AND tier is T3/T4 (T1/T2 are never
        auto-pruned per §9 policy).
    importance:
        Memory's static importance score.
    age_hours:
        Hours since last access.
    access_count:
        Number of accesses recorded.
    tier:
        Salience tier string: "T1", "T2", "T3", or "T4".
    """
    now_iso = _now_iso()
    record = {
        "ts": now_iso,
        "memory_id": memory_id,
        "computed_activation": computed_activation,
        "threshold": threshold,
        "would_prune": would_prune,
        "importance": importance,
        "age_hours": age_hours,
        "access_count": access_count,
        "tier": tier,
        "shadow_only": SHADOW_ONLY,
    }
    _append_jsonl(_obs_dir() / "shadow-prune.jsonl", record)

    # When SHADOW_ONLY is False and this memory would be pruned, perform the
    # soft-archive: set archived_at = <now_iso> if not already archived.
    # This does NOT hard-delete — hard deletion is deferred to prune_sweep.py
    # which runs after the 2× TTL window (Decision B1).
    if not SHADOW_ONLY and would_prune:
        try:
            from core.models import db
            db.execute_sql(
                "UPDATE memories SET archived_at = ? WHERE id = ? AND archived_at IS NULL",
                (now_iso, memory_id),
            )
        except Exception:
            # Never let a DB write failure break the logging path.
            pass
