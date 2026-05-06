"""
Stage 1.5 — Issue-card synthesis over hierarchically-extracted observations.

Replaces the Wu 2021 "fixup" refine pass with a structurally richer output:
flat observations get reorganized into issue cards with problem framing,
options considered, the decision/outcome, and the user's affective reaction.

Theoretical basis:
- Conway 2001 ("Sensory-Perceptual Episodic Memory and Its Context",
  Phil. Trans. R. Soc. Lond. B 356:1375-1384) — autobiographical memory is
  hierarchical: event-specific knowledge → general events → lifetime
  periods. Issue cards correspond to general events; their evidence_facts[]
  retain pointers to event-specific knowledge.
- Wu et al. 2021 (arXiv 2109.10862) — hierarchical recursive summarization
  beats flat extraction for long-document recall.
- Schank 1982 (Dynamic Memory) — memory organization packets (MOPs):
  thematic units that bind related episodes around a problem and resolution.

Why issue cards over flat observations:
1. Compression: 16 flat obs collapse into ~3-5 cards covering the same
   ground but with cross-cuts (problem→option→decision→reaction).
2. Retrievability: a card's title is a natural query target ("how did Emma
   decide on the HUD port strategy?") in a way that flat fact lists are
   not.
3. Affect attachment: user_reaction/valence is captured at the card level,
   not lost in fact-level dilution.

Schema is intentionally LLM-friendly: short string fields, evidence_facts
as a list of verbatim quotes from the input observations, no nested
structures the LLM has to invent.
"""

from __future__ import annotations

import json
import logging
import re

from core.llm import call_llm

logger = logging.getLogger(__name__)


