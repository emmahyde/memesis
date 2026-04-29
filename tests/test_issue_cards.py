"""Tests for core/issue_cards.py — Stage 1.5 issue-card synthesis."""

from __future__ import annotations

import json
from unittest.mock import patch

from core.issue_cards import synthesize_issue_cards, ISSUE_SYNTHESIS_PROMPT, extract_card_memory_fields


class TestRule0EntityGate:
    """Verify that the ENTITY GATE (Rule 6) text is present and that the
    synthesize_issue_cards() function correctly handles solo observations,
    shared-entity observations, and empty input."""

    def _make_obs(self, facts: list[str], importance: float = 0.5) -> dict:
        return {
            "kind": "finding",
            "knowledge_type": "factual",
            "knowledge_type_confidence": "medium",
            "importance": importance,
            "facts": facts,
            "cwd": "/tmp",
        }

    def test_rule0_text_in_prompt(self):
        """Rule 6 ENTITY GATE text appears verbatim in ISSUE_SYNTHESIS_PROMPT."""
        assert "ENTITY GATE: If an observation does not share at least one named entity" in ISSUE_SYNTHESIS_PROMPT
        assert "orphan it rather than forcing it into a card." in ISSUE_SYNTHESIS_PROMPT
        assert "Prefer zero cards to a card\n   with one low-importance observation." in ISSUE_SYNTHESIS_PROMPT

    def test_solo_observation_becomes_orphan(self):
        """Single observation with no entity overlap → LLM returns it in orphans[], card_count == 0."""
        obs = [self._make_obs(["The deployment pipeline broke at step 3."])]
        orphan_obs = self._make_obs(["The deployment pipeline broke at step 3."])
        llm_response = json.dumps({
            "issue_cards": [],
            "orphans": [orphan_obs],
            "synthesis_notes": "Single observation with no entity overlap — placed in orphans per Rule 6.",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            cards, orphans, stats = synthesize_issue_cards(
                obs,
                synopsis="A short session synopsis.",
                session_affect_summary={"dominant_valence": "neutral"},
            )
        assert stats["card_count"] == 0
        assert stats["outcome"] == "ok"
        assert cards == []
        assert len(orphans) == 1

    def test_shared_entity_stays_in_card(self):
        """Two observations sharing a named entity → LLM returns a card, card_count >= 1."""
        obs = [
            self._make_obs(["Emma approved the database migration plan."]),
            self._make_obs(["Emma noted that the rollback procedure was unclear."]),
        ]
        card = {
            "title": "Database migration approval",
            "problem": "Migration plan reviewed but rollback unclear.",
            "options_considered": [],
            "decision_or_outcome": "Emma approved with a follow-up on rollback.",
            "user_reaction": "cautious accept",
            "user_affect_valence": "neutral",
            "evidence_quotes": ["Emma approved the database migration plan."],
            "evidence_obs_indices": [0, 1],
            "kind": "decision",
            "knowledge_type": "procedural",
            "importance": 0.65,
            "scope": "session-local",
        }
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "Shared entity 'Emma' — grouped into one card.",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            cards, orphans, stats = synthesize_issue_cards(
                obs,
                synopsis="A session about DB migration.",
                session_affect_summary=None,
            )
        assert stats["card_count"] >= 1
        assert stats["outcome"] == "ok"
        assert len(cards) == 1
        assert orphans == []

    def test_empty_observations_returns_empty(self):
        """synthesize_issue_cards([]) returns ([], [], {...}) without making an LLM call."""
        with patch("core.issue_cards.call_llm") as mock_llm:
            cards, orphans, stats = synthesize_issue_cards(
                [],
                synopsis="irrelevant",
                session_affect_summary=None,
            )
        mock_llm.assert_not_called()
        assert cards == []
        assert orphans == []
        assert stats["outcome"] == "empty"
        assert stats["card_count"] == 0


# ---------------------------------------------------------------------------
# TestExtractCardMemoryFields — Task 3.1 acceptance criteria
# ---------------------------------------------------------------------------

class TestExtractCardMemoryFields:
    """Unit tests for extract_card_memory_fields helper."""

    def test_scope_mapping(self):
        card = {"scope": "cross-session-durable"}
        result = extract_card_memory_fields(card)
        assert result["temporal_scope"] == "cross-session-durable"

    def test_scope_session_local(self):
        card = {"scope": "session-local"}
        result = extract_card_memory_fields(card)
        assert result["temporal_scope"] == "session-local"

    def test_scope_absent_is_none(self):
        card = {}
        result = extract_card_memory_fields(card)
        assert result["temporal_scope"] is None

    def test_confidence_high(self):
        card = {"knowledge_type_confidence": "high"}
        result = extract_card_memory_fields(card)
        assert result["confidence"] == 0.9

    def test_confidence_low(self):
        card = {"knowledge_type_confidence": "low"}
        result = extract_card_memory_fields(card)
        assert result["confidence"] == 0.5

    def test_confidence_default(self):
        """None or unknown knowledge_type_confidence → 0.7."""
        card = {}
        result = extract_card_memory_fields(card)
        assert result["confidence"] == 0.7

    def test_confidence_unknown_value(self):
        card = {"knowledge_type_confidence": "medium"}
        result = extract_card_memory_fields(card)
        assert result["confidence"] == 0.7

    def test_affect_valence_passed_through(self):
        card = {"user_affect_valence": "friction"}
        result = extract_card_memory_fields(card)
        assert result["affect_valence"] == "friction"

    def test_affect_valence_absent_is_none(self):
        card = {}
        result = extract_card_memory_fields(card)
        assert result["affect_valence"] is None

    def test_actor_regex_single_name(self):
        card = {"evidence_quotes": ["Emma approved the design."]}
        result = extract_card_memory_fields(card)
        assert result["actor"] == "Emma"

    def test_actor_regex_two_word_name(self):
        card = {"evidence_quotes": ["John Smith reviewed the PR."]}
        result = extract_card_memory_fields(card)
        assert result["actor"] == "John Smith"

    def test_actor_regex_uses_first_quote(self):
        """Actor is extracted from the first evidence_quote that has a match."""
        card = {"evidence_quotes": ["Alice suggested the approach.", "Bob disagreed."]}
        result = extract_card_memory_fields(card)
        assert result["actor"] == "Alice"

    def test_actor_null_when_no_quote(self):
        card = {"evidence_quotes": []}
        result = extract_card_memory_fields(card)
        assert result["actor"] is None

    def test_actor_null_when_quotes_missing(self):
        card = {}
        result = extract_card_memory_fields(card)
        assert result["actor"] is None

    def test_actor_null_when_no_capitalized_word(self):
        """Quotes without capitalized words → actor is None."""
        card = {"evidence_quotes": ["the system crashed at midnight."]}
        result = extract_card_memory_fields(card)
        assert result["actor"] is None

    def test_all_fields_returned(self):
        """Return dict always has all four keys."""
        result = extract_card_memory_fields({})
        assert set(result.keys()) == {"temporal_scope", "confidence", "affect_valence", "actor"}
