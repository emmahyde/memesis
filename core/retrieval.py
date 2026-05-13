"""
Three-tier retrieval engine with context matching and token budget.

Tier 1 — Instinctive: always injected, zero decision overhead.
Tier 2 — Crystallized: context-matched, token-budgeted.
Tier 3 — Active search: agent-initiated FTS with progressive disclosure.

NOTE: Future write-tools MUST set source='agent'; _compute_priors() should
filter source != 'agent' OR access_count > K once agent-write path ships.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from peewee import fn

import json

from .database import get_base_dir, get_vec_store
from .flags import get_flag
from .models import (
    Memory,
    MemoryEdge,
    NarrativeThread,
    RetrievalCandidate,
    RetrievalLog,
    ThreadMember,
    db,
)
from .tiers import stage_to_tier, tier_ttl
from .codebook import (
    get_codebook_summary,
    is_codebook_enabled,
    contains_codebook_tokens,
)

if TYPE_CHECKING:
    from .vec import VecStore


# ---------------------------------------------------------------------------
# RISK-11: Cognitive module registry and experimental opt-in
#
# Modules listed here contribute scores to retrieval ranking.  Modules marked
# experimental=True are excluded from scoring by default; opt-in via the env
# var MEMESIS_EXPERIMENTAL_MODULES (comma-separated module names).
#
# Example: MEMESIS_EXPERIMENTAL_MODULES=self_reflection,coherence
# ---------------------------------------------------------------------------

_COGNITIVE_MODULES = [
    "affect",
    "coherence",
    "habituation",
    "orienting",
    "replay",
    "self_reflection",
    "somatic",
]


def _get_enabled_modules() -> set[str]:
    """Return the set of cognitive module names active for scoring.

    Non-experimental modules are always enabled.
    Experimental modules are enabled only when listed in the
    MEMESIS_EXPERIMENTAL_MODULES environment variable.
    """
    import importlib

    opt_in_raw = os.environ.get("MEMESIS_EXPERIMENTAL_MODULES", "")
    opted_in = {name.strip() for name in opt_in_raw.split(",") if name.strip()}

    enabled: set[str] = set()
    for module_name in _COGNITIVE_MODULES:
        try:
            mod = importlib.import_module(f"core.{module_name}")
            is_experimental = getattr(mod, "experimental", False)
        except Exception:
            is_experimental = False

        if not is_experimental or module_name in opted_in:
            enabled.add(module_name)

    return enabled


def compute_module_scores(memories: list, enabled_modules: set[str] | None = None) -> dict[str, float]:
    """Compute per-module mean score contribution across a set of memories.

    For each enabled cognitive module, extracts the relevant signal from
    each memory's attributes and averages across the set.

    Returns a dict mapping module name -> mean contribution (0.0–1.0).
    Modules absent from enabled_modules contribute 0.0 and are excluded.
    """
    if enabled_modules is None:
        enabled_modules = _get_enabled_modules()

    if not memories:
        return {m: 0.0 for m in _COGNITIVE_MODULES}

    scores: dict[str, list[float]] = {m: [] for m in _COGNITIVE_MODULES}

    for memory in memories:
        # affect: importance-weighted affect_valence presence
        if "affect" in enabled_modules:
            affect_val = getattr(memory, "affect_valence", None)
            scores["affect"].append(1.0 if affect_val and affect_val != "neutral" else 0.0)

        # somatic: same signal as affect (valence presence)
        if "somatic" in enabled_modules:
            affect_val = getattr(memory, "affect_valence", None)
            scores["somatic"].append(1.0 if affect_val and affect_val != "neutral" else 0.0)

        # habituation: modeled as inverse of reinforcement_count (higher = less novel)
        if "habituation" in enabled_modules:
            rc = getattr(memory, "reinforcement_count", 0) or 0
            import math
            factor = 1.0 / (1.0 + math.log(max(rc, 1)))
            scores["habituation"].append(factor)

        # orienting: modeled as importance above baseline (higher importance = orienting)
        if "orienting" in enabled_modules:
            imp = getattr(memory, "importance", 0.5) or 0.5
            scores["orienting"].append(max(0.0, (imp - 0.5) * 2.0))

        # replay: injection_count relative to reinforcement (frequently replayed = salient)
        if "replay" in enabled_modules:
            ic = getattr(memory, "injection_count", 0) or 0
            scores["replay"].append(min(1.0, ic / 10.0))

        # coherence: divergence flag presence (tagged memories score higher on coherence need)
        if "coherence" in enabled_modules:
            tags = getattr(memory, "tag_list", []) or []
            scores["coherence"].append(1.0 if "coherence_divergent" in tags else 0.0)

        # self_reflection: only present when explicitly opted in
        if "self_reflection" in enabled_modules:
            stage = getattr(memory, "stage", "") or ""
            scores["self_reflection"].append(1.0 if stage == "instinctive" else 0.0)

    result: dict[str, float] = {}
    for module_name in _COGNITIVE_MODULES:
        vals = scores[module_name]
        result[module_name] = sum(vals) / len(vals) if vals else 0.0

    return result

CONTEXT_WINDOW_CHARS = 200_000 * 4  # 200K tokens x 4 chars/token
# Friction memories encode emotionally-salient negative affect (Kensinger 2007);
# a small additive boost ensures they rank above equal-RRF neutral memories.
AFFECT_FRICTION_BOOST = 0.02
THREAD_BUDGET_CHARS = 8_000
TENSION_BUDGET_CHARS = 2_000
_THREAD_NARRATIVE_CAP = 1_000

# ---------------------------------------------------------------------------
# Channel capacity note (linguistic-compression research)
# ---------------------------------------------------------------------------
# The 8% token budget (~16K tokens for a 200K context window) is Memesis's
# analog to the ~39 bits/second universal information rate found across human
# languages (Coupé et al. 2019).  Natural languages converge on this rate
# despite enormous variation in syllable-level density (5–8 bits/syllable)
# because faster-speaking languages use lower-density syllables and vice versa.
#
# Similarly, the LLM context window has a finite "channel capacity."  We
# cannot exceed it by stuffing more tokens — we can only reallocate cognitive
# labor between the memory system (encoder) and the model (decoder).  The 8%
# budget was chosen as a conservative fraction that leaves ample room for
# conversation history, tool outputs, and user prompts while still injecting
# enough crystallized context to be useful.
#
# If this fraction were derived from first principles rather than empirically,
# it would follow the Uniform Information Density hypothesis (Levy & Jaeger
# 2007): smooth surprisal across the context window so no section is
# overwhelmingly dense or sparse.  In practice, 8% appears to hit this balance
# for code-assistance sessions, but it should be re-evaluated as model context
# windows grow or session patterns change.
# ---------------------------------------------------------------------------


def _record_injection(memory_id: str, session_id: str, project_context: str = None, retrieval_id: str | None = None) -> None:
    """Record that a memory was injected into a session."""
    now = datetime.now().isoformat()
    Memory.update(
        last_injected_at=now,
        injection_count=Memory.injection_count + 1,
    ).where(Memory.id == memory_id).execute()

    metadata_json = json.dumps({"retrieval_id": retrieval_id}) if retrieval_id else None
    RetrievalLog.create(
        timestamp=now,
        session_id=session_id,
        memory_id=memory_id,
        retrieval_type='injected',
        project_context=project_context,
        metadata=metadata_json,
    )


class RetrievalEngine:
    """
    Three-tier retrieval engine for memory injection.

    Token budget is expressed as a fraction of the 200K-token context window
    (approximated at 4 chars per token).  Default 8% yields ~16 000 tokens.
    """

    def __init__(self, token_budget_pct: float = 0.08):
        """
        Args:
            token_budget_pct: Fraction of context window reserved for Tier-2
                crystallized memories.  Must be in (0, 1].
        """
        if not 0 < token_budget_pct <= 1:
            raise ValueError(
                f"token_budget_pct must be between 0 (exclusive) and 1 "
                f"(inclusive), got {token_budget_pct}"
            )
        self.token_budget_pct = token_budget_pct
        # token_limit is in *characters* (chars/4 is the token estimate)
        self.token_limit = int(token_budget_pct * 200_000) * 4  # chars
        self._last_hybrid_candidates = []
        # RISK-11: last computed module_scores (dict[str, float]) populated after each retrieval call.
        self._last_module_scores: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject_for_session(
        self,
        session_id: str,
        project_context: str = None,
        query: str = None,
        query_embedding: bytes | None = None,
        session_affect: dict | None = None,
    ) -> str:
        """
        Build the full memory context string for injection into a session.

        If ``query`` is provided, Tier 2 retrieval uses hybrid RRF ranking
        instead of the static sort.  ``query_embedding`` is optional; when
        absent the vector leg is skipped and FTS-only RRF applies.
        """
        tier1 = self.get_instinctive_memories()
        tier2 = self.get_crystallized_for_context(
            project_context=project_context,
            token_limit=self.token_limit,
            query=query,
            query_embedding=query_embedding,
        )

        if query is not None:
            self._record_retrieval_run(
                query=query,
                session_id=session_id,
                retrieval_type="injected_query",
                project_context=project_context,
                selected_ids=[m.id for m in tier2],
                limit=len(tier2),
            )

        # Observability: emit one retrieval-trace covering this session's injection.
        # Captures the candidate set so downstream acceptance signals (track_usage)
        # can compute precision@k. Retrieval_id stashed on each RetrievalLog row
        # via _record_injection so track_usage can correlate later.
        retrieval_id: str | None = None
        try:
            from .observability import log_retrieval
            returned_ids = [m.id for m in tier1 + tier2]
            if returned_ids:
                retrieval_id = log_retrieval(
                    query=query or "",
                    candidate_ids=returned_ids,
                    returned_ids=returned_ids,
                    scores={m.id: float(m.importance or 0.0) for m in tier1 + tier2},
                    context={
                        "session_id": session_id,
                        "project_context": project_context,
                        "tier1_count": len(tier1),
                        "tier2_count": len(tier2),
                        "source": "inject_for_session",
                    },
                )
        except Exception:
            retrieval_id = None

        # Log injections for every memory surfaced (retrieval_id correlates them)
        for memory in tier1 + tier2:
            _record_injection(memory.id, session_id, project_context=project_context, retrieval_id=retrieval_id)

        # RISK-11: compute per-module scores over all injected memories.
        self._last_module_scores = compute_module_scores(tier1 + tier2)

        if not tier1 and not tier2:
            return ""

        sections = ["---MEMORY CONTEXT---", ""]

        # Tier 1 — Instinctive (behavioral guidelines)
        if tier1:
            sections.append("## Your Behavioral Guidelines (always active)")
            for memory in tier1:
                sections.append("")
                title = memory.title or "Guideline"
                sections.append(f"### {title}")
                content = (memory.content or "").strip()
                if content:
                    sections.append(content)

        # Tier 2 — Crystallized (context-relevant knowledge)
        if tier2:
            if get_flag("provenance_signals"):
                provenance_map = self._compute_provenance_batch([m.id for m in tier2])
            else:
                provenance_map = {}

            sections.append("")
            sections.append("## Context-Relevant Knowledge")
            for memory in tier2:
                sections.append("")
                title = memory.title or "Memory"
                importance = memory.importance or 0.5
                sections.append(f"### {title} (importance: {importance:.2f})")
                if memory.id in provenance_map:
                    sections.append(f"*{provenance_map[memory.id]}*")
                summary = (memory.summary or "").strip()
                if summary:
                    sections.append(f"*{summary}*")
                content = (memory.content or "").strip()
                if content:
                    sections.append(content)

        # Tier 2.5 — Narrative threads (episodic arcs for injected memories)
        thread_narratives = self._get_thread_narratives(tier2, session_affect=session_affect)
        if thread_narratives:
            sections.append("")
            sections.append("## Narrative Threads (how understanding evolved)")
            for thread in thread_narratives:
                sections.append("")
                title = thread.title or "Thread"
                sections.append(f"### {title}")
                narrative = (thread.narrative or "").strip()
                if narrative:
                    sections.append(narrative)

        # Tier 2.6 — Active Tensions (unresolved contradictions)
        if get_flag("contradiction_tensors") and tier2:
            tension_blocks = self._get_active_tensions(tier2)
            if tension_blocks:
                sections.append("")
                sections.append(
                    "## Active Tensions (conflicting memories — context determines which applies)"
                )
                for block in tension_blocks:
                    sections.append("")
                    sections.append(block)

        sections.append("")
        sections.append("---END MEMORY CONTEXT---")

        result = "\n".join(sections)

        if is_codebook_enabled():
            all_content = "\n".join(
                (m.content or "") for m in tier1 + tier2
            )
            if contains_codebook_tokens(all_content):
                codebook_summary = get_codebook_summary()
                result = result.replace(
                    "---MEMORY CONTEXT---\n",
                    f"---MEMORY CONTEXT---\n\n{codebook_summary}\n",
                )

        return result

    def active_search(
        self,
        query: str,
        session_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """
        Agent-initiated hybrid search (Tier 3) with progressive disclosure.

        Uses hybrid_search (FTS + optional vector) via RRF fusion.  If the
        Bedrock embedding API is unavailable, gracefully falls back to FTS-only
        ranking.  Hydrates Memory objects from the ranked IDs and returns them
        as dicts with progressive-disclosure fields.
        """
        # Attempt to get query embedding (lazy import avoids import-time Bedrock dependency)
        query_embedding = None
        try:
            from .embeddings import embed_text
            query_embedding = embed_text(query)
        except Exception:
            pass  # FTS-only fallback

        # Run hybrid RRF fusion (falls back to FTS-only if query_embedding is None)
        ranked = self.hybrid_search(
            query=query,
            query_embedding=query_embedding,
            k=limit,
            vec_store=get_vec_store(),
        )

        if not ranked:
            return []

        # Hydrate Memory objects in ranked order
        ranked_ids = [mid for mid, _ in ranked]
        memories_by_id = {
            m.id: m
            for m in Memory.select().where(Memory.id.in_(ranked_ids))
        }

        # Preserve RRF order when building the output
        disclosed = []
        selected_memories = []
        for memory_id, rrf_score in ranked:
            memory = memories_by_id.get(memory_id)
            if memory is None:
                continue
            selected_memories.append(memory)
            disclosed.append({
                "id": memory.id,
                "title": memory.title,
                "summary": memory.summary,
                "content": memory.content or "",
                "importance": memory.importance or 0.5,
                "stage": memory.stage,
                "tags": memory.tag_list,
                "rank": rrf_score,
                "project_context": memory.project_context,
            })

        # RISK-11: compute per-module contribution scores and attach to each result
        module_scores = compute_module_scores(selected_memories)
        self._last_module_scores = module_scores
        for item in disclosed:
            item["module_scores"] = module_scores

        self._record_retrieval_run(
            query=query,
            session_id=session_id,
            retrieval_type="active_search",
            project_context=None,
            selected_ids=[d["id"] for d in disclosed],
            limit=limit,
        )

        return disclosed

    def _record_retrieval_run(
        self,
        query: str,
        session_id: str,
        retrieval_type: str,
        project_context: str | None,
        selected_ids: list[str],
        limit: int,
    ) -> None:
        """Persist per-candidate retrieval scoring for Observer inspection."""
        candidates = self._last_hybrid_candidates or []
        if not candidates:
            return

        now = datetime.now().isoformat()
        selected = set(selected_ids)
        try:
            log = RetrievalLog.create(
                timestamp=now,
                session_id=session_id,
                memory_id=None,
                retrieval_type=retrieval_type,
                was_used=1 if selected_ids else 0,
                relevance_score=candidates[0]["final_score"] if candidates else None,
                project_context=project_context,
                query_text=query,
                limit_count=limit,
                selected_count=len(selected_ids),
                metadata=json.dumps({"candidate_count": len(candidates)}),
            )

            for candidate in candidates:
                RetrievalCandidate.create(
                    retrieval_log_id=log.id,
                    memory_id=candidate["memory_id"],
                    rank=candidate["rank"],
                    fts_rank=candidate.get("fts_rank"),
                    vector_rank=candidate.get("vector_rank"),
                    semantic_score=candidate.get("semantic_score", 0.0),
                    recency_score=candidate.get("recency_score", 0.0),
                    importance_score=candidate.get("importance_score", 0.0),
                    affect_score=candidate.get("affect_score", 0.0),
                    reinforcement_score=candidate.get("reinforcement_score", 0.0),
                    boost_score=candidate.get("boost_score", 0.0),
                    final_score=candidate.get("final_score", 0.0),
                    was_selected=1 if candidate["memory_id"] in selected else 0,
                    metadata=json.dumps(candidate.get("metadata", {})),
                )
        except Exception:
            # Retrieval must stay available even if observer instrumentation lags a migration.
            return

    def hybrid_search(
        self,
        query: str,
        query_embedding: bytes | None = None,
        k: int = 20,
        rrf_k: int = 60,
        vec_store: "VecStore | None" = None,
    ) -> list[tuple[str, float]]:
        """
        Reciprocal Rank Fusion over FTS and vector search legs.

        Combines BM25 full-text search with KNN vector search into a single
        ranked list.  Each leg contributes RRF terms: 1 / (rrf_k + rank).
        Memories absent from a leg are not penalised — they simply receive
        fewer RRF terms.

        Args:
            query: Text query sent to the FTS leg.
            query_embedding: Serialised embedding bytes for the vector leg.
                If None, the vector leg is skipped.
            k: Maximum number of results to return; also the per-leg candidate
                limit fed to FTS / vector search.
            rrf_k: RRF smoothing constant (default 60 per research literature).
            vec_store: Optional VecStore instance.  If None or not available,
                the method falls back to FTS-only ranking.

        Returns:
            List of (memory_id, rrf_score) tuples, sorted by score descending,
            limited to at most ``k`` entries.
        """
        # --- FTS leg -------------------------------------------------------
        fts_query = Memory.tokenize_fts_query(query)
        fts_results = Memory.search_fts(fts_query, limit=k)
        # Build {memory_id: 1-based rank} from FTS order
        fts_ranks: dict[str, int] = {
            mem.id: rank for rank, mem in enumerate(fts_results, start=1)
        }

        # --- Vector leg (conditional) ---------------------------------------
        vec_ranks: dict[str, int] = {}
        use_vec = (
            vec_store is not None
            and vec_store.available
            and query_embedding is not None
        )
        if use_vec:
            vec_results = vec_store.search_vector(query_embedding, k=k)
            vec_ranks = {
                r["memory_id"]: rank
                for rank, r in enumerate(vec_results, start=1)
            }

        # --- RRF fusion ----------------------------------------------------
        all_ids = set(fts_ranks) | set(vec_ranks)
        if not all_ids:
            return []

        scores: dict[str, float] = {}
        component_rows: dict[str, dict] = {}
        for memory_id in all_ids:
            score = 0.0
            lexical_score = 0.0
            semantic_score = 0.0
            if memory_id in fts_ranks:
                lexical_score = 1.0 / (rrf_k + fts_ranks[memory_id])
                score += lexical_score
            if memory_id in vec_ranks:
                semantic_score = 1.0 / (rrf_k + vec_ranks[memory_id])
                score += semantic_score
            scores[memory_id] = score
            component_rows[memory_id] = {
                "memory_id": memory_id,
                "fts_rank": fts_ranks.get(memory_id),
                "vector_rank": vec_ranks.get(memory_id),
                "semantic_score": semantic_score,
                "recency_score": 0.0,
                "importance_score": 0.0,
                "affect_score": 0.0,
                "reinforcement_score": 0.0,
                "boost_score": lexical_score,
                "final_score": score,
                "metadata": {"query": query, "rrf_k": rrf_k},
            }

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        self._last_hybrid_candidates = [
            {**component_rows[memory_id], "rank": rank}
            for rank, (memory_id, _score) in enumerate(ranked, start=1)
        ]

        # Filter to live (non-expired, non-archived) memories only.
        # FTS and vec legs do not know about expires_at, so we post-filter here.
        top_k = ranked[:k]
        if not top_k:
            return top_k

        top_k_ids = [mid for mid, _ in top_k]
        live_ids = {
            m.id
            for m in Memory.live().where(Memory.id.in_(top_k_ids))
        }
        top_k = [(mid, score) for mid, score in top_k if mid in live_ids]

        # Consume-bump: single batched UPDATE for all returned memories (B3/B4).
        # T1 (instinctive, tier_ttl=None) rows keep expires_at=NULL — they are
        # excluded from the expires_at part of the SET clause via per-row SQL
        # with CASE.  Choice: single executemany with per-row values is cleaner
        # than a CASE expression over unknown-length IN lists.
        if top_k:
            self._bump_accessed(top_k_ids=[mid for mid, _ in top_k])

        return top_k

    def _bump_accessed(self, top_k_ids: list[str]) -> None:
        """Issue a single batched UPDATE to record consumption of returned memories.

        Sets last_accessed_at to now (UTC ISO) for all ids.
        Sets expires_at to now_unix + tier_ttl for non-T1 memories;
        T1 memories (instinctive, tier_ttl=None) are skipped in the expires_at
        update — their expires_at remains NULL (never expire).

        Implemented as a single executemany so write contention is one round-trip
        per query, not one per memory (CONTEXT write-contention concern).
        """
        if not top_k_ids:
            return

        # Load stage for each returned memory to compute per-row expiry.
        stage_by_id: dict[str, str] = {
            m.id: m.stage
            for m in Memory.select(Memory.id, Memory.stage).where(
                Memory.id.in_(top_k_ids)
            )
        }

        now_unix = int(time.time())
        now_iso = datetime.now(timezone.utc).isoformat()

        # Build per-row (last_accessed_at, expires_at, id) tuples.
        # For T1 memories tier_ttl returns None; pass None so the column stays NULL.
        rows = []
        for mid in top_k_ids:
            stage = stage_by_id.get(mid, "ephemeral")
            ttl = tier_ttl(stage_to_tier(stage))
            new_expiry = (now_unix + ttl) if ttl is not None else None
            rows.append((now_iso, new_expiry, mid))

        # Single executemany — one round-trip to the DB.
        # db.execute_sql() only accepts a single params tuple; use the underlying
        # sqlite3 connection directly for executemany support.
        db.connection().executemany(
            "UPDATE memories SET last_accessed_at = ?, expires_at = ? WHERE id = ?",
            rows,
        )

    def _get_thread_narratives(
        self,
        tier2_memories: list,
        session_affect: dict | None = None,
    ) -> list:
        """
        Find narrative threads whose members appear in tier2_memories.

        When session_affect is provided and frustration > 0.3, and the
        affect_signatures flag is enabled, threads are reordered:
          - frustration_to_mastery threads are prioritized (surfaced first)
          - sustained_struggle threads are deprioritized (sorted last)
        Threads with arc_affect = NULL are treated as neutral.
        """
        if not tier2_memories:
            return []

        memory_ids = [m.id for m in tier2_memories]
        if not memory_ids:
            return []

        # Batch query for threads containing any of the tier2 memory IDs
        candidates = list(
            NarrativeThread.select()
            .join(ThreadMember, on=(NarrativeThread.id == ThreadMember.thread_id))
            .where(ThreadMember.memory_id.in_(memory_ids))
            .distinct()
            .order_by(NarrativeThread.updated_at.desc())
        )

        # Per-narrative cap: truncate at sentence boundary
        for t in candidates:
            narrative = t.narrative or ""
            if len(narrative) > _THREAD_NARRATIVE_CAP:
                truncated = narrative[:_THREAD_NARRATIVE_CAP]
                last_period = truncated.rfind(".")
                if last_period > _THREAD_NARRATIVE_CAP // 2:
                    truncated = truncated[:last_period + 1]
                t.narrative = truncated

        # Affect-aware ordering: when frustration > 0.3 and flag is on
        frustration = (session_affect or {}).get("frustration", 0.0)
        if frustration > 0.3 and get_flag("affect_signatures"):
            def _affect_sort_key(thread):
                """Return (priority_bucket, narrative_length).

                Bucket 0 = frustration_to_mastery (highest priority)
                Bucket 1 = neutral / NULL / unrecognised trajectory
                Bucket 2 = sustained_struggle (deprioritized)
                """
                arc_affect_raw = thread.arc_affect
                if not arc_affect_raw:
                    return (1, len(thread.narrative or ""))
                try:
                    arc_data = json.loads(arc_affect_raw)
                    trajectory = arc_data.get("trajectory", "")
                except Exception:
                    trajectory = ""
                if trajectory == "frustration_to_mastery":
                    return (0, len(thread.narrative or ""))
                elif trajectory == "sustained_struggle":
                    return (2, len(thread.narrative or ""))
                else:
                    return (1, len(thread.narrative or ""))

            candidates_sorted = sorted(candidates, key=_affect_sort_key)
        else:
            # Greedy budget: shortest first maximises arc count
            candidates_sorted = sorted(candidates, key=lambda t: len(t.narrative or ""))

        budget_remaining = THREAD_BUDGET_CHARS
        selected = []
        for thread in candidates_sorted:
            cost = len(thread.narrative or "")
            if cost <= budget_remaining:
                selected.append(thread)
                budget_remaining -= cost

        # Lazy update: record surfacing timestamp
        if selected:
            now = datetime.now(timezone.utc).isoformat()
            thread_ids = [t.id for t in selected]
            NarrativeThread.update(last_surfaced_at=now).where(
                NarrativeThread.id.in_(thread_ids)
            ).execute()

        return selected

    def _get_active_tensions(self, tier2_memories: list) -> list[str]:
        """
        Surface unresolved contradiction edges for Tier 2.6 (Active Tensions).

        Queries MemoryEdge for edge_type="contradicts" where source_id or
        target_id is in the injected set.  Only includes edges where
        metadata.resolved == false (or metadata is absent / unparseable, which
        is treated as unresolved).

        Returns a list of formatted tension block strings, packed greedily
        within TENSION_BUDGET_CHARS.
        """
        if not tier2_memories:
            return []

        memory_ids = set(m.id for m in tier2_memories)

        # Single batch query: all contradicts edges where source or target is
        # in the injected set.  Avoids N+1 by fetching all at once.
        edges = list(
            MemoryEdge.select()
            .where(
                MemoryEdge.edge_type == "contradicts",
                (MemoryEdge.source_id.in_(memory_ids)) | (MemoryEdge.target_id.in_(memory_ids)),
            )
        )

        if not edges:
            return []

        # Filter to unresolved edges only (D-02)
        unresolved_edges = []
        for edge in edges:
            if not edge.metadata:
                # No metadata means resolution state unknown — treat as unresolved
                unresolved_edges.append(edge)
                continue
            try:
                meta = json.loads(edge.metadata)
            except (ValueError, TypeError):
                unresolved_edges.append(edge)
                continue
            if meta.get("resolved") is not True:
                unresolved_edges.append(edge)

        if not unresolved_edges:
            return []

        # Batch-load Memory objects for all referenced IDs in one query
        all_referenced_ids = set()
        for edge in unresolved_edges:
            all_referenced_ids.add(edge.source_id)
            all_referenced_ids.add(edge.target_id)

        memories_by_id = {
            m.id: m
            for m in Memory.select(Memory.id, Memory.title, Memory.summary).where(
                Memory.id.in_(all_referenced_ids)
            )
        }

        # Deduplicate: same pair may appear as both (A→B) and (B→A)
        seen_pairs: set[frozenset] = set()
        formatted_blocks: list[str] = []

        for edge in unresolved_edges:
            pair = frozenset([edge.source_id, edge.target_id])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            source_mem = memories_by_id.get(edge.source_id)
            target_mem = memories_by_id.get(edge.target_id)

            # Build position descriptions using title + summary
            def _describe(mem) -> str:
                if mem is None:
                    return "(memory not found)"
                title = mem.title or "Untitled"
                summary = (mem.summary or "").strip()
                if summary:
                    return f"{title}: {summary}"
                return title

            position_a = _describe(source_mem)
            position_b = _describe(target_mem)

            # Parse context from edge metadata if available
            context_note = ""
            if edge.metadata:
                try:
                    meta = json.loads(edge.metadata)
                    context = meta.get("context", "")
                    if context:
                        context_note = f"\nContext: {context}"
                except (ValueError, TypeError):
                    pass

            block = (
                f"### Tension\n"
                f"Position A: {position_a}\n"
                f"Position B: {position_b}"
                f"{context_note}"
            )
            formatted_blocks.append(block)

        # Greedy budget packing
        budget_remaining = TENSION_BUDGET_CHARS
        selected: list[str] = []
        for block in formatted_blocks:
            cost = len(block)
            if cost <= budget_remaining:
                selected.append(block)
                budget_remaining -= cost

        return selected

    def get_instinctive_memories(self) -> list:
        """
        Return all instinctive memories with their content loaded.
        Tier 1 — no filtering, no budget limits.
        """
        return list(Memory.by_stage("instinctive"))

    def get_crystallized_for_context(
        self,
        project_context: str = None,
        token_limit: int = None,
        query: str = None,
        query_embedding: bytes | None = None,
    ) -> list:
        """
        Return token-budgeted crystallized memories, optionally boosted by
        project context.

        When ``query`` is provided, uses hybrid RRF ranking (FTS + optional
        vector) instead of the static three-pass sort.  When ``query`` is None,
        preserves the original static sort behaviour exactly (backward
        compatible — SessionStart injection has no query).
        """
        if token_limit is None:
            token_limit = self.token_limit

        if query is not None:
            return self._crystallized_hybrid(
                query=query,
                query_embedding=query_embedding,
                project_context=project_context,
                token_limit=token_limit,
            )

        # --- Static path (no query) — preserved exactly as before --------------
        from .spaced import is_injection_eligible
        # Tier 2 includes crystallized + high-importance consolidated (>= 0.7).
        # High-importance consolidated memories may carry critical behavioral signals
        # before they accumulate enough reinforcement to crystallize.
        crystallized = [m for m in Memory.by_stage("crystallized") if is_injection_eligible(m)]
        high_value_consolidated = [
            m for m in Memory.by_stage("consolidated")
            if is_injection_eligible(m) and (m.importance or 0.0) >= 0.7
        ]
        records = crystallized + high_value_consolidated

        # Three-pass stable sort
        # DEFERRED: Interleave injection ordering (primacy/recency slots)
        # — "Lost in the middle" research shows positional degradation at 20+ doc contexts,
        #   but memesis injects 5-15 memories (~2K tokens) where positional effects are negligible.
        #   Revisit if injection block grows to 30+ memories or token budget exceeds 10K tokens.
        #   See: .context/DEFERRED-COMPRESSION.md #1
        records_sorted = sorted(
            records,
            key=lambda m: m.last_used_at or "",
            reverse=True,
        )
        records_sorted = sorted(
            records_sorted,
            key=lambda m: m.importance or 0.0,
            reverse=True,
        )
        records_sorted = sorted(
            records_sorted,
            key=lambda m: (
                0
                if (
                    project_context is not None
                    and m.project_context == project_context
                )
                else 1
            ),
        )

        if get_flag("thompson_sampling"):
            records_sorted = self._thompson_rerank(records_sorted)

        # Apply token budget
        budget_remaining = token_limit
        selected = []

        for record in records_sorted:
            content = record.content or ""
            cost = len(content)

            if cost <= budget_remaining:
                selected.append(record)
                budget_remaining -= cost

        return selected

    def _compute_provenance_batch(self, memory_ids: list[str]) -> dict[str, str]:
        """
        Compute human-readable provenance strings for a batch of memory IDs.

        Issues a single aggregating query against RetrievalLog to get per-memory
        session counts and earliest retrieval timestamps, then formats strings:

        - session_count > 1: "Established across N sessions over M weeks"
          (uses "over less than a week" when weeks == 0)
        - session_count <= 1 or no log entries: "First observed {relative_time}"
          where relative_time is computed from Memory.created_at
          ("recently" for <1 day, "N days ago", "N weeks ago", etc.)

        Returns a dict mapping memory_id -> provenance string.
        """
        if not memory_ids:
            return {}

        # Single batched query: session_count + earliest per memory_id
        rows = list(
            RetrievalLog.select(
                RetrievalLog.memory_id,
                fn.COUNT(RetrievalLog.session_id.distinct()).alias("session_count"),
                fn.MIN(RetrievalLog.timestamp).alias("earliest"),
            )
            .where(RetrievalLog.memory_id.in_(memory_ids))
            .group_by(RetrievalLog.memory_id)
        )

        log_by_id: dict[str, tuple[int, str | None]] = {
            row.memory_id: (row.session_count, row.earliest)
            for row in rows
        }

        now = datetime.now()
        result: dict[str, str] = {}

        # Collect IDs needing created_at fallback (single-session or no log entries)
        fallback_ids = [
            mid for mid in memory_ids
            if mid not in log_by_id or log_by_id[mid][0] <= 1
        ]

        # Batch-load created_at for fallback IDs (one query)
        created_at_by_id: dict[str, str | None] = {}
        if fallback_ids:
            for mem in Memory.select(Memory.id, Memory.created_at).where(Memory.id.in_(fallback_ids)):
                created_at_by_id[mem.id] = mem.created_at

        for mid in memory_ids:
            if mid in log_by_id:
                session_count, earliest_str = log_by_id[mid]
            else:
                session_count, earliest_str = 0, None

            if session_count > 1 and earliest_str:
                # Multi-session: compute span in weeks
                try:
                    earliest_dt = datetime.fromisoformat(earliest_str)
                except (ValueError, TypeError):
                    earliest_dt = now

                days_span = (now - earliest_dt).days
                weeks = days_span // 7

                if weeks == 0:
                    week_phrase = "over less than a week"
                elif weeks == 1:
                    week_phrase = "over 1 week"
                else:
                    week_phrase = f"over {weeks} weeks"

                result[mid] = f"Established across {session_count} sessions {week_phrase}"
            else:
                # Single-session or zero-session: relative time from created_at
                created_str = created_at_by_id.get(mid)
                relative = self._relative_time(created_str, now)
                result[mid] = f"First observed {relative}"

        return result

    @staticmethod
    def _relative_time(created_str: str | None, now: datetime) -> str:
        """Format a relative time string from a created_at ISO string."""
        if not created_str:
            return ""
        try:
            created_dt = datetime.fromisoformat(created_str)
        except (ValueError, TypeError):
            return ""

        delta_days = (now - created_dt).days
        if delta_days < 1:
            return "recently"
        elif delta_days == 1:
            return "1 day ago"
        elif delta_days < 7:
            return f"{delta_days} days ago"
        elif delta_days < 14:
            return "1 week ago"
        else:
            weeks = delta_days // 7
            return f"{weeks} weeks ago"

    def _thompson_rerank(self, memories: list) -> list:
        """Re-rank memories using Thompson sampling over Beta(usage+1, unused+1).

        Each memory draws a sample from Beta(a, b) where:
          a = usage_count + 1
          b = max(injection_count - usage_count, 0) + 1

        This gives a Beta(1,1) uniform prior for cold-start memories (injection=0,
        usage=0), and increasingly favours high-usage memories as counts grow.
        The b=max(..., 0)+1 guard handles data anomalies where usage_count
        exceeds injection_count.
        """
        import random

        scored = []
        for mem in memories:
            a = (mem.usage_count or 0) + 1
            b = max((mem.injection_count or 0) - (mem.usage_count or 0), 0) + 1
            sample = random.betavariate(a, b)
            scored.append((sample, mem))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored]

    def _crystallized_hybrid(
        self,
        query: str,
        query_embedding: bytes | None,
        project_context: str | None,
        token_limit: int,
    ) -> list:
        """
        Hybrid RRF path for get_crystallized_for_context when a query is provided.

        1. Run hybrid_search to get (memory_id, rrf_score) ranked list.
        2. Hydrate Memory objects from ranked IDs.
        3. Apply project_context boost (small RRF bonus keeps local memories competitive).
        4. Re-sort by boosted score.
        5. Apply greedy token budget packing.
        """
        # RRF_K constant used for the boost term (same constant as hybrid_search default)
        _RRF_K = 60
        PROJECT_BOOST = 1.0 / (_RRF_K + 0.5)  # ~0.01639

        ranked = self.hybrid_search(
            query=query,
            query_embedding=query_embedding,
            k=50,  # over-fetch to give token budget room to select
            vec_store=get_vec_store(),
        )

        if not ranked:
            return []

        ranked_ids = [mid for mid, _ in ranked]

        # 1-hop graph expansion: add thread/tag neighbors to candidate pool
        from .graph import expand_neighbors
        neighbor_ids = expand_neighbors(ranked_ids, max_expansion=10, vec_store=get_vec_store())
        if neighbor_ids:
            # Neighbors get a reduced RRF score (half of the lowest seed score)
            min_score = min(s for _, s in ranked) if ranked else 0.0
            neighbor_score = min_score * 0.5
            for nid in neighbor_ids:
                ranked.append((nid, neighbor_score))
            ranked_ids = ranked_ids + neighbor_ids

        # Include both crystallized and consolidated memories — both have been
        # through quality gates. Crystallized get a boost in the scoring below.
        memories_by_id = {
            m.id: m
            for m in Memory.select().where(
                Memory.id.in_(ranked_ids),
                Memory.stage.in_(["crystallized", "consolidated"]),
            )
        }

        # Filter by SM-2 eligibility
        from .spaced import is_injection_eligible

        # Crystallized memories get a small RRF boost over consolidated
        CRYSTAL_BOOST = 1.0 / (_RRF_K + 1)  # ~0.01639

        # Build score table with boosts
        scored: list[tuple[float, Memory]] = []
        for memory_id, rrf_score in ranked:
            memory = memories_by_id.get(memory_id)
            if memory is None or not is_injection_eligible(memory):
                continue
            boost = 0.0
            if memory.stage == "crystallized":
                boost += CRYSTAL_BOOST
            if project_context is not None and memory.project_context == project_context:
                boost += PROJECT_BOOST
            if getattr(memory, "affect_valence", None) == "friction":
                boost += AFFECT_FRICTION_BOOST
            scored.append((rrf_score + boost, memory))

        # Propagate affect_score into _last_hybrid_candidates for observer logging
        affect_by_id: dict[str, float] = {
            memory.id: (AFFECT_FRICTION_BOOST if getattr(memory, "affect_valence", None) == "friction" else 0.0)
            for _, memory in scored
        }
        for candidate in self._last_hybrid_candidates:
            mid = candidate["memory_id"]
            if mid in affect_by_id:
                candidate["affect_score"] = affect_by_id[mid]

        scored.sort(key=lambda x: x[0], reverse=True)

        if get_flag("thompson_sampling"):
            ranked_memories = [mem for _, mem in scored]
            ranked_memories = self._thompson_rerank(ranked_memories)
        else:
            ranked_memories = [mem for _, mem in scored]

        # Greedy token budget
        budget_remaining = token_limit
        selected = []
        for memory in ranked_memories:
            content = memory.content or ""
            cost = len(content)
            if cost <= budget_remaining:
                selected.append(memory)
                budget_remaining -= cost

        return selected
