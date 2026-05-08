"""
Tests for core.schemas — Pydantic validation of LLM consolidation output.

Coverage:
  - ConsolidationDecision: action, importance, UUID fields, destructive rationale
  - ConsolidationResponse: envelope parsing
  - ContradictionResolution: confidence, resolution_type
  - StageTransition: valid and invalid transitions
  - Happy-path: card-shaped keep decision round-trips cleanly
  - "null" string coercion
"""

import uuid

import pytest
from pydantic import ValidationError

from core.schemas import (
    ALLOWED_ACTIONS,
    ConsolidationDecision,
    ConsolidationResponse,
    ContradictionResolution,
    StageTransition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_uuid4() -> str:
    return str(uuid.uuid4())


def _keep_decision(**overrides) -> dict:
    base = {
        "action": "keep",
        "observation": "User prefers short variable names.",
        "rationale": "Stated preference, load-bearing.",
        "title": "Short variable names",
        "summary": "User prefers terse identifiers.",
        "target_path": "preferences/naming.md",
        "importance": 0.7,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TestConsolidationDecisionAction
# ---------------------------------------------------------------------------

class TestConsolidationDecisionAction:
    def test_valid_actions_accepted(self):
        for action in ALLOWED_ACTIONS:
            d = ConsolidationDecision(**_keep_decision(action=action, rationale="non-empty" if action in ("prune", "archive") else "rationale"))
            assert d.action == action

    def test_action_normalised_to_lowercase(self):
        d = ConsolidationDecision(**_keep_decision(action="KEEP"))
        assert d.action == "keep"

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError, match="action must be one of"):
            ConsolidationDecision(**_keep_decision(action="delete"))

    def test_action_not_string_rejected(self):
        with pytest.raises(ValidationError):
            ConsolidationDecision(**_keep_decision(action=42))

    def test_unknown_action_string_rejected(self):
        with pytest.raises(ValidationError):
            ConsolidationDecision(**_keep_decision(action="supersede"))


# ---------------------------------------------------------------------------
# TestConsolidationDecisionImportance
# ---------------------------------------------------------------------------

class TestConsolidationDecisionImportance:
    def test_valid_importance_accepted(self):
        d = ConsolidationDecision(**_keep_decision(importance=0.5))
        assert d.importance == pytest.approx(0.5)

    def test_importance_zero_accepted(self):
        d = ConsolidationDecision(**_keep_decision(importance=0.0))
        assert d.importance == pytest.approx(0.0)

    def test_importance_one_accepted(self):
        d = ConsolidationDecision(**_keep_decision(importance=1.0))
        assert d.importance == pytest.approx(1.0)

    def test_importance_above_one_rejected(self):
        with pytest.raises(ValidationError, match=r"importance must be in \[0\.0, 1\.0\]"):
            ConsolidationDecision(**_keep_decision(importance=1.1))

    def test_importance_below_zero_rejected(self):
        with pytest.raises(ValidationError, match=r"importance must be in \[0\.0, 1\.0\]"):
            ConsolidationDecision(**_keep_decision(importance=-0.1))

    def test_importance_string_number_rejected(self):
        # "high" cannot be coerced to float
        with pytest.raises(ValidationError):
            ConsolidationDecision(**_keep_decision(importance="high"))

    def test_importance_none_accepted(self):
        d = ConsolidationDecision(**_keep_decision(importance=None))
        assert d.importance is None

    def test_importance_absent_accepted(self):
        data = _keep_decision()
        data.pop("importance")
        d = ConsolidationDecision(**data)
        assert d.importance is None

    def test_raw_importance_validated_same_way(self):
        with pytest.raises(ValidationError):
            ConsolidationDecision(**_keep_decision(raw_importance=2.0))


# ---------------------------------------------------------------------------
# TestConsolidationDecisionUUIDs
# ---------------------------------------------------------------------------

class TestConsolidationDecisionUUIDs:
    def test_valid_uuid4_reinforces_accepted(self):
        uid = _valid_uuid4()
        d = ConsolidationDecision(**_keep_decision(action="promote", reinforces=uid))
        assert d.reinforces == uid

    def test_null_string_reinforces_coerced_to_none(self):
        d = ConsolidationDecision(**_keep_decision(reinforces="null"))
        assert d.reinforces is None

    def test_none_reinforces_accepted(self):
        d = ConsolidationDecision(**_keep_decision(reinforces=None))
        assert d.reinforces is None

    def test_non_uuid_reinforces_rejected(self):
        with pytest.raises(ValidationError, match="reinforces must be a valid UUID4"):
            ConsolidationDecision(**_keep_decision(reinforces="not-a-uuid"))

    def test_uuid1_reinforces_rejected(self):
        uid1 = str(uuid.uuid1())
        with pytest.raises(ValidationError, match="UUID version 4"):
            ConsolidationDecision(**_keep_decision(reinforces=uid1))

    def test_valid_uuid4_contradicts_accepted(self):
        uid = _valid_uuid4()
        d = ConsolidationDecision(**_keep_decision(contradicts=uid))
        assert d.contradicts == uid

    def test_null_string_contradicts_coerced_to_none(self):
        d = ConsolidationDecision(**_keep_decision(contradicts="null"))
        assert d.contradicts is None

    def test_non_uuid_contradicts_rejected(self):
        with pytest.raises(ValidationError, match="contradicts must be a valid UUID4"):
            ConsolidationDecision(**_keep_decision(contradicts="bad-id"))

    def test_resolves_question_id_null_coerced(self):
        d = ConsolidationDecision(**_keep_decision(resolves_question_id="null"))
        assert d.resolves_question_id is None

    def test_resolves_question_id_valid_uuid4_accepted(self):
        uid = _valid_uuid4()
        d = ConsolidationDecision(**_keep_decision(resolves_question_id=uid))
        assert d.resolves_question_id == uid

    def test_resolves_question_id_bad_string_rejected(self):
        with pytest.raises(ValidationError, match="resolves_question_id must be a valid UUID4"):
            ConsolidationDecision(**_keep_decision(resolves_question_id="abc-123"))


# ---------------------------------------------------------------------------
# TestConsolidationDecisionDestructiveRationale
# ---------------------------------------------------------------------------

class TestConsolidationDecisionDestructiveRationale:
    def test_prune_with_rationale_accepted(self):
        d = ConsolidationDecision(
            action="prune",
            observation="Trivial debug log statement.",
            rationale="Not durable, not load-bearing.",
        )
        assert d.action == "prune"

    def test_prune_empty_rationale_rejected(self):
        with pytest.raises(ValidationError, match="requires a non-empty rationale"):
            ConsolidationDecision(
                action="prune",
                observation="Something.",
                rationale="",
            )

    def test_prune_whitespace_only_rationale_rejected(self):
        with pytest.raises(ValidationError, match="requires a non-empty rationale"):
            ConsolidationDecision(
                action="prune",
                observation="Something.",
                rationale="   ",
            )

    def test_archive_with_rationale_accepted(self):
        d = ConsolidationDecision(
            action="archive",
            observation="Old preference superseded.",
            rationale="Replaced by newer explicit preference.",
        )
        assert d.action == "archive"

    def test_archive_empty_rationale_rejected(self):
        with pytest.raises(ValidationError, match="requires a non-empty rationale"):
            ConsolidationDecision(
                action="archive",
                observation="Something.",
                rationale="",
            )

    def test_keep_empty_rationale_allowed(self):
        # keep is not destructive; empty rationale is fine
        d = ConsolidationDecision(
            action="keep",
            observation="Something.",
            rationale="",
        )
        assert d.action == "keep"

    def test_promote_empty_rationale_allowed(self):
        d = ConsolidationDecision(
            action="promote",
            observation="Something.",
            rationale="",
        )
        assert d.action == "promote"


# ---------------------------------------------------------------------------
# TestConsolidationResponse
# ---------------------------------------------------------------------------

class TestConsolidationResponse:
    def test_empty_decisions_accepted(self):
        resp = ConsolidationResponse(decisions=[])
        assert resp.decisions == []

    def test_parse_from_dict(self):
        data = {
            "decisions": [
                {
                    "action": "keep",
                    "observation": "User prefers Python.",
                    "rationale": "Stated preference.",
                    "importance": 0.8,
                }
            ]
        }
        resp = ConsolidationResponse(**data)
        assert len(resp.decisions) == 1
        assert resp.decisions[0].action == "keep"
        assert resp.decisions[0].importance == pytest.approx(0.8)

    def test_invalid_decision_in_list_raises(self):
        data = {
            "decisions": [
                {
                    "action": "explode",
                    "observation": "x",
                    "rationale": "y",
                }
            ]
        }
        with pytest.raises(ValidationError):
            ConsolidationResponse(**data)

    def test_multiple_decisions_parsed(self):
        data = {
            "decisions": [
                {"action": "keep", "observation": "A", "rationale": "r"},
                {"action": "prune", "observation": "B", "rationale": "not useful"},
            ]
        }
        resp = ConsolidationResponse(**data)
        assert len(resp.decisions) == 2
        assert resp.decisions[1].action == "prune"


# ---------------------------------------------------------------------------
# TestContradictionResolution
# ---------------------------------------------------------------------------

class TestContradictionResolution:
    def test_happy_path(self):
        r = ContradictionResolution(
            confidence=0.75,
            resolution_type="scoped",
            refined_title="Preference (scoped to project A)",
            refined_content="User prefers verbose logs in project A only.",
        )
        assert r.confidence == pytest.approx(0.75)
        assert r.resolution_type == "scoped"

    def test_defaults_used_on_empty(self):
        r = ContradictionResolution()
        assert r.confidence == pytest.approx(0.0)
        assert r.resolution_type == "scoped"
        assert r.refined_title == ""
        assert r.refined_content == ""

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError, match=r"confidence must be in \[0\.0, 1\.0\]"):
            ContradictionResolution(confidence=1.1)

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError, match=r"confidence must be in \[0\.0, 1\.0\]"):
            ContradictionResolution(confidence=-0.5)

    def test_confidence_string_rejected(self):
        with pytest.raises(ValidationError):
            ContradictionResolution(confidence="high")

    def test_invalid_resolution_type_rejected(self):
        with pytest.raises(ValidationError):
            ContradictionResolution(resolution_type="delete")

    def test_superseded_resolution_accepted(self):
        r = ContradictionResolution(resolution_type="superseded", confidence=0.9)
        assert r.resolution_type == "superseded"

    def test_coexist_resolution_accepted(self):
        r = ContradictionResolution(resolution_type="coexist", confidence=0.5)
        assert r.resolution_type == "coexist"


