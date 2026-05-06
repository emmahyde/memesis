"""Tests for core/card_validators.py — circular-evidence predicate."""

from __future__ import annotations

from core.card_validators import _card_evidence_load_bearing, _card_evidence_indices_valid


def _card(quote: str, problem: str = "", outcome: str = "", kind: str = "finding") -> dict:
    return {
        "evidence_quotes": [quote],
        "problem": problem,
        "decision_or_outcome": outcome,
        "kind": kind,
    }


class TestPronounCheck:
    def test_first_person_i(self):
        assert _card_evidence_load_bearing(_card("I told the agent to stop."))

    def test_first_person_we(self):
        assert _card_evidence_load_bearing(_card("We prefer subagent over inline."))

    def test_second_person_you(self):
        assert _card_evidence_load_bearing(_card("You should always check the log."))

    def test_possessive_my(self):
        assert _card_evidence_load_bearing(_card("My config was reset."))

    def test_no_pronoun_fails(self):
        # purely third-person, no technical tokens, no imperative
        assert not _card_evidence_load_bearing(
            _card("Subagent threshold decision was made.", "Subagent threshold decision", "Subagent threshold decision")
        )


class TestImperativeCheck:
    def test_dont_at_start(self):
        assert _card_evidence_load_bearing(_card("Don't use inline codemod for large files."))

    def test_use_at_start(self):
        assert _card_evidence_load_bearing(_card("Use subagent over inline codemod."))

    def test_prefer_at_start(self):
        assert _card_evidence_load_bearing(_card("prefer subagent threshold of 50 lines."))

    def test_never_at_start(self):
        assert _card_evidence_load_bearing(_card("Never skip the refine pass."))

    def test_always_at_start(self):
        assert _card_evidence_load_bearing(_card("Always validate JSON before writing."))

    def test_imperative_not_at_start(self):
        # "use" buried mid-sentence — should NOT match imperative check
        # (still may pass via technical token if tokens differ)
        card = _card(
            "The team decided to use the approach.",
            "The team decided to use the approach",
            "The team decided to use the approach",
        )
        # pronoun check: no; imperative at start: no; technical tokens: none
        assert not _card_evidence_load_bearing(card)


class TestTechnicalTokenCheck:
    def test_version_number(self):
        assert _card_evidence_load_bearing(
            _card("Bumped gum.monogame to 2026.4.5.1", "Bumped gum", "Updated dependency")
        )

    def test_file_path_with_slash(self):
        assert _card_evidence_load_bearing(
            _card("core/card_validators.py was added", "New file added", "New file added")
        )

    def test_colon_token(self):
        # "http:" contains a colon → technical token
        assert _card_evidence_load_bearing(
            _card("Endpoint http:api changed", "Endpoint changed", "Endpoint changed")
        )

    def test_dotted_token_absent_from_body(self):
        assert _card_evidence_load_bearing(
            _card("Sector.Engine.Core is the dependency", "dependency", "dependency")
        )

    def test_no_technical_token_when_body_covers_it(self):
        # All tokens in quote also in body — no new technical token
        card = _card(
            "Flecs split rationale decided",
            "Flecs split rationale decided",
            "Flecs split rationale decided",
        )
        assert not _card_evidence_load_bearing(card)


class TestMultiQuotePassthrough:
    def test_multi_quote_card_always_passes(self):
        """Cards with >1 evidence_quote skip the check entirely."""
        card = {
            "evidence_quotes": ["quote one", "quote two"],
            "problem": "quote one quote two",
            "decision_or_outcome": "quote one quote two",
            "kind": "finding",
        }
        assert _card_evidence_load_bearing(card)

    def test_zero_quote_card_returns_false(self):
        """Cards with 0 quotes (shouldn't reach this, but guard anyway)."""
        card = {
            "evidence_quotes": [],
            "problem": "",
            "decision_or_outcome": "",
            "kind": "finding",
        }
        assert not _card_evidence_load_bearing(card)


class TestKnownCircularExamples:
    """Reproduce the four cards named in the audit."""

    def test_inline_codemod_circular(self):
        # "Inline codemod vs subagent threshold" — quote restates body
        card = _card(
            "Inline codemod vs subagent threshold was evaluated",
            "Inline codemod vs subagent threshold",
            "Inline codemod vs subagent threshold was evaluated",
        )
        assert not _card_evidence_load_bearing(card)

    def test_flecs_world_entity_circular(self):
        card = _card(
            "FlecsWorldEntityWorld split rationale was discussed",
            "FlecsWorldEntityWorld split rationale",
            "FlecsWorldEntityWorld split rationale was discussed",
        )
        assert not _card_evidence_load_bearing(card)

    def test_no_godot_test_framework_circular(self):
        card = _card(
            "No Godot side test framework baseline exists",
            "No Godot side test framework baseline",
            "No Godot side test framework baseline exists",
        )
        assert not _card_evidence_load_bearing(card)

    def test_vendored_claude_mem_circular(self):
        card = _card(
            "Vendored claude mem as reference donor",
            "Vendored claude mem as reference donor",
            "Vendored claude mem as reference donor",
        )
        assert not _card_evidence_load_bearing(card)


class TestCardEvidenceIndicesValid:
    """Unit tests for _card_evidence_indices_valid (tier3 #29)."""

    def _card(self, indices: list) -> dict:
        return {"evidence_obs_indices": indices}

    def test_valid_index_within_range(self):
        assert _card_evidence_indices_valid(self._card([0, 1, 2]), window_count=5)

    def test_single_valid_index(self):
        assert _card_evidence_indices_valid(self._card([4]), window_count=5)

    def test_all_out_of_range(self):
        assert not _card_evidence_indices_valid(self._card([5, 99]), window_count=5)

    def test_empty_indices(self):
        assert not _card_evidence_indices_valid(self._card([]), window_count=5)

    def test_none_indices(self):
        assert not _card_evidence_indices_valid({}, window_count=5)

    def test_mixed_valid_and_invalid(self):
        # One valid index is enough to pass
        assert _card_evidence_indices_valid(self._card([0, 999]), window_count=3)

    def test_non_int_only_indices(self):
        # Strings and floats are not valid
        assert not _card_evidence_indices_valid(self._card(["0", 1.5]), window_count=5)

    def test_boundary_zero(self):
        assert _card_evidence_indices_valid(self._card([0]), window_count=1)

    def test_boundary_exactly_window_count_is_invalid(self):
        # window_count=3 → valid range [0, 2]; index 3 is out of range
        assert not _card_evidence_indices_valid(self._card([3]), window_count=3)

    def test_negative_index_invalid(self):
        assert not _card_evidence_indices_valid(self._card([-1]), window_count=5)
