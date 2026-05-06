"""
Self-reflection framework for the transcript-extraction layer (C scaffolding).

Closes the loop: after each extraction run, the system observes ITSELF —
which windows produced signal, which were skipped, where token budget was
spent without yield, what affect cues went unused. Produces self-observations
that feed back into the next run via a `self_model.md` file.

Theoretical basis:
- Park et al. 2023 (arXiv 2304.03442 §3.3) "Reflection" mechanism:
  agents periodically synthesize lower-level observations into higher-level
  abstractions about themselves and their environment. The same loop, but
  scoped to extraction process metacognition.
- Schön 1983 (The Reflective Practitioner) — reflection-on-action: knowing
  THAT a skill worked is incomplete without knowing WHY and HOW. Stored
  process knowledge enables transfer to novel contexts.
- Anderson 1983 (ACT-R, Cognitive Skills and their Acquisition, ch. 4) —
  production-rule learning: refining IF-THEN rules from declarative
  observation. self_model.md entries are early-stage productions waiting
  for compilation into procedural memory.

Status — this module is the FRAMEWORK ONLY:
- Data structures + loading/saving + minimal observation generation are
  implemented and exercised.
- Heuristic library that turns run statistics into self-observations is
  bootstrapped with a small ruleset and is the iteration surface.
- Integration with the full memesis Memory model + consolidator is left
  as a follow-up; for now self-observations live in the on-disk
  `self_model.md` and a JSONL audit log.

The framework is intentionally additive: if the self-model file is missing
or empty, extraction proceeds as if the loop did not exist. Failures in
this module never block extraction — they are logged and skipped.

Cross-session meta-rule (recurrent_agent_failure):
  Implemented via Option A: a standalone `reflect_on_corpus` helper that the
  caller (e.g. run_selected_sessions.py) invokes after processing all sessions,
  passing the accumulated correction cards. Option B was rejected because the
  audit JSONL stores SelfObservation dicts — not card content — so we cannot
  reconstruct card problem text from it.

  Caller wiring needed (not implemented here):
  - run_selected_sessions.py: collect CorrectionCard objects during the run
    loop, then call reflect_on_corpus(cards, root=...) once after the loop.
  - transcript_ingest.py: same pattern if it runs multi-session sweeps.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.rule_registry import RULE_OVERRIDES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------

_DEFAULT_ROOT = Path.home() / ".claude" / "memesis"


def _self_model_dir(root: Path | None = None) -> Path:
    base = root or _DEFAULT_ROOT
    out = base / "self-model"
    out.mkdir(parents=True, exist_ok=True)
    return out


def self_model_path(root: Path | None = None) -> Path:
    """Active self-model markdown file the next extraction reads as context."""
    return _self_model_dir(root) / "self_model.md"


def self_model_audit_path(root: Path | None = None) -> Path:
    """Append-only JSONL of every self-observation produced."""
    return _self_model_dir(root) / "self_observations.jsonl"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class SelfObservation:
    """A metacognitive observation about the extraction process itself.

    Mirrors the W5 Stage 1 observation schema where it makes sense (kind,
    knowledge_type, importance, facts) so a future consolidator can ingest
    self-observations alongside session observations without a new pipeline.
    """

    facts: list[str]
    kind: str = "finding"  # finding | correction | open_question | preference
    knowledge_type: str = "metacognitive"
    knowledge_type_confidence: str = "high"
    importance: float = 0.5
    subject: str = "self"  # always "self" for this module
    work_event: str | None = None  # null per session_type contract
    evidence: dict = field(default_factory=dict)  # raw stats that triggered
    proposed_action: str | None = None  # what the rule recommends doing
    rule_id: str = ""  # the heuristic that fired
    ts: str = ""

    def to_dict(self) -> dict:
        return {
            "facts": self.facts,
            "kind": self.kind,
            "knowledge_type": self.knowledge_type,
            "knowledge_type_confidence": self.knowledge_type_confidence,
            "importance": self.importance,
            "subject": self.subject,
            "work_event": self.work_event,
            "evidence": self.evidence,
            "proposed_action": self.proposed_action,
            "rule_id": self.rule_id,
            "ts": self.ts,
        }


@dataclass
class ExtractionRunStats:
    """Per-session run statistics consumed by the reflection rules."""

    session_id: str
    session_type: str
    chunking: str  # "stride" | "user_anchored" | "flat"
    windows: int
    productive_windows: int  # windows that produced ≥1 obs
    raw_observations: int
    final_observations: int  # post-dedup, post-issue-card synthesis
    issue_cards: int
    orphans: int
    skipped_windows: int
    parse_errors: int
    affect_signals_total: int  # n windows with non-zero importance_prior
    affect_quotes_used: int  # n cards with user_reaction populated
    nontrivial_user_turn_count: int
    entry_count: int
    cost_calls: int  # LLM calls made
    dropped_duplicates: int = 0  # Jaccard near-dup losses
    low_importance_dropped: int = 0  # obs filtered at importance < 0.3
    notes: list[str] = field(default_factory=list)
    repeated_fact_hashes: list[str] = field(default_factory=list)  # MD5 hashes of raw obs facts
    unique_knowledge_types_emitted: int = 0  # distinct knowledge_type values in final observations
    repeated_facts_count: int = 0  # fuzzy Jaccard ≥0.55 vs memory store (cross-session paraphrase repeats)
    windows_with_affect_signal_but_no_card: int = 0  # windows with affect max_boost > 0 but no extraction
    min_card_importance: float = 1.0  # lowest importance among final cards

    @property
    def productive_rate(self) -> float:
        if self.windows == 0:
            return 0.0
        return self.productive_windows / self.windows

    @property
    def cost_per_obs(self) -> float:
        if self.final_observations == 0:
            return float("inf") if self.cost_calls > 0 else 0.0
        return self.cost_calls / self.final_observations

    @property
    def obs_per_cost_call(self) -> float:
        if self.cost_calls == 0:
            return 0.0
        return self.raw_observations / self.cost_calls

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_type": self.session_type,
            "chunking": self.chunking,
            "windows": self.windows,
            "productive_windows": self.productive_windows,
            "raw_observations": self.raw_observations,
            "final_observations": self.final_observations,
            "issue_cards": self.issue_cards,
            "orphans": self.orphans,
            "skipped_windows": self.skipped_windows,
            "parse_errors": self.parse_errors,
            "affect_signals_total": self.affect_signals_total,
            "affect_quotes_used": self.affect_quotes_used,
            "nontrivial_user_turn_count": self.nontrivial_user_turn_count,
            "entry_count": self.entry_count,
            "cost_calls": self.cost_calls,
            "productive_rate": self.productive_rate,
            "cost_per_obs": self.cost_per_obs,
            "obs_per_cost_call": self.obs_per_cost_call,
            "repeated_fact_hashes": self.repeated_fact_hashes,
            "notes": self.notes,
            "unique_knowledge_types_emitted": self.unique_knowledge_types_emitted,
            "repeated_facts_count": self.repeated_facts_count,
            "windows_with_affect_signal_but_no_card": self.windows_with_affect_signal_but_no_card,
            "min_card_importance": self.min_card_importance,
        }


# ---------------------------------------------------------------------------
# Heuristic rules — each maps (run_stats) → SelfObservation | None
# ---------------------------------------------------------------------------

_RULES: list = []


def _rule(rule_id: str):
    """Decorator: register a reflection rule."""
    def deco(fn):
        fn.__rule_id__ = rule_id
        _RULES.append(fn)
        return fn
    return deco


@_rule("low_productive_rate")
def _rule_low_productive_rate(stats: ExtractionRunStats) -> SelfObservation | None:
    """Productive window rate below 30% wastes LLM budget."""
    if stats.windows < 4:
        return None
    if stats.productive_rate >= 0.3:
        return None
    return SelfObservation(
        facts=[
            f"Extraction over session {stats.session_id[:12]} "
            f"({stats.chunking} chunking, {stats.windows} windows) had "
            f"{stats.productive_windows} productive windows "
            f"({stats.productive_rate:.0%}); LLM budget on the other "
            f"{stats.skipped_windows} windows produced no observations."
        ],
        kind="finding",
        importance=0.7,
        proposed_action=(
            "Reduce max_windows from current value or pre-filter to "
            "high-affect / decision-anchored segments before LLM call."
        ),
        evidence=stats.to_dict(),
        rule_id="low_productive_rate",
    )


@_rule("chunking_suboptimal")
def _rule_chunking_suboptimal(stats: ExtractionRunStats) -> SelfObservation | None:
    """Chunking strategy was a poor fit for the session shape.

    Bidirectional: catches both directions of mismatch.

      Arm A (original): user_anchored chosen on agent-driven session with
        few user turns → strides past the agent monologue and misses signal.

      Arm B (added 2026-04-28 Phase E): stride chosen on user-dense session
        with many short user turns and low yield → user_anchored would have
        framed each user pivot as a window centroid and likely surfaced
        more decisions.
    """
    arm_a = (
        stats.chunking == "user_anchored"
        and stats.nontrivial_user_turn_count < 5
        and stats.final_observations < 3
    )
    arm_b = (
        stats.chunking == "stride"
        and stats.nontrivial_user_turn_count >= 8
        and stats.final_observations < 3
    )
    if not (arm_a or arm_b):
        return None
    if arm_a:
        fact = (
            f"user_anchored chunking on session {stats.session_id[:12]} "
            f"with only {stats.nontrivial_user_turn_count} substantive user "
            f"turns produced {stats.final_observations} observations; "
            f"agent-driven sessions need stride chunking to surface signal "
            f"buried in long autonomous executions."
        )
        action = (
            "When nontrivial_user_turn_count < 5 OR entries/user_turn ratio "
            "> 30, prefer chunking='stride' over 'user_anchored'."
        )
    else:  # arm_b
        fact = (
            f"stride chunking on session {stats.session_id[:12]} with "
            f"{stats.nontrivial_user_turn_count} substantive user turns "
            f"produced only {stats.final_observations} observations; "
            f"user-dense sessions benefit from user_anchored chunking that "
            f"centers each window on a user pivot."
        )
        action = (
            "When nontrivial_user_turn_count >= 8 and final_observations "
            "< 3, prefer chunking='user_anchored' over 'stride'."
        )
    return SelfObservation(
        facts=[fact],
        kind="correction",
        importance=0.85,
        proposed_action=action,
        evidence=stats.to_dict(),
        rule_id="chunking_suboptimal",
    )


@_rule("affect_blind_spot")
def _rule_affect_blind(stats: ExtractionRunStats) -> SelfObservation | None:
    """High volume, zero affect signals suggests detector is missing cues.

    Universalized: fires on any session_type, since the affect detector
    is supposed to work everywhere. Phase E audit (2026-04-28) confirmed
    that research sessions also produce 0 affect signals despite cards
    bearing user_reaction text, so the original code-only gate hid a
    real gap.
    """
    if stats.entry_count < 50:
        return None
    if stats.affect_signals_total > 0:
        return None
    return SelfObservation(
        facts=[
            f"Session {stats.session_id[:12]} ({stats.entry_count} entries, "
            f"session_type={stats.session_type}) produced 0 windows with "
            f"non-zero affect prior; either the user was uniformly neutral "
            f"(possible) or the somatic detector missed pushback/repetition "
            f"cues that the observation set contains."
        ],
        kind="open_question",
        importance=0.6,
        proposed_action=(
            "Manually inspect 3 random windows from this session for affect "
            "markers the detector missed; consider expanding somatic.py "
            "lexicon."
        ),
        evidence=stats.to_dict(),
        rule_id="affect_blind_spot",
    )


@_rule("parse_errors_present")
def _rule_parse_errors(stats: ExtractionRunStats) -> SelfObservation | None:
    if stats.parse_errors == 0:
        return None
    return SelfObservation(
        facts=[
            f"Extraction on session {stats.session_id[:12]} encountered "
            f"{stats.parse_errors} JSON parse errors from LLM responses; "
            f"hierarchical recovery saved them as orphans, but the prompt "
            f"is letting the model emit unparseable output."
        ],
        kind="correction",
        importance=0.7,
        proposed_action=(
            "Tighten OBSERVATION_EXTRACT_PROMPT or ISSUE_SYNTHESIS_PROMPT "
            "with stricter 'no markdown, no commentary' enforcement; add a "
            "single-shot retry on parse failure before falling through to "
            "orphan-only path."
        ),
        evidence=stats.to_dict(),
        rule_id="parse_errors_present",
    )


@_rule("dedup_inert")
def _rule_dedup_inert(stats: ExtractionRunStats) -> SelfObservation | None:
    """Content-hash dedup drops nothing despite high observation volume.

    With Reframe A (stateful incremental extraction), in-session dedup
    should prevent re-extraction of already-seen facts. Zero drops on a
    dense session may mean windows are genuinely non-overlapping OR that
    Reframe A is disabled and paraphrase duplicates are passing through.
    """
    if stats.raw_observations < 30:
        return None
    if stats.dropped_duplicates > 0:
        return None
    return SelfObservation(
        facts=[
            f"Session {stats.session_id[:12]} produced "
            f"{stats.raw_observations} raw observations across "
            f"{stats.windows} windows; content-hash dedup dropped zero. "
            f"If Reframe A is disabled, paraphrase duplicates may be "
            f"passing through to synthesis."
        ],
        kind="open_question",
        importance=0.55,
        proposed_action=(
            "Enable REFRAME_A_ENABLED to prevent in-session re-extraction. "
            "If already enabled, zero drops indicate genuinely non-overlapping windows."
        ),
        evidence=stats.to_dict(),
        rule_id="dedup_inert",
    )


@_rule("low_obs_yield_per_call")
def _rule_low_obs_yield_per_call(stats: ExtractionRunStats) -> SelfObservation | None:
    """Raw observation yield per LLM call is too low — extraction is inefficient.

    Fires when obs_per_cost_call < 2.0 AND cost_calls >= 8 (enough sample size
    to be meaningful; fewer calls may reflect a legitimately sparse session).
    """
    if stats.cost_calls < 8:
        return None
    if stats.obs_per_cost_call >= 2.0:
        return None
    return SelfObservation(
        facts=[
            f"Session {stats.session_id[:12]} made {stats.cost_calls} LLM calls "
            f"and produced {stats.raw_observations} raw observations "
            f"({stats.obs_per_cost_call:.2f} obs/call); "
            f"yield below the 2.0 threshold signals extraction spend without signal return."
        ],
        kind="finding",
        importance=0.65,
        proposed_action=(
            "Reduce max_windows or enable affect_pre_filter to improve raw "
            "observation yield per LLM call."
        ),
        evidence=stats.to_dict(),
        rule_id="low_obs_yield_per_call",
    )


@_rule("repeated_facts_high")
def _rule_repeated_facts_high(stats: ExtractionRunStats) -> SelfObservation | None:
    """Cross-session re-extraction detected via content_hash collision.

    Caller populates stats.repeated_fact_hashes with MD5 hashes of raw
    observation facts. This rule checks how many of those hashes already
    exist in the memories DB — collisions indicate facts being re-extracted
    across sessions without deduplication.
    """
    if not stats.repeated_fact_hashes:
        return None
    collision_count: int | None = None
    try:
        from core.models import Memory, db
        if not db.is_connection_usable():
            return None
        existing = (
            Memory.select()
            .where(Memory.content_hash.in_(stats.repeated_fact_hashes))
            .count()
        )
        collision_count = existing
    except Exception:
        return None
    if collision_count is None or collision_count < 3:
        return None
    return SelfObservation(
        facts=[
            f"Session {stats.session_id[:12]} produced {collision_count} raw "
            f"observations whose content_hash already exists in the memory DB; "
            f"facts are being re-extracted across sessions without deduplication."
        ],
        kind="finding",
        importance=0.7,
        proposed_action=(
            "Cross-session re-extraction detected; consider deduplication at "
            "consolidation time or raising importance_gate."
        ),
        evidence=stats.to_dict(),
        rule_id="repeated_facts_high",
    )


@_rule("confirmed_rule_no_action")
def _rule_confirmed_rule_no_action(stats: ExtractionRunStats) -> SelfObservation | None:
    """A self-reflection rule has fired many times but has no override wired.

    Checks aggregate_audit() for rules that have fired >= 5 times, have a
    proposed_action, but are NOT in RULE_OVERRIDES. These are confirmed
    observations with no automated response — dead letter rules.
    """
    try:
        audit = aggregate_audit(root=None)
    except Exception:
        return None
    qualifying: list[str] = []
    for rule_id, slot in audit.items():
        if slot.get("fire_count", 0) < 5:
            continue
        proposed = (slot.get("latest") or {}).get("proposed_action") or ""
        if not proposed:
            continue
        if rule_id in RULE_OVERRIDES:
            continue
        qualifying.append(rule_id)
    if not qualifying:
        return None
    facts = [
        f"Rule '{rid}' has fired >= 5 times with a proposed_action but has no "
        f"entry in RULE_OVERRIDES — its recommendation is never automatically applied."
        for rid in qualifying
    ]
    # Use the first qualifying rule_id in the proposed_action message
    first_rid = qualifying[0]
    return SelfObservation(
        facts=facts,
        kind="open_question",
        importance=0.75,
        proposed_action=(
            f"Wire a parameter override for {first_rid} in "
            f"core/rule_registry.py RULE_OVERRIDES."
        ),
        evidence=stats.to_dict(),
        rule_id="confirmed_rule_no_action",
    )


@_rule("synthesis_overgreedy")
def _rule_synthesis_overgreedy(stats: ExtractionRunStats) -> SelfObservation | None:
    """Issue-card synthesis clustered every observation — no orphans left.

    Added 2026-04-28 (Phase E audit). 4 of 5 audited sessions ended with
    orphan_count=0 despite 20-50 raw observations spanning multiple
    sub-topics. Real session diversity should produce some unclustered
    findings; zero orphans suggests synthesis is forcing weak observations
    into cards rather than discarding or floating them.
    """
    if stats.raw_observations < 20:
        return None
    if stats.orphans > 0:
        return None
    if stats.issue_cards == 0:
        return None
    return SelfObservation(
        facts=[
            f"Issue-card synthesis on session {stats.session_id[:12]} "
            f"clustered all {stats.raw_observations} raw observations into "
            f"{stats.issue_cards} cards with zero orphans. Genuine session "
            f"diversity should leave some observations unclustered; zero "
            f"orphans suggests the synthesis prompt is forcing weak "
            f"observations into cards instead of dropping them."
        ],
        kind="open_question",
        importance=0.6,
        proposed_action=(
            "Inspect the lowest-importance card on this session — if its "
            "evidence is thin, tighten ISSUE_SYNTHESIS_PROMPT to drop or "
            "orphan observations that don't share at least one entity with "
            "an existing cluster."
        ),
        evidence=stats.to_dict(),
        rule_id="synthesis_overgreedy",
    )


@_rule("monotone_knowledge_lens")
def _rule_monotone_knowledge_lens(stats: ExtractionRunStats) -> SelfObservation | None:
    """Extractor emitted observations of only one knowledge_type across ≥5 final obs.

    A single knowledge_type across a substantial session usually means the lens
    is too narrow — either the prompt template constrains the classifier or the
    session is genuinely monothematic. The rule flags the pattern so it can be
    verified before accepting the extraction.
    """
    if stats.unique_knowledge_types_emitted != 1:
        return None
    if stats.final_observations < 5:
        return None
    return SelfObservation(
        facts=[
            f"Session {stats.session_id[:12]} produced {stats.final_observations} "
            f"observations all classified under a single knowledge_type; "
            f"a monotone lens may indicate prompt-driven classifier collapse "
            f"rather than genuine session homogeneity."
        ],
        kind="finding",
        importance=0.6,
        proposed_action=(
            "Extractor running with monotone lens; verify session content "
            "actually monothematic before accepting."
        ),
        evidence=stats.to_dict(),
        rule_id="monotone_knowledge_lens",
    )


@_rule("affect_signal_no_extraction")
def _rule_affect_signal_no_extraction(stats: ExtractionRunStats) -> SelfObservation | None:
    """Somatic detector fires on windows where LLM extracts nothing.

    When affect signals accumulate across windows but the LLM produces no
    observation cards for those same windows, either the prompt is under-
    specifying how to handle affect-flagged content or the detector has a
    false-positive rate worth investigating.
    """
    if stats.windows_with_affect_signal_but_no_card < 3:
        return None
    return SelfObservation(
        facts=[
            f"Session {stats.session_id[:12]} had "
            f"{stats.windows_with_affect_signal_but_no_card} windows where the "
            f"somatic affect detector fired but the LLM produced no observation "
            f"cards; signal is being detected but not converted to memory."
        ],
        kind="finding",
        importance=0.7,
        proposed_action=(
            "Somatic detector fires on windows where LLM extracts nothing — "
            "investigate prompt clarity or detector false positives."
        ),
        evidence=stats.to_dict(),
        rule_id="affect_signal_no_extraction",
    )


@_rule("forced_clustering_low_importance")
def _rule_forced_clustering_low_importance(stats: ExtractionRunStats) -> SelfObservation | None:
    """Synthesis is force-clustering low-importance observations.

    Co-condition: synthesis_overgreedy must be confirmed (≥3 fires in audit)
    AND the minimum card importance on this run is below 0.4. Together they
    indicate the synthesis prompt is pulling weak observations into cards
    rather than orphaning or dropping them.
    """
    try:
        audit = aggregate_audit(root=None)
    except Exception:
        return None
    if (audit.get("synthesis_overgreedy") or {}).get("confidence") != "confirmed":
        return None
    if stats.min_card_importance >= 0.4:
        return None
    return SelfObservation(
        facts=[
            f"Session {stats.session_id[:12]} has min_card_importance="
            f"{stats.min_card_importance:.2f} and synthesis_overgreedy is "
            f"confirmed across prior runs; synthesis is likely force-clustering "
            f"low-importance observations into cards rather than leaving orphans."
        ],
        kind="finding",
        importance=0.7,
        proposed_action=(
            "Synthesis is force-clustering low-importance observations; "
            "tighten orphan threshold or enable synthesis_strict."
        ),
        evidence=stats.to_dict(),
        rule_id="forced_clustering_low_importance",
    )


@_rule("cards_unused_high_importance")
def _rule_cards_unused_high_importance(stats: ExtractionRunStats) -> SelfObservation | None:
    """Fires when ≥3 high-importance (>=0.8) cards from a session are never
    retrieved within 10 subsequent sessions. Catches false-positive
    high-importance scoring at synthesis time.
    """
    if not stats.session_id:
        return None
    try:
        from core.feedback import cards_unused_in_subsequent_sessions
        unused = cards_unused_in_subsequent_sessions(stats.session_id)
    except Exception:
        return None
    if len(unused) < 3:
        return None
    return SelfObservation(
        rule_id="cards_unused_high_importance",
        importance=0.7,
        kind="finding",
        facts=[
            f"{len(unused)} high-importance memories from session {stats.session_id} "
            f"never retrieved in next 10 sessions"
        ],
        proposed_action=(
            "False-positive high-importance scoring detected at synthesis time. "
            "Tighten card importance threshold or refine synthesis prompt's "
            "importance calibration."
        ),
        evidence=stats.to_dict(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reflect_on_extraction(
    stats: ExtractionRunStats,
    *,
    root: Path | None = None,
) -> list[SelfObservation]:
    """Run all rules over a single session's run stats.

    Returns the list of fired self-observations (may be empty). Side
    effects: appends every observation to the audit JSONL and refreshes
    the active self_model.md file.
    """
    obs: list[SelfObservation] = []
    now = datetime.now(timezone.utc).isoformat()
    for rule in _RULES:
        try:
            result = rule(stats)
        except Exception as exc:
            logger.warning(
                "self-reflection rule %s raised: %s",
                getattr(rule, "__rule_id__", rule.__name__),
                exc,
            )
            continue
        if result is not None:
            result.ts = now
            obs.append(result)

    if obs:
        _append_audit(obs, root=root)
        _refresh_self_model_doc(obs, stats, root=root)

    return obs


def load_self_model(root: Path | None = None) -> str:
    """Return the current self_model.md text (empty if not yet written).

    The next extraction can pass this into prompts as additional context so
    the model can avoid known failure modes.
    """
    p = self_model_path(root)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _append_audit(obs: list[SelfObservation], root: Path | None = None) -> None:
    p = self_model_audit_path(root)
    try:
        with p.open("a", encoding="utf-8") as fh:
            for o in obs:
                fh.write(json.dumps(o.to_dict()) + "\n")
    except OSError as exc:
        logger.warning("self-reflection: failed to write audit: %s", exc)


def stamp_confirmed_observations(root: Path | None = None) -> int:
    """Back-propagate confirmed status to JSONL entries.

    Reads self_observations.jsonl, determines which rule_ids have fire_count >= 3
    (confirmed threshold), rewrites the file atomically with confirmed=True on
    those entries. Returns the number of entries updated.

    Called after each sweep run so `confirmed` in the JSONL stays consistent
    with what `aggregate_audit()` computes dynamically.
    """
    import tempfile, shutil
    audit_p = self_model_audit_path(root)
    if not audit_p.exists():
        return 0

    # First pass: count fires per rule_id
    fire_counts: dict[str, int] = {}
    try:
        lines = audit_p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        try:
            rec = json.loads(line)
            rid = rec.get("rule_id", "unknown")
            fire_counts[rid] = fire_counts.get(rid, 0) + 1
        except json.JSONDecodeError:
            pass

    confirmed_rules = {rid for rid, n in fire_counts.items() if n >= 3}
    if not confirmed_rules:
        return 0

    # Second pass: rewrite with confirmed=True for confirmed rules
    updated = 0
    new_lines: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if rec.get("rule_id") in confirmed_rules and not rec.get("confirmed"):
                rec["confirmed"] = True
                updated += 1
            new_lines.append(json.dumps(rec))
        except json.JSONDecodeError:
            new_lines.append(line)

    if updated == 0:
        return 0

    try:
        fd, tmp = tempfile.mkstemp(dir=audit_p.parent, suffix=".jsonl.tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines) + "\n")
        shutil.move(tmp, audit_p)
    except OSError as exc:
        logger.warning("stamp_confirmed_observations: write failed: %s", exc)
        return 0

    return updated


def _refresh_self_model_doc(
    new_obs: list[SelfObservation],
    stats: ExtractionRunStats,
    root: Path | None = None,
) -> None:
    """Rewrite self_model.md with the most recent ruleset findings.

    Aggregates the last N audit entries by rule_id, keeps the single most
    recent per rule (rules tend to be deterministic — the latest evidence
    is the most relevant), and writes a markdown doc.
    """
    audit_p = self_model_audit_path(root)
    by_rule: dict[str, dict] = {}
    if audit_p.exists():
        try:
            with audit_p.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rid = rec.get("rule_id") or "unknown"
                    by_rule[rid] = rec  # last-wins
        except OSError:
            pass

    for o in new_obs:
        by_rule[o.rule_id] = o.to_dict()

    md_lines: list[str] = [
        "# Self-Model — Extraction-Process Metacognition",
        "",
        f"_Last refreshed: {datetime.now(timezone.utc).isoformat()}_",
        "",
        f"_Latest run: session={stats.session_id[:12]}, chunking={stats.chunking}, "
        f"windows={stats.windows}, productive={stats.productive_windows}, "
        f"final_obs={stats.final_observations}_",
        "",
        "This file is read by the next extraction as context. Each entry is a "
        "production rule the system learned about its own behavior; the "
        "**Action** line is the recommended adjustment.",
        "",
        "---",
        "",
    ]

    for rid in sorted(by_rule.keys()):
        rec = by_rule[rid]
        md_lines.append(f"## {rid}")
        md_lines.append("")
        md_lines.append(f"_kind={rec.get('kind')}, importance={rec.get('importance')}, "
                         f"ts={rec.get('ts','?')}_")
        md_lines.append("")
        for f in rec.get("facts", []):
            md_lines.append(f"- {f}")
        action = rec.get("proposed_action")
        if action:
            md_lines.append("")
            md_lines.append(f"**Action:** {action}")
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    p = self_model_path(root)
    try:
        p.write_text("\n".join(md_lines), encoding="utf-8")
    except OSError as exc:
        logger.warning("self-reflection: failed to write self_model.md: %s", exc)


def list_rules() -> list[str]:
    """Inventory of registered rule_ids — useful for tests / debugging."""
    return [getattr(r, "__rule_id__", r.__name__) for r in _RULES]


# ---------------------------------------------------------------------------
# Cross-run aggregation + feedback into next extraction
# ---------------------------------------------------------------------------


def aggregate_audit(root: Path | None = None) -> dict[str, dict]:
    """Aggregate every prior self-observation by rule_id.

    Returns dict keyed by rule_id with:
      {
        "fire_count": int,
        "first_seen": ts,
        "last_seen": ts,
        "confidence": "tentative" | "confirmed",
        "latest": <full SelfObservation dict>
      }

    Confirmed = rule fired ≥3 times across runs (Anderson 1983 production-rule
    compilation: a rule becomes procedural only after repeated successful firing).
    """
    audit_p = self_model_audit_path(root)
    if not audit_p.exists():
        return {}
    by_rule: dict[str, dict] = {}
    try:
        with audit_p.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = rec.get("rule_id") or "unknown"
                slot = by_rule.setdefault(rid, {
                    "fire_count": 0,
                    "first_seen": rec.get("ts"),
                    "last_seen": rec.get("ts"),
                    "latest": rec,
                })
                slot["fire_count"] += 1
                ts = rec.get("ts")
                if ts:
                    if not slot["first_seen"] or ts < slot["first_seen"]:
                        slot["first_seen"] = ts
                    if not slot["last_seen"] or ts > slot["last_seen"]:
                        slot["last_seen"] = ts
                slot["latest"] = rec
    except OSError:
        return {}
    for rid, slot in by_rule.items():
        slot["confidence"] = "confirmed" if slot["fire_count"] >= 3 else "tentative"
    return by_rule


def select_chunking(
    nontrivial_user_turn_count: int,
    entry_count: int,
    *,
    root: Path | None = None,
) -> str:
    """Apply rule chunking_suboptimal automatically.

    This is the "feedback into next extraction" channel — instead of writing
    the recommendation to a doc and hoping a human reads it, the system
    consults the audit log and acts on confirmed rules directly.

    Returns "stride" or "user_anchored". Default is "user_anchored" because
    most Claude Code sessions are direction-driven; we only override when
    there's a confirmed rule and the session shape matches its trigger.
    """
    by_rule = aggregate_audit(root)
    rule = by_rule.get("chunking_suboptimal")
    # Note: was "chunking_mismatch_user_anchored_low_turns" pre-Phase-E rename;
    # leaving anchor here so future renames don't silently break the lookup.
    # Threshold from rule definition: <5 substantive user turns OR
    # entries-per-user-turn ratio > 30 → agent-driven, prefer stride
    user_to_entry_ratio = (
        nontrivial_user_turn_count / max(entry_count, 1)
        if entry_count > 0 else 0
    )
    is_agent_driven = (
        nontrivial_user_turn_count < 5
        or (entry_count > 50 and user_to_entry_ratio < 0.03)
    )
    if rule and rule.get("confidence") == "confirmed" and is_agent_driven:
        return "stride"
    if not rule and is_agent_driven:
        # No confirmed evidence yet, but heuristic still applies
        return "stride"
    return "user_anchored"


# ---------------------------------------------------------------------------
# Cross-session meta-rule: recurrent_agent_failure (Option A)
# ---------------------------------------------------------------------------

# Keywords triggering the cross-session failure cluster signal.
_RECURRENT_FAILURE_KEYWORDS: frozenset[str] = frozenset({
    "without reading",
    "spec drift",
    "api mismatch",
    "api drift",
    "without verifying",
    "did not check",
    "did not read",
    "wrong api",
    "outdated spec",
})


@dataclass
class CorrectionCard:
    """Minimal representation of a correction-kind card for cross-session analysis."""

    card_id: str
    session_id: str
    problem: str
    decision_or_outcome: str


def reflect_on_corpus(
    cards: list[CorrectionCard],
    *,
    root: Path | None = None,
) -> list[SelfObservation]:
    """Run the recurrent_agent_failure cross-session meta-rule over a corpus of cards.

    Called by the sweep runner after all sessions are processed. Returns fired
    self-observations and appends them to the audit log (same path as per-session
    rules). Empty list when no cluster meets the threshold.

    Fire condition: >=2 correction-kind cards across >=2 distinct session_ids
    share >=1 keyword from _RECURRENT_FAILURE_KEYWORDS (case-insensitive substring
    match against problem + decision_or_outcome concatenated).
    """
    # Group cards by keyword: keyword → list of (session_id, card_id, excerpt)
    keyword_hits: dict[str, list[dict[str, str]]] = {}
    for card in cards:
        # Combine both text fields for matching — one place to search
        haystack = (card.problem + " " + card.decision_or_outcome).lower()
        for kw in _RECURRENT_FAILURE_KEYWORDS:
            if kw in haystack:
                keyword_hits.setdefault(kw, []).append({
                    "session_id": card.session_id,
                    "card_id": card.card_id,
                    "card_problem_excerpt": card.problem[:120],
                })

    obs: list[SelfObservation] = []
    now = datetime.now(timezone.utc).isoformat()
    for kw, hits in keyword_hits.items():
        # Require >=2 hits across >=2 distinct sessions
        distinct_sessions = {h["session_id"] for h in hits}
        if len(hits) < 2 or len(distinct_sessions) < 2:
            continue
        # Cap evidence list at 3 entries to keep the record compact
        evidence_entries = hits[:3]
        total = len(hits)
        card_ids = [h["card_id"] for h in hits]
        observation = SelfObservation(
            facts=[
                f"Recurrent agent failure pattern detected: keyword '{kw}' matched "
                f"{total} correction card(s) across {len(distinct_sessions)} distinct "
                f"session(s) ({', '.join(sorted(distinct_sessions)[:3])}). "
                f"Repeated cross-session corrections sharing the same root cause "
                f"indicate a systemic failure mode, not a one-off mistake."
            ],
            kind="correction",
            importance=0.9,
            proposed_action=(
                f"Surface to Stage 1 prompt: warn extractor that prior sessions showed "
                f"pattern '{kw}' across cards {card_ids}. Consider tightening "
                f"pre-extraction read-source verification."
            ),
            evidence={
                "keyword_matched": kw,
                "card_count": total,
                "sessions": list(sorted(distinct_sessions)),
                "entries": evidence_entries,
            },
            rule_id="recurrent_agent_failure",
        )
        observation.ts = now
        obs.append(observation)

    if obs:
        _append_audit(obs, root=root)

    return obs


def build_self_model_preamble(root: Path | None = None) -> str:
    """Produce a short preamble that can be injected into the extraction prompt.

    Reads only CONFIRMED rules (≥3 fires) so we don't pollute the prompt
    with one-off observations. Output is a compact bullet list of action
    recommendations. Empty string if no confirmed rules — caller can skip.
    """
    by_rule = aggregate_audit(root)
    confirmed = {
        rid: slot for rid, slot in by_rule.items()
        if slot.get("confidence") == "confirmed"
    }
    if not confirmed:
        return ""
    lines = [
        "## Self-model (confirmed extraction-process rules from prior runs)",
        "",
        "These are calibrations the system has learned from prior extractions. "
        "Apply when relevant, ignore otherwise — they are heuristics, not gospel.",
        "",
    ]
    for rid in sorted(confirmed.keys()):
        slot = confirmed[rid]
        rec = slot["latest"]
        action = rec.get("proposed_action") or "(no action recorded)"
        fires = slot["fire_count"]
        lines.append(f"- **{rid}** (fired {fires}x): {action}")
    lines.append("")
    return "\n".join(lines)
