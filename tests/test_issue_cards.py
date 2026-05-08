"""Tests for core/issue_cards.py — Stage 1.5 issue-card synthesis."""

from __future__ import annotations

import json
from unittest.mock import patch

from core.issue_cards import synthesize_issue_cards, ISSUE_SYNTHESIS_PROMPT, extract_card_memory_fields
from core.card_validators import _card_evidence_indices_valid


def _make_obs(facts: list[str], importance: float = 0.5) -> dict:
    return {
        "kind": "finding",
        "knowledge_type": "factual",
        "knowledge_type_confidence": "medium",
        "importance": importance,
        "facts": facts,
        "cwd": "/tmp",
    }


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
        """Return dict always has all six keys."""
        result = extract_card_memory_fields({})
        assert set(result.keys()) == {
            "temporal_scope", "confidence", "affect_valence", "actor",
            "criterion_weights", "rejected_options",
        }


# ---------------------------------------------------------------------------
# TestEvidenceIndicesValidation — Item 15 acceptance criteria
# ---------------------------------------------------------------------------

class TestEvidenceIndicesValidation:
    """evidence_obs_indices values outside [0, n_obs) are dropped; card survives."""

    def _card_with_indices(self, indices: list, title: str = "Test card") -> dict:
        return {
            "title": title,
            "problem": "A problem.",
            "options_considered": [],
            "decision_or_outcome": "An outcome.",
            "user_reaction": "neutral",
            "user_affect_valence": "neutral",
            "evidence_quotes": ["Emma approved the plan."],
            "evidence_obs_indices": indices,
            "kind": "finding",
            "knowledge_type": "factual",
            "importance": 0.6,
            "scope": "session-local",
        }

    def test_out_of_range_index_dropped_card_demoted_to_orphan(self):
        """Card with evidence_obs_indices: [999] and 5 obs → index stripped, card demoted to orphan."""
        obs = [_make_obs([f"Obs {i}."]) for i in range(5)]
        card = self._card_with_indices([999])
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "one card",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            cards, orphans, stats = synthesize_issue_cards(
                obs,
                synopsis="synopsis",
                session_affect_summary=None,
            )
        assert stats["outcome"] == "ok"
        # card is demoted to orphan — not in valid_cards
        assert len(cards) == 0
        assert len(orphans) == 1
        assert orphans[0].get("demoted_invalid_indices") is True
        assert stats["dropped_invalid_indices"] == 1
        assert stats["cards_invalid_indices_demoted"] == 1

    def test_valid_indices_preserved(self):
        """Valid in-range indices are kept unchanged."""
        obs = [_make_obs([f"Obs {i}."]) for i in range(5)]
        card = self._card_with_indices([0, 2, 4])
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "all valid",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            cards, orphans, stats = synthesize_issue_cards(
                obs,
                synopsis="synopsis",
                session_affect_summary=None,
            )
        assert cards[0]["evidence_obs_indices"] == [0, 2, 4]
        assert stats["dropped_invalid_indices"] == 0

    def test_invalid_type_filtered(self):
        """Non-integer index values (e.g. strings, floats as non-int) are filtered."""
        obs = [_make_obs([f"Obs {i}."]) for i in range(5)]
        # String indices and a float (not int) should be dropped
        card = self._card_with_indices(["0", 1, 3.5, 2])  # "0", 3.5 are non-int
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "mixed types",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            cards, orphans, stats = synthesize_issue_cards(
                obs,
                synopsis="synopsis",
                session_affect_summary=None,
            )
        # Only the int 1 and int 2 survive; "0" (str) and 3.5 (float) are dropped
        assert cards[0]["evidence_obs_indices"] == [1, 2]
        assert stats["dropped_invalid_indices"] == 1


# ---------------------------------------------------------------------------
# TestDropGateStat — Item 16 acceptance criteria
# ---------------------------------------------------------------------------

