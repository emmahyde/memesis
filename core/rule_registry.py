"""
Confirmed-rule → parameter override registry.

Generalizes the `select_chunking()` pattern in `self_reflection_extraction.py`:
when a self-reflection rule reaches confirmed status (≥3 fires), its
proposed_action becomes an actual parameter override applied to the next
extraction run, not just a diary entry.

Today only `select_chunking()` mechanically acted on a confirmed rule. Every
other rule (`affect_blind_spot`, `dedup_inert`, `synthesis_overgreedy`,
`low_productive_rate`, `parse_errors_present`) accumulated fires forever
with no behavioral consequence. This module wires those rules to actual
knobs in `extract_observations_hierarchical()` and Stage 1 LLM calls.

Design notes:
  - Each override is a pure function: (rule_record, base) -> ParameterOverrides.
    Composable — multiple confirmed rules layer onto each other deterministically
    (later rules in `RULE_OVERRIDES` win on field conflicts).
  - Defaults in `ParameterOverrides` mirror current hardcoded values so
    `resolve_overrides({})` is a no-op equivalent to today's behavior.
  - `dedup_inert` is a stub. The current dedup uses MD5 exact-hash, not
    Jaccard — `proposed_action` references a future Jaccard/embedding pass
    that doesn't exist in code yet. The override is wired but inert until
    a similarity-based dedup lands.
  - The dead-key class of bug (`select_chunking()` referenced the old
    rule_id "chunking_mismatch_user_anchored_low_turns" after a rename) is
    avoided here by keeping all rule_ids in one `RULE_OVERRIDES` dict —
    grep-able from a single source.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class ParameterOverrides:
    """All extraction-side knobs a confirmed rule might tune.

    Defaults match the hardcoded values in `transcript_ingest.py` and
    `core/llm.py` as of 2026-04-28.
    """

    importance_gate: float = 0.3
    max_windows: int = 10
    window_chars: int = 16000
    stride_chars: int = 12800
    max_tokens_stage1: int = 8192
    chunking_strategy: str | None = None  # "stride" | "user_anchored" | None=auto
    affect_pre_filter: bool = False  # gate Stage 1 LLM on max_boost > 0
    synthesis_strict: bool = False  # tighter ISSUE_SYNTHESIS_PROMPT (orphan weak obs)
    prefilter_research_neutral: bool = True  # research-session neutral-affect window skip
    prefilter_density_threshold_chars: int = 200  # avg chars/entry below = low-density
    prefilter_ttr_threshold: float = 0.25  # type/token ratio below = self-referential
    prefilter_observer_density_threshold_chars: int = 400  # observer-cwd density gate
    notes: tuple[str, ...] = ()  # human-readable trace of which rules fired

    def with_note(self, note: str) -> ParameterOverrides:
        return replace(self, notes=self.notes + (note,))


# Override functions ---------------------------------------------------------


def _override_chunking(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`chunking_suboptimal` confirmed → set chunking_strategy.

    Rule fires bidirectionally (Arm A: stride needed; Arm B: user_anchored
    needed). The proposed_action text encodes which arm — we let the caller
    inspect features to decide. Here we only mark that auto-selection should
    consult the rule. `select_chunking()` is the existing chooser; this just
    surfaces "consult it" rather than letting it default.
    """
    return base.with_note(f"chunking_suboptimal confirmed ({rec.get('fire_count', '?')} fires)")