ISSUE_SYNTHESIS_PROMPT = """You are reorganizing flat observations from a Claude Code session into ISSUE CARDS.

Each card frames a problem-and-resolution unit: what was at stake, the
options considered, the outcome, and how the user felt about it.

DO NOT invent observations. Every evidence_quote MUST be a verbatim substring
of an input observations[].facts[] entry. No paraphrase, no rewording, no
granularity shift. Copy the exact text.

INPUTS:

session_synopsis (~6KB): {synopsis}

session_affect_summary: {affect_summary}

observations (JSON list, possibly redundant — overlapping windows):
{observations_json}

---

OUTPUT a single JSON object:

{{
  "issue_cards": [
    {{
      "title": "≤8 words, names the issue",
      "problem": "1-2 sentences — what was at stake",
      "options_considered": ["option1", "option2", ...],   // empty list if none
      "decision_or_outcome": "1-2 sentences — what was decided/found/changed",
      "user_reaction": "short phrase — the user's affective response (e.g. 'rejected with frustration', 'enthusiastic accept', 'silent acquiescence', 'unresolved')",
      "user_affect_valence": "friction|delight|surprise|neutral|mixed",  // Use 'mixed' when the user's reaction evolved across the card's span (e.g., initial friction then accept). Track the trajectory in 'user_reaction' text.
      "evidence_quotes": ["verbatim quote from input observations[].facts[]", ...],
      "evidence_obs_indices": [0, 3, 7],
      "kind": "decision|finding|preference|constraint|correction|open_question",
      "knowledge_type": "factual|conceptual|procedural|metacognitive",
      "importance": 0.0,
      "scope": "session-local | cross-session-durable",
      // decision-kind only — both fields are OPTIONAL; omit for non-decision cards
      "criterion_weights": {{
        "<criterion>": "hard_veto|strong|weak|mentioned"
      }},
      "rejected_options": [
        {{"option": "<name>", "reason": "<why rejected, or 'rejected without recorded reason'>"}}
      ]
    }}
  ],
  "orphans": [
    /* observations that don't fit any issue card — keep them as-is in original schema */
  ],
  "synthesis_notes": "1-2 sentences on what surprised you or what you couldn't classify"
}}

QUALITY RULES:

1. A card MUST have ≥1 evidence_quote. No card without evidence.
2. Cards SHOULD aggregate related observations: don't make a 1-obs card
   unless the observation truly stands alone.
3. Importance: take the max() of source observations' importance.
   (Kensinger +0.05 friction bump is applied at persistence in consolidator.py,
   not here — do not pre-apply it.)
4. scope = "cross-session-durable" only if the issue would still matter
   in a session three weeks from now. Otherwise "session-local".
5. orphans[] retains observations in their ORIGINAL schema (kind,
   knowledge_type, knowledge_type_confidence, importance, facts, cwd).
   Do not reformat them.
Orphaning is a quality gate, not a fallback. Prefer emitting zero cards to forcing a cluster — a weak observation that fits no card MUST become an orphan, not be force-fit.
6. ENTITY GATE: If an observation does not share at least one named entity
   (person, system, file, concept) with any other observation in the input,
   orphan it rather than forcing it into a card. Prefer zero cards to a card
   with one low-importance observation.
7. ORPHAN TARGET: Aim for ≥1 orphan per 15 input observations unless the
   session is genuinely monothematic (all observations address a single
   coherent problem). Do not force every observation into a card.
8. ZERO-ORPHAN AUDIT: If orphans[] is empty and issue_cards[] has >8 entries,
   you MUST re-examine every card. Orphan any card whose evidence_quotes are
   all substrings of that card's own problem or decision_or_outcome text —
   those are self-referential cards, not grounded evidence.
9. LOOKUP-TABLE GUARD: If a card's evidence_quotes contain ≥5 distinct named
   entities (proper nouns, type names, file paths) without a unifying decision
   or problem that applies to all of them, split into orphans rather than
   synthesize. Lookup-table content belongs as orphans, not cards.
10. DROP GATE: An observation with importance < 0.3 sharing no named entity
    with any sibling MAY be dropped entirely (omit from both issue_cards[]
    and orphans[]). Use sparingly — preserves orphan signal but reduces
    noise floor. The synthesis_notes should mention how many were dropped.

FRICTION DISCIPLINE: When multiple observations describe the same repeated
friction pattern (user retried, gave up, reduced scope), treat it as a
signal — but only emit a card if the pattern reveals a durable workflow
constraint. A single blocked moment belongs in orphans[], not a card.

---

DECISION-KIND RULES (apply when kind == "decision"):

RULE D1 — REJECTION RATIONALE (applies when options_considered has ≥2 entries):
  evidence_quotes MUST include ≥1 quote per rejected option explaining WHY it was
  rejected. If the source observations contain no recorded rationale for a rejected
  option, add a synthetic note in rejected_options[]:
    {{"option": "<name>", "reason": "<rejected without recorded reason>"}}
  Do not silently drop unchosen options — the rejection reason is often more
  durable than the chosen path.

RULE D2 — ATTRIBUTION RUBRIC:
  When writing decision_or_outcome or any attribution language, use the rubric below.
  Default to "Emma chose" when ambiguous — Emma is the decision authority.

  "Emma chose"      — source quotes contain Emma's imperative or hard veto:
                      "we must", "no", "stop", "do X", "don't X", explicit rejection
  "Emma confirmed"  — Claude proposed, Emma approved:
                      "yes", "good", "ship it", "go", "looks good", "okay"
  "Emma and Claude" — ONLY when source quotes show genuine joint exploration:
                      both parties proposing distinct variants and iterating together

  Do NOT default to "Emma and Claude" just because both names appear in the session.
  The test is whether Claude's proposals were substantively adopted or merely executed.

RULE D3 — CRITERION WEIGHTS:
  For decision cards, populate criterion_weights{{}} for each evaluation criterion
  that appears in the source observations. Use these four levels:

  "hard_veto"  — Emma stated it as non-negotiable; any option violating it was
                 automatically rejected (e.g. "test invalidation is a blocker")
  "strong"     — explicitly weighted heavily, but trade-offs were discussed
  "weak"       — mentioned as a consideration but not pivotal to the outcome
  "mentioned"  — surfaced once without weight indication

  If no criteria are discernible from the source, omit criterion_weights entirely
  (the field is optional).

EXAMPLE — fully populated decision card showing new fields:
{{
  "title": "Skill taxonomy 13 + Command + Trade",
  "problem": "Taxonomy needed to cover Command and Trade skills without invalidating existing tests.",
  "options_considered": ["13-category flat list", "13 + Command + Trade extension", "full redesign"],
  "decision_or_outcome": "Emma chose 13 + Command + Trade extension; full redesign rejected as test-invalidating.",
  "user_reaction": "hard veto on full redesign",
  "user_affect_valence": "friction",
  "evidence_quotes": [
    "Emma stated test invalidation is a blocker — any option breaking existing tests is off the table",
    "Emma rejected full redesign citing test-invalidation risk"
  ],
  "evidence_obs_indices": [2, 5],
  "kind": "decision",
  "knowledge_type": "conceptual",
  "importance": 0.85,
  "scope": "cross-session-durable",
  "criterion_weights": {{
    "test preservation": "hard_veto",
    "coverage of Command and Trade": "strong",
    "migration effort": "weak"
  }},
  "rejected_options": [
    {{"option": "full redesign", "reason": "would invalidate existing skill taxonomy tests — Emma's hard veto"}},
    {{"option": "13-category flat list", "reason": "rejected without recorded reason"}}
  ]
}}

{strict_clause}

If there are zero observations to organize, return:
{{"issue_cards": [], "orphans": [], "synthesis_notes": "no observations to synthesize"}}

Output ONLY the JSON object. No markdown fences. No commentary.
"""