class TestDropGateStat:
    """Rule 10 DROP GATE in prompt; dropped_weak_observations present in stats."""

    def test_prompt_contains_drop_gate_text(self):
        """ISSUE_SYNTHESIS_PROMPT contains Rule 10 DROP GATE text verbatim."""
        assert "DROP GATE" in ISSUE_SYNTHESIS_PROMPT
        assert "importance < 0.3" in ISSUE_SYNTHESIS_PROMPT

    def test_dropped_weak_observations_in_stats(self):
        """stats dict returned by synthesize_issue_cards contains dropped_weak_observations key."""
        obs = [_make_obs(["Emma chose the approach."]), _make_obs(["Deployment finished."])]
        card = {
            "title": "Approach decision",
            "problem": "Deciding on approach.",
            "options_considered": [],
            "decision_or_outcome": "Emma chose the approach.",
            "user_reaction": "accept",
            "user_affect_valence": "neutral",
            "evidence_quotes": ["Emma chose the approach."],
            "evidence_obs_indices": [0],
            "kind": "decision",
            "knowledge_type": "procedural",
            "importance": 0.7,
            "scope": "session-local",
        }
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "one card",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            _, _, stats = synthesize_issue_cards(
                obs,
                synopsis="synopsis",
                session_affect_summary=None,
            )
        assert "dropped_weak_observations" in stats
        assert isinstance(stats["dropped_weak_observations"], int)
        assert stats["dropped_weak_observations"] >= 0


# ---------------------------------------------------------------------------
# TestMixedValenceInstruction — Item 17 acceptance criteria
# ---------------------------------------------------------------------------

class TestRule3KensingerRemoved:
    """Rule 3 in prompt must NOT mention Kensinger bump; consolidator owns it."""

    def test_rule3_no_kensinger_bump(self):
        assert "bump +0.05" not in ISSUE_SYNTHESIS_PROMPT
        assert "Kensinger 2009" not in ISSUE_SYNTHESIS_PROMPT

    def test_rule3_consolidator_note_present(self):
        assert "consolidator.py" in ISSUE_SYNTHESIS_PROMPT
        assert "do not pre-apply it" in ISSUE_SYNTHESIS_PROMPT


class TestAllIndicesInvalidDemotion:
    """tier3 #29 — cards with all-invalid indices are demoted to orphans."""

    def _card_with_indices(self, indices: list) -> dict:
        return {
            "title": "Demote me",
            "problem": "A problem.",
            "options_considered": [],
            "decision_or_outcome": "An outcome.",
            "user_reaction": "neutral",
            "user_affect_valence": "neutral",
            "evidence_quotes": ["Emma approved the plan."],
            "evidence_obs_indices": indices,
            "kind": "finding",
            "knowledge_type": "factual",
            "importance": 0.6,
            "scope": "session-local",
        }

    def test_all_invalid_indices_demoted(self):
        obs = [_make_obs([f"Obs {i}."]) for i in range(3)]
        card = self._card_with_indices([50, 99])
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "hallucinated indices",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            cards, orphans, stats = synthesize_issue_cards(obs, synopsis="s", session_affect_summary=None)
        assert len(cards) == 0
        assert len(orphans) == 1
        assert orphans[0]["demoted_invalid_indices"] is True
        assert stats["cards_invalid_indices_demoted"] == 1

    def test_partial_valid_indices_not_demoted(self):
        """Card with at least one valid index is NOT demoted."""
        obs = [_make_obs([f"Obs {i}."]) for i in range(5)]
        card = self._card_with_indices([0, 999])
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "one valid index",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            cards, orphans, stats = synthesize_issue_cards(obs, synopsis="s", session_affect_summary=None)
        assert len(cards) == 1
        assert stats["cards_invalid_indices_demoted"] == 0

    def test_empty_indices_demoted(self):
        """Card with evidence_obs_indices: [] is demoted (no valid indices)."""
        obs = [_make_obs([f"Obs {i}."]) for i in range(3)]
        card = self._card_with_indices([])
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "empty indices",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            cards, orphans, stats = synthesize_issue_cards(obs, synopsis="s", session_affect_summary=None)
        assert len(cards) == 0
        assert stats["cards_invalid_indices_demoted"] == 1

    def test_demoted_stat_zero_when_all_valid(self):
        obs = [_make_obs([f"Obs {i}."]) for i in range(5)]
        card = self._card_with_indices([0, 2])
        llm_response = json.dumps({
            "issue_cards": [card],
            "orphans": [],
            "synthesis_notes": "all valid",
        })
        with patch("core.issue_cards.call_llm", return_value=llm_response):
            _, _, stats = synthesize_issue_cards(obs, synopsis="s", session_affect_summary=None)
        assert stats["cards_invalid_indices_demoted"] == 0


class TestMixedValenceInstruction:
    """Mixed-valence sentence is present verbatim in ISSUE_SYNTHESIS_PROMPT."""

    def test_mixed_valence_trajectory_guidance_present(self):
        """Prompt contains the mixed-valence trajectory instruction."""
        assert "Use 'mixed' when the user's reaction evolved across the card's span" in ISSUE_SYNTHESIS_PROMPT
        assert "Track the trajectory in 'user_reaction' text." in ISSUE_SYNTHESIS_PROMPT
