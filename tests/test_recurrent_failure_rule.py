"""Tests for the recurrent_agent_failure cross-session meta-rule (Task #24)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.self_reflection_extraction import CorrectionCard, reflect_on_corpus


def _card(card_id: str, session_id: str, problem: str, outcome: str = "") -> CorrectionCard:
    return CorrectionCard(
        card_id=card_id,
        session_id=session_id,
        problem=problem,
        decision_or_outcome=outcome,
    )


def test_empty_input_no_fire():
    result = reflect_on_corpus([], root=None)
    assert result == []


def test_single_matching_card_no_fire():
    # Need >=2 cards — one is not enough
    cards = [_card("c1", "sess-A", "phase 1 api mismatch in skill spec")]
    result = reflect_on_corpus(cards, root=None)
    assert result == []


def test_two_cards_same_session_no_fire():
    # Two matches but both in the same session — need distinct sessions
    cards = [
        _card("c1", "sess-A", "did not read the source before authoring"),
        _card("c2", "sess-A", "another case where we did not read the module"),
    ]
    result = reflect_on_corpus(cards, root=None)
    assert result == []


def test_two_cards_two_sessions_matching_keyword_fires():
    # Both cards share "api mismatch" across distinct sessions
    cards = [
        _card("c1", "sess-A", "Phase 1 spec aptitude/skill mismatch — api mismatch with source"),
        _card("c2", "sess-B", "Phase 2 caused by api mismatch between client and server"),
    ]
    result = reflect_on_corpus(cards, root=None)
    # At least one observation fires
    assert len(result) >= 1
    matching = [o for o in result if o.evidence.get("keyword_matched") == "api mismatch"]
    assert len(matching) == 1
    obs = matching[0]
    assert obs.rule_id == "recurrent_agent_failure"
    assert obs.kind == "correction"
    assert obs.importance == 0.9
    # Evidence carries the matched keyword and card count
    assert obs.evidence["card_count"] >= 2
    assert "sess-A" in obs.evidence["sessions"]
    assert "sess-B" in obs.evidence["sessions"]
    # proposed_action references the keyword
    assert obs.evidence["keyword_matched"] in obs.proposed_action
    # entries capped at 3
    assert len(obs.evidence["entries"]) <= 3


def test_two_cards_two_sessions_no_keyword_overlap_no_fire():
    cards = [
        _card("c1", "sess-A", "forgot to run lint before committing"),
        _card("c2", "sess-B", "test timeout due to missing await"),
    ]
    result = reflect_on_corpus(cards, root=None)
    assert result == []


def test_three_cards_three_sessions_fires_once_per_keyword():
    # All three share "without reading" — should fire once with card_count=3
    cards = [
        _card("c1", "sess-A", "wrote spec without reading source"),
        _card("c2", "sess-B", "updated api without reading existing interface"),
        _card("c3", "sess-C", "filed issue without reading the prior thread"),
    ]
    result = reflect_on_corpus(cards, root=None)
    # Exactly one observation for "without reading"
    matching = [o for o in result if o.evidence.get("keyword_matched") == "without reading"]
    assert len(matching) == 1
    obs = matching[0]
    assert obs.evidence["card_count"] == 3
    # All three sessions represented
    assert set(obs.evidence["sessions"]) == {"sess-A", "sess-B", "sess-C"}
    # Evidence entries capped at 3 (all 3 fit here)
    assert len(obs.evidence["entries"]) == 3


def test_evidence_excerpt_truncated_to_120_chars():
    long_problem = "x" * 200
    cards = [
        _card("c1", "sess-A", long_problem),
        _card("c2", "sess-B", "api mismatch " + long_problem),
    ]
    result = reflect_on_corpus(cards, root=None)
    # Find any observation and check excerpt length
    for obs in result:
        for entry in obs.evidence.get("entries", []):
            assert len(entry["card_problem_excerpt"]) <= 120


def test_proposed_action_contains_card_ids():
    cards = [
        _card("card-001", "sess-A", "spec drift between v1 and v2"),
        _card("card-002", "sess-B", "spec drift caused wrong model field"),
    ]
    result = reflect_on_corpus(cards, root=None)
    matching = [o for o in result if o.evidence.get("keyword_matched") == "spec drift"]
    assert len(matching) == 1
    action = matching[0].proposed_action
    assert "card-001" in action
    assert "card-002" in action


def test_multiple_keywords_each_fires_independently():
    # c1+c2 share "api mismatch", c3+c4 share "did not read"
    cards = [
        _card("c1", "sess-A", "api mismatch in the new route"),
        _card("c2", "sess-B", "api mismatch caused 404s"),
        _card("c3", "sess-C", "did not read the spec before implementing"),
        _card("c4", "sess-D", "did not read prior art, duplicated work"),
    ]
    result = reflect_on_corpus(cards, root=None)
    keywords_fired = {o.evidence["keyword_matched"] for o in result}
    assert "api mismatch" in keywords_fired
    assert "did not read" in keywords_fired