# ---------------------------------------------------------------------------
# TestStageTransition
# ---------------------------------------------------------------------------

class TestStageTransition:
    def test_ephemeral_to_consolidated(self):
        t = StageTransition(from_stage="ephemeral", to_stage="consolidated")
        assert t.to_stage == "consolidated"

    def test_consolidated_to_crystallized(self):
        t = StageTransition(from_stage="consolidated", to_stage="crystallized")
        assert t.to_stage == "crystallized"

    def test_crystallized_to_instinctive(self):
        t = StageTransition(from_stage="crystallized", to_stage="instinctive")
        assert t.to_stage == "instinctive"

    def test_any_to_pending_delete(self):
        for stage in ("ephemeral", "consolidated", "crystallized", "instinctive"):
            t = StageTransition(from_stage=stage, to_stage="pending_delete")
            assert t.to_stage == "pending_delete"

    def test_ephemeral_to_instinctive_rejected(self):
        with pytest.raises(ValidationError, match="Invalid stage transition"):
            StageTransition(from_stage="ephemeral", to_stage="instinctive")

    def test_ephemeral_to_crystallized_rejected(self):
        with pytest.raises(ValidationError, match="Invalid stage transition"):
            StageTransition(from_stage="ephemeral", to_stage="crystallized")

    def test_consolidated_to_instinctive_rejected(self):
        with pytest.raises(ValidationError, match="Invalid stage transition"):
            StageTransition(from_stage="consolidated", to_stage="instinctive")

    def test_unknown_from_stage_rejected(self):
        with pytest.raises(ValidationError, match="Unknown from_stage"):
            StageTransition(from_stage="zombie", to_stage="consolidated")


