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

from core.llm import call_llm

logger = logging.getLogger(__name__)


ISSUE_SYNTHESIS_PROMPT = """You are reorganizing flat observations from a Claude Code session into ISSUE CARDS.

Each card frames a problem-and-resolution unit: what was at stake, the
options considered, the outcome, and how the user felt about it.

DO NOT invent observations. Every evidence_fact MUST appear verbatim in
the input observations[] list.

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
      "user_affect_valence": "friction|delight|surprise|neutral|mixed",
      "evidence_quotes": ["verbatim quote from input observations[].facts[]", ...],
      "evidence_obs_indices": [0, 3, 7],
      "kind": "decision|finding|preference|constraint|correction|open_question",
      "knowledge_type": "factual|conceptual|procedural|metacognitive",
      "importance": 0.0,
      "scope": "session-local | cross-session-durable"
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
3. Importance: take the max() of source observations' importance, then
   bump +0.05 if user_affect_valence is friction (Kensinger 2009 emotional
   encoding privilege).
4. scope = "cross-session-durable" only if the issue would still matter
   in a session three weeks from now. Otherwise "session-local".
5. orphans[] retains observations in their ORIGINAL schema (kind,
   knowledge_type, knowledge_type_confidence, importance, facts, cwd).
   Do not reformat them.
6. ENTITY GATE: If an observation does not share at least one named entity
   (person, system, file, concept) with any other observation in the input,
   orphan it rather than forcing it into a card. Prefer zero cards to a card
   with one low-importance observation.

If there are zero observations to organize, return:
{{"issue_cards": [], "orphans": [], "synthesis_notes": "no observations to synthesize"}}

Output ONLY the JSON object. No markdown fences. No commentary.
"""


def synthesize_issue_cards(
    observations: list[dict],
    synopsis: str,
    session_affect_summary: dict | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Run Stage 1.5 synthesis. Returns (issue_cards, orphans, stats).

    Falls back to (empty_cards, original_observations, error_stats) on parse
    failure or LLM error so caller never loses data.
    """
    if not observations:
        return [], [], {"outcome": "empty", "card_count": 0, "orphan_count": 0}

    affect_str = json.dumps(session_affect_summary or {}, indent=2)
    prompt = ISSUE_SYNTHESIS_PROMPT.format(
        synopsis=synopsis[:6000],
        affect_summary=affect_str,
        observations_json=json.dumps(observations, indent=2)[:80000],
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

    # Sanity: every card must have ≥1 evidence_quote
    valid_cards = [c for c in cards if (c.get("evidence_quotes") or [])]
    dropped_cards = len(cards) - len(valid_cards)
    if dropped_cards:
        logger.info("issue_synthesis: dropped %d evidence-less cards", dropped_cards)

    stats = {
        "outcome": "ok",
        "card_count": len(valid_cards),
        "orphan_count": len(orphans),
        "dropped_evidenceless": dropped_cards,
        "synthesis_notes": parsed.get("synthesis_notes", ""),
    }
    return valid_cards, orphans, stats