def _override_low_productive_rate(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`low_productive_rate` confirmed → reduce max_windows, enable affect pre-filter.

    Both knobs reduce LLM spend on low-yield windows. Cap reduction at 6
    (from default 10) to avoid starving long sessions entirely.
    """
    return replace(
        base,
        max_windows=min(base.max_windows, 6),
        affect_pre_filter=True,
        notes=base.notes + (f"low_productive_rate confirmed → max_windows=6, affect_pre_filter=True",),
    )


def _override_affect_blind_spot(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`affect_blind_spot` confirmed → currently informational.

    The actual remediation (`_merge_card_affect` overlay) was implemented
    inline at `transcript_ingest.py:495` and runs unconditionally — there's
    no parameter to flip. Keeping the override fn so the rule doesn't appear
    unwired and so future tuning has a hook.
    """
    return base.with_note("affect_blind_spot confirmed → _merge_card_affect already active")


def _override_dedup_inert(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`dedup_inert` confirmed → STUB.

    Proposed action recommends Jaccard 0.55 or embedding cosine. Current
    `_dedupe_observations` uses MD5 exact-hash (`transcript_ingest.py:295`),
    so there's no threshold to lower. This override is a placeholder for
    when a similarity pass is reintroduced.
    """
    return base.with_note("dedup_inert confirmed → STUB (no Jaccard knob in current code)")


def _override_synthesis_overgreedy(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`synthesis_overgreedy` confirmed → set synthesis_strict flag.

    Caller (`synthesize_issue_cards`) reads this flag to tighten the
    ISSUE_SYNTHESIS_PROMPT toward orphaning weak obs rather than forcing
    them into clusters. Wiring on the synthesis side is out of scope for
    this change — flag set here, prompt branch added in a follow-up.
    """
    return replace(
        base,
        synthesis_strict=True,
        notes=base.notes + ("synthesis_overgreedy confirmed → synthesis_strict=True",),
    )


def _override_parse_errors(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`parse_errors_present` confirmed → bump Stage 1 max_tokens.

    Truncated arrays from a too-low token cap can show up as parse errors
    after the repair path fails. Bumping ceiling reduces those at small
    cost.
    """
    return replace(
        base,
        max_tokens_stage1=max(base.max_tokens_stage1, 12288),
        notes=base.notes + ("parse_errors_present confirmed → max_tokens_stage1=12288",),
    )


def _override_cards_unused_high_importance(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`cards_unused_high_importance` (meta-rule from FeedbackLoop bridge).

    Cards marked high-importance never get retrieved — extraction is
    over-confident on importance. Tighten the importance gate to filter
    weak signal earlier.
    """
    return replace(
        base,
        importance_gate=max(base.importance_gate, 0.45),
        notes=base.notes + ("cards_unused_high_importance confirmed → importance_gate=0.45",),
    )


def _override_monotone_knowledge_lens(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`monotone_knowledge_lens` confirmed → informational stub.

    When confirmed, extraction is repeatedly collapsing all observations into
    a single knowledge_type, indicating a classifier collapse or genuinely
    monothematic sessions. No direct knob yet — the parameter
    `knowledge_type_diversity_floor` would gate synthesis to require ≥2 distinct
    types, but that field doesn't exist in the synthesis prompt currently.

    This stub documents the intent and surface the confirmation in audit
    output so the pattern isn't invisible.
    """
    return base.with_note(
        "monotone_knowledge_lens confirmed → informational (no diversity-floor knob yet)"
    )


def _override_affect_signal_no_extraction(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`affect_signal_no_extraction` confirmed → informational stub.

    When confirmed, windows with somatic affect signals are consistently
    failing to produce observation cards. Ideally this would flip an
    `affect_signal_audit_required` flag causing the pipeline to log
    those window pairs for prompt-debugging, but that logging path
    doesn't exist yet.

    This stub records the confirmation in audit output and notes the
    intended future parameter.
    """
    return base.with_note(
        "affect_signal_no_extraction confirmed → informational (affect_signal_audit_required not yet wired)"
    )


def _override_forced_clustering_low_importance(rec: dict, base: ParameterOverrides) -> ParameterOverrides:
    """`forced_clustering_low_importance` confirmed → raise synthesis_strict_floor.

    Co-condition: synthesis_overgreedy already confirmed (that rule's override
    sets synthesis_strict=True). This rule fires when the minimum card importance
    is also below 0.4 on top of overgreedy clustering.

    The intended knob is `synthesis_strict_floor` — a minimum importance a card
    must meet to survive strict-mode synthesis. No such field exists in
    ParameterOverrides today; adding synthesis_strict=True here ensures the strict
    path is at least engaged, even if the floor isn't numerically enforced yet.
    """
    return replace(
        base,
        synthesis_strict=True,
        notes=base.notes + (
            "forced_clustering_low_importance confirmed → synthesis_strict=True "
            "(synthesis_strict_floor=0.4 pending prompt wiring)",
        ),
    )


# Registry: rule_id -> override function -------------------------------------

RULE_OVERRIDES: dict[str, Callable[[dict, ParameterOverrides], ParameterOverrides]] = {
    "chunking_suboptimal": _override_chunking,
    "low_productive_rate": _override_low_productive_rate,
    "affect_blind_spot": _override_affect_blind_spot,
    "dedup_inert": _override_dedup_inert,
    "synthesis_overgreedy": _override_synthesis_overgreedy,
    "parse_errors_present": _override_parse_errors,
    "cards_unused_high_importance": _override_cards_unused_high_importance,
    "monotone_knowledge_lens": _override_monotone_knowledge_lens,
    "affect_signal_no_extraction": _override_affect_signal_no_extraction,
    "forced_clustering_low_importance": _override_forced_clustering_low_importance,
}


# Metadata for registry_status CLI — describes knobs each rule touches -------

RULE_METADATA: dict[str, dict] = {
    "chunking_suboptimal": {
        "knobs": ["chunking_strategy"],
        "note": "Sets chunking_strategy via select_chunking() bidirectional logic.",
    },
    "low_productive_rate": {
        "knobs": ["max_windows", "affect_pre_filter"],
        "note": "Caps max_windows=6; enables affect pre-filter to reduce LLM spend.",
    },
    "affect_blind_spot": {
        "knobs": [],
        "note": "Informational; _merge_card_affect overlay runs unconditionally.",
    },
    "dedup_inert": {
        "knobs": [],
        "note": "STUB — no Jaccard/cosine knob yet; MD5 exact-hash only.",
    },
    "synthesis_overgreedy": {
        "knobs": ["synthesis_strict"],
        "note": "Sets synthesis_strict=True; tightens orphan pressure in synthesis.",
    },
    "parse_errors_present": {
        "knobs": ["max_tokens_stage1"],
        "note": "Bumps max_tokens_stage1 to 12288 to reduce truncation parse errors.",
    },
    "cards_unused_high_importance": {
        "knobs": ["importance_gate"],
        "note": "Raises importance_gate to 0.45 when high-imp cards go unused.",
    },
    "monotone_knowledge_lens": {
        "knobs": [],
        "note": "STUB — knowledge_type_diversity_floor not yet wired in synthesis.",
    },
    "affect_signal_no_extraction": {
        "knobs": [],
        "note": "STUB — affect_signal_audit_required logging path not yet wired.",
    },
    "forced_clustering_low_importance": {
        "knobs": ["synthesis_strict"],
        "note": "Sets synthesis_strict=True; synthesis_strict_floor=0.4 pending prompt wiring.",
    },
}


def resolve_overrides(
    audit: dict[str, dict],
    *,
    base: ParameterOverrides | None = None,
) -> ParameterOverrides:
    """Walk the audit; for each confirmed rule, apply its override.

    Order is determined by `RULE_OVERRIDES` insertion order — later
    overrides win on field conflicts. Caller can pass a non-default `base`
    to start from a tuned baseline (e.g., session-type defaults).

    Args:
        audit: Output of `aggregate_audit()` — dict[rule_id, slot] where
               slot has 'confidence' and 'latest' keys.
        base: Starting overrides. Defaults to factory defaults.

    Returns:
        Composed ParameterOverrides reflecting all confirmed rules.
    """
    out = base or ParameterOverrides()
    for rule_id, override_fn in RULE_OVERRIDES.items():
        slot = audit.get(rule_id)
        if not slot:
            continue
        if slot.get("confidence") != "confirmed":
            continue
        try:
            out = override_fn(slot, out)
        except Exception as exc:  # noqa: BLE001
            # Override functions must never block extraction. Record and skip.
            out = out.with_note(f"override {rule_id} raised: {exc}")
    return out


def resolve_overrides_from_root(
    root: Path | None = None,
    *,
    base: ParameterOverrides | None = None,
) -> ParameterOverrides:
    """Convenience: aggregate audit and resolve in one call."""
    from core.self_reflection_extraction import aggregate_audit

    return resolve_overrides(aggregate_audit(root), base=base)