# Filled into {strict_clause} when synthesis_strict=True (wired from rule_registry.synthesis_strict)
_STRICT_CLAUSE = (
    "STRICT MODE (synthesis_overgreedy confirmed): Default to orphaning when uncertain. "
    "A weak observation that fits no card clearly MUST become an orphan — never be "
    "force-fit into an existing card to avoid an empty orphans list."
)
_LENIENT_CLAUSE = ""  # no extra instruction in normal mode


def _jaccard(a: str, b: str) -> float:
    # Token-set Jaccard similarity; no new dependencies — stdlib split only
    sa = set(a.split())
    sb = set(b.split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def _dedupe_quotes(quotes: list[str]) -> tuple[list[str], int]:
    # Cluster by Jaccard ≥0.7; keep longest member of each cluster
    kept: list[str] = []
    removed = 0
    for q in quotes:
        merged = False
        for i, k in enumerate(kept):
            if _jaccard(q, k) >= 0.7:
                # Keep longer string
                if len(q) > len(k):
                    kept[i] = q
                removed += 1
                merged = True
                break
        if not merged:
            kept.append(q)
    return kept, removed


def synthesize_issue_cards(
    observations: list[dict],
    synopsis: str,
    session_affect_summary: dict | None = None,
    synthesis_strict: bool = False,
) -> tuple[list[dict], list[dict], dict]:
    """Run Stage 1.5 synthesis. Returns (issue_cards, orphans, stats).

    Falls back to (empty_cards, original_observations, error_stats) on parse
    failure or LLM error so caller never loses data.

    synthesis_strict: wire from ParameterOverrides.synthesis_strict when
    rule_registry confirms synthesis_overgreedy — tightens orphan pressure.
    """
    if not observations:
        return [], [], {"outcome": "empty", "card_count": 0, "orphan_count": 0}

    affect_str = json.dumps(session_affect_summary or {}, indent=2)
    strict_clause = _STRICT_CLAUSE if synthesis_strict else _LENIENT_CLAUSE
    prompt = ISSUE_SYNTHESIS_PROMPT.format(
        synopsis=synopsis[:6000],
        affect_summary=affect_str,
        observations_json=json.dumps(observations, indent=2)[:80000],
        strict_clause=strict_clause,
    )

    try:
        raw = call_llm(prompt, max_tokens=8192)
    except Exception as exc:
        logger.warning("issue_synthesis: LLM call failed (%s)", exc)
        return [], observations, {"outcome": "llm_error", "error": str(exc)}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "issue_synthesis: failed to parse LLM JSON — keeping flat obs as orphans"
        )
        return [], observations, {"outcome": "parse_error"}

    cards = parsed.get("issue_cards") or []
    orphans = parsed.get("orphans") or []
    if not isinstance(cards, list) or not isinstance(orphans, list):
        logger.warning("issue_synthesis: bad shape — keeping flat obs as orphans")
        return [], observations, {"outcome": "bad_shape"}

    # Validate evidence_obs_indices against input observation count
    n_obs = len(observations)
    dropped_invalid_indices = 0
    for card in cards:
        indices = card.get("evidence_obs_indices") or []
        valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < n_obs]
        dropped = len(indices) - len(valid_indices)
        if dropped:
            dropped_invalid_indices += 1
            logger.info(
                "issue_synthesis: dropped %d out-of-range indices in card '%s'",
                dropped,
                card.get("title", "?"),
            )
        card["evidence_obs_indices"] = valid_indices

    # tier3 #29: demote cards with all-invalid indices to orphans (preserves evidence_quotes)
    from core.card_validators import _card_evidence_indices_valid  # noqa: PLC0415
    cards_invalid_indices_demoted = 0
    surviving_cards_pre_evidence = []
    for card in cards:
        if not _card_evidence_indices_valid(card, n_obs):
            card["demoted_invalid_indices"] = True
            orphans.append(card)
            cards_invalid_indices_demoted += 1
        else:
            surviving_cards_pre_evidence.append(card)
    cards = surviving_cards_pre_evidence

    # Sanity: every card must have ≥1 evidence_quote
    valid_cards = [c for c in cards if (c.get("evidence_quotes") or [])]
    dropped_cards = len(cards) - len(valid_cards)
    if dropped_cards:
        logger.info("issue_synthesis: dropped %d evidence-less cards", dropped_cards)

    # D4 post-process: dedupe near-duplicate evidence_quotes per card (Jaccard ≥0.7)
    total_deduped = 0
    for card in valid_cards:
        deduped, n_removed = _dedupe_quotes(card.get("evidence_quotes") or [])
        card["evidence_quotes"] = deduped
        total_deduped += n_removed

    # dropped_weak_observations: observations the LLM dropped per Rule 10 (DROP GATE).
    # This is a prompt-instruction stat — actual tracking requires LLM cooperation.
    # We approximate: obs that appear in neither evidence_obs_indices nor orphans.
    # When LLM doesn't return counts, this estimate may undercount (some obs are
    # legitimately uncited). Initialize conservatively to max(0, delta).
    obs_in_cards: set[int] = set()
    for card in valid_cards:
        for idx in card.get("evidence_obs_indices") or []:
            if isinstance(idx, int) and 0 <= idx < n_obs:
                obs_in_cards.add(idx)
    dropped_weak_observations = max(
        0, n_obs - len(orphans) - len(obs_in_cards)
    )

    stats = {
        "outcome": "ok",
        "card_count": len(valid_cards),
        "orphan_count": len(orphans),
        "dropped_evidenceless": dropped_cards,
        "quotes_deduped": total_deduped,
        "dropped_invalid_indices": dropped_invalid_indices,
        "cards_invalid_indices_demoted": cards_invalid_indices_demoted,
        "dropped_weak_observations": dropped_weak_observations,
        "synthesis_notes": parsed.get("synthesis_notes", ""),
    }
    return valid_cards, orphans, stats


# ---------------------------------------------------------------------------
# Card → Memory field mapping (Wave 3 / Task 3.1)
# ---------------------------------------------------------------------------

_ACTOR_RE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b')


def extract_card_memory_fields(card: dict) -> dict:
    """Map issue card fields to Memory column values for schema promotion.

    Returns a dict with keys: temporal_scope, confidence, affect_valence, actor.
    All values may be None (Memory columns are nullable).
    """
    temporal_scope = card.get("scope")
    confidence_raw = card.get("knowledge_type_confidence")
    if confidence_raw == "high":
        confidence = 0.9
    elif confidence_raw == "low":
        confidence = 0.5
    else:
        confidence = 0.7
    affect_valence = card.get("user_affect_valence")
    actor = None
    for fact in card.get("evidence_quotes") or []:
        m = _ACTOR_RE.search(fact)
        if m:
            actor = m.group(0)
            break
    return {
        "temporal_scope": temporal_scope,
        "confidence": confidence,
        "affect_valence": affect_valence,
        "actor": actor,
        "criterion_weights": card.get("criterion_weights"),
        "rejected_options": card.get("rejected_options"),
    }
