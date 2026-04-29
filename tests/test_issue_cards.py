"""Tests for core/issue_cards.py — Stage 1.5 issue-card synthesis."""

from __future__ import annotations

import json
from unittest.mock import patch

from core.issue_cards import synthesize_issue_cards, ISSUE_SYNTHESIS_PROMPT


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