# ---------------------------------------------------------------------------
# TestHappyPathCardDecision
# ---------------------------------------------------------------------------

class TestHappyPathCardDecision:
    def test_full_card_shaped_keep_decision_parses(self):
        """A card-shaped keep decision (with scope, evidence_quotes, etc.) round-trips cleanly."""
        uid = _valid_uuid4()
        data = {
            "action": "keep",
            "observation": "User consistently uses snake_case for all identifiers.",
            "rationale": "Stated explicit preference in multiple sessions.",
            "title": "Snake-case naming preference",
            "summary": "User prefers snake_case throughout.",
            "target_path": "preferences/naming.md",
            "importance": 0.85,
            "raw_importance": 0.80,
            "kind": "preference",
            "knowledge_type": "procedural",
            "knowledge_type_confidence": "high",
            "facts": ["User used snake_case in all reviewed code"],
            "cwd": "/Users/test/project",
            "subject": "user",
            "work_event": None,
            "subtitle": "Snake-case for all identifiers",
            "reinforces": uid,
            "contradicts": None,
            "resolves_question_id": "null",
            "scope": "cross-session-durable",
            "evidence_quotes": ["snake_case throughout", "always snake"],
            "user_affect_valence": "neutral",
            "criterion_weights": {"readability": "strong", "consistency": "hard_veto"},
            "rejected_options": [{"option": "camelCase", "reason": "not Pythonic"}],
        }
        d = ConsolidationDecision(**data)
        assert d.action == "keep"
        assert d.importance == pytest.approx(0.85)
        assert d.reinforces == uid
        assert d.contradicts is None
        assert d.resolves_question_id is None  # "null" coerced
        assert d.criterion_weights == {"readability": "strong", "consistency": "hard_veto"}
        assert d.rejected_options == [{"option": "camelCase", "reason": "not Pythonic"}]
        assert d.scope == "cross-session-durable"

    def test_minimal_prune_decision_parses(self):
        d = ConsolidationDecision(
            action="prune",
            observation="Debug print statement was added temporarily.",
            rationale="Transient; not a durable observation.",
        )
        assert d.action == "prune"
        assert d.importance is None
        assert d.reinforces is None

    def test_promote_decision_parses(self):
        uid = _valid_uuid4()
        d = ConsolidationDecision(
            action="promote",
            observation="User prefers Python again.",
            rationale="Reinforced existing preference.",
            reinforces=uid,
        )
        assert d.action == "promote"
        assert d.reinforces == uid

    def test_extra_fields_silently_allowed(self):
        """Forward-compat: unknown keys from future prompt schema don't cause rejection."""
        d = ConsolidationDecision(
            action="keep",
            observation="x",
            rationale="r",
            some_future_field="value",
            another_new_field=42,
        )
        assert d.action == "keep"

    def test_consolidation_response_wrapper(self):
        uid = _valid_uuid4()
        data = {
            "decisions": [
                {
                    "action": "keep",
                    "observation": "Prefers explicit imports.",
                    "rationale": "Load-bearing preference.",
                    "importance": 0.75,
                    "contradicts": uid,
                },
                {
                    "action": "prune",
                    "observation": "Mentioned the weather briefly.",
                    "rationale": "Not durable or actionable.",
                },
            ]
        }
        resp = ConsolidationResponse(**data)
        assert len(resp.decisions) == 2
        assert resp.decisions[0].contradicts == uid
        assert resp.decisions[1].action == "prune"
