# pyright: reportUnusedFunction=false
"""Tests for core/tags.py — two-tier tag vocabulary loader and classifier."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.tags import (
    classify_tag,
    is_tier1,
    load_vocabulary,
    render_for_prompt,
    validate_tags,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_YAML = textwrap.dedent("""\
    namespaces:
      technology:
        description: "Test technology namespace"
        values:
          - python
          - peewee
          - sqlite
      domain:
        description: "Test domain namespace"
        values:
          - database
          - retrieval
      scope:
        description: "Test scope namespace"
        values:
          - project-local
          - global
      severity:
        description: "Test severity namespace"
        values:
          - data-loss
          - correctness
""")


@pytest.fixture()
def vocab_path(tmp_path: Path) -> Path:
    """Write a minimal fixture YAML and return its path."""
    p = tmp_path / "tag_vocabulary.yaml"
    p.write_text(FIXTURE_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def vocab(vocab_path: Path) -> dict:
    """Return the loaded vocabulary from the fixture YAML."""
    return load_vocabulary(path=vocab_path)


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


def test_load_vocabulary_returns_expected_namespaces(vocab: dict) -> None:
    """Loader returns a dict with all four canonical namespaces."""
    namespaces = vocab.get("namespaces", {})
    assert set(namespaces.keys()) == {"technology", "domain", "scope", "severity"}


def test_load_vocabulary_technology_values(vocab: dict) -> None:
    """technology namespace has the seeded values."""
    values = vocab["namespaces"]["technology"]["values"]
    assert "python" in values
    assert "peewee" in values
    assert "sqlite" in values


def test_load_vocabulary_domain_values(vocab: dict) -> None:
    """domain namespace has expected values."""
    values = vocab["namespaces"]["domain"]["values"]
    assert "database" in values
    assert "retrieval" in values


def test_load_vocabulary_real_file_has_all_namespaces() -> None:
    """The real core/tag_vocabulary.yaml contains all four canonical namespaces."""
    real_vocab = load_vocabulary()
    namespaces = real_vocab.get("namespaces", {})
    for ns in ("technology", "domain", "scope", "severity"):
        assert ns in namespaces, f"Expected namespace {ns!r} missing from real vocabulary"


# ---------------------------------------------------------------------------
# is_tier1 tests
# ---------------------------------------------------------------------------


def test_is_tier1_known_value(vocab_path: Path) -> None:
    """technology:python is a valid tier-1 tag."""
    v = load_vocabulary(path=vocab_path)
    assert is_tier1("technology:python", vocab=v) is True


def test_is_tier1_off_list_value(vocab_path: Path) -> None:
    """technology:rust is not in the fixture list — returns False."""
    v = load_vocabulary(path=vocab_path)
    assert is_tier1("technology:rust", vocab=v) is False


def test_is_tier1_off_namespace(vocab_path: Path) -> None:
    """foo:bar uses an unknown namespace — returns False."""
    v = load_vocabulary(path=vocab_path)
    assert is_tier1("foo:bar", vocab=v) is False


def test_is_tier1_malformed_uppercase(vocab_path: Path) -> None:
    """technology:Python is malformed (uppercase) — returns False."""
    v = load_vocabulary(path=vocab_path)
    assert is_tier1("technology:Python", vocab=v) is False


def test_is_tier1_hyphenated_value(vocab_path: Path) -> None:
    """scope:project-local contains a hyphen — should be valid tier-1."""
    v = load_vocabulary(path=vocab_path)
    assert is_tier1("scope:project-local", vocab=v) is True


# ---------------------------------------------------------------------------
# classify_tag tests
# ---------------------------------------------------------------------------


def test_classify_tier1(vocab_path: Path) -> None:
    v = load_vocabulary(path=vocab_path)
    assert classify_tag("technology:python", vocab=v) == "tier1"


def test_classify_tier2_off_value(vocab_path: Path) -> None:
    """technology:rust is in a canonical namespace but off the value list."""
    v = load_vocabulary(path=vocab_path)
    assert classify_tag("technology:rust", vocab=v) == "tier2_off_value"


def test_classify_tier2_off_namespace(vocab_path: Path) -> None:
    """foo:bar uses a namespace not in the vocabulary."""
    v = load_vocabulary(path=vocab_path)
    assert classify_tag("foo:bar", vocab=v) == "tier2_off_namespace"


def test_classify_malformed_uppercase(vocab_path: Path) -> None:
    """technology:Python has an uppercase character in the value — malformed."""
    v = load_vocabulary(path=vocab_path)
    assert classify_tag("technology:Python", vocab=v) == "malformed"


def test_classify_malformed_empty_value(vocab_path: Path) -> None:
    """technology: has an empty value — malformed."""
    v = load_vocabulary(path=vocab_path)
    assert classify_tag("technology:", vocab=v) == "malformed"


def test_classify_malformed_no_colon(vocab_path: Path) -> None:
    """A tag with no colon separator is malformed."""
    v = load_vocabulary(path=vocab_path)
    assert classify_tag("plainword", vocab=v) == "malformed"


def test_classify_malformed_starts_with_digit(vocab_path: Path) -> None:
    """A value starting with a digit is malformed per the regex rule."""
    v = load_vocabulary(path=vocab_path)
    # namespace starts with digit
    assert classify_tag("1ns:value", vocab=v) == "malformed"


# ---------------------------------------------------------------------------
# validate_tags tests
# ---------------------------------------------------------------------------


def test_validate_tags_mixed(vocab_path: Path) -> None:
    """validate_tags with tier1, off-list value, and off-namespace tag.

    All three non-malformed tags should be accepted; the two tier-2 tags
    should appear in review_entries.
    """
    v = load_vocabulary(path=vocab_path)
    tags = ["technology:python", "technology:rust", "weird:thing"]
    accepted, review = validate_tags(tags, vocab=v)

    # All three accepted (none are malformed)
    assert len(accepted) == 3
    assert "technology:python" in accepted
    assert "technology:rust" in accepted
    assert "weird:thing" in accepted

    # Two review entries (tier-2 tags)
    assert len(review) == 2
    review_tags = [t for t, _ in review]
    assert "technology:rust" in review_tags
    assert "weird:thing" in review_tags

    # Classifications are correct
    review_dict = dict(review)
    assert review_dict["technology:rust"] == "tier2_off_value"
    assert review_dict["weird:thing"] == "tier2_off_namespace"


def test_validate_tags_malformed_dropped(vocab_path: Path) -> None:
    """Malformed tags are silently dropped — not in accepted_tags or review_entries."""
    v = load_vocabulary(path=vocab_path)
    tags = ["technology:Python", "technology:python", "bad"]
    accepted, review = validate_tags(tags, vocab=v)

    assert "technology:Python" not in accepted
    assert "bad" not in accepted
    assert "technology:python" in accepted
    # No review entry for a malformed tag
    review_tags = [t for t, _ in review]
    assert "technology:Python" not in review_tags


def test_validate_tags_all_tier1(vocab_path: Path) -> None:
    """All tier-1 tags → review_entries is empty."""
    v = load_vocabulary(path=vocab_path)
    tags = ["technology:python", "domain:database", "scope:global"]
    accepted, review = validate_tags(tags, vocab=v)
    assert len(accepted) == 3
    assert review == []


def test_validate_tags_empty_list(vocab_path: Path) -> None:
    """Empty input returns empty outputs."""
    v = load_vocabulary(path=vocab_path)
    accepted, review = validate_tags([], vocab=v)
    assert accepted == []
    assert review == []


# ---------------------------------------------------------------------------
# render_for_prompt tests
# ---------------------------------------------------------------------------


def test_render_for_prompt_contains_namespace_headers(vocab_path: Path) -> None:
    """render_for_prompt output includes each canonical namespace as a header."""
    v = load_vocabulary(path=vocab_path)
    rendered = render_for_prompt(vocab=v)

    for ns in ("technology", "domain", "scope", "severity"):
        assert f"`{ns}:`" in rendered, f"Expected `{ns}:` header in rendered prompt block"


def test_render_for_prompt_contains_values(vocab_path: Path) -> None:
    """render_for_prompt output includes at least one value from each namespace."""
    v = load_vocabulary(path=vocab_path)
    rendered = render_for_prompt(vocab=v)

    assert "python" in rendered
    assert "database" in rendered
    assert "project-local" in rendered
    assert "data-loss" in rendered


def test_render_for_prompt_includes_tier2_note(vocab_path: Path) -> None:
    """render_for_prompt reminds callers that new tags are accepted for review."""
    v = load_vocabulary(path=vocab_path)
    rendered = render_for_prompt(vocab=v)
    assert "reviewed later" in rendered
