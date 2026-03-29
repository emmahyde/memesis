"""
Tests for the crystallization engine — episodic → semantic memory transformation.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.crystallizer import Crystallizer, CRYSTALLIZATION_PROMPT
from core.lifecycle import LifecycleManager
from core.storage import MemoryStore


@pytest.fixture
def store(tmp_path):
    """Create a temporary MemoryStore."""
    return MemoryStore(base_dir=str(tmp_path / "memory"))


@pytest.fixture
def lifecycle(store):
    return LifecycleManager(store)


@pytest.fixture
def crystallizer(store, lifecycle):
    return Crystallizer(store, lifecycle)


def _create_consolidated(store, title, content, tags=None, reinforcement_count=0):
    """Helper to create a consolidated memory with specific reinforcement count."""
    mem_id = store.create(
        path=f"test/{title.lower().replace(' ', '_')}.md",
        content=content,
        metadata={
            "stage": "consolidated",
            "title": title,
            "summary": content[:100],
            "tags": tags or [],
            "importance": 0.65,
        },
    )
    if reinforcement_count > 0:
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE memories SET reinforcement_count = ? WHERE id = ?",
                (reinforcement_count, mem_id),
            )
    return mem_id


# --- Candidate detection ---


def test_no_candidates_returns_empty(crystallizer):
    """No promotion candidates → no crystallization."""
    results = crystallizer.crystallize_candidates()
    assert results == []


def test_candidates_below_threshold_not_crystallized(crystallizer, store):
    """Memories with < 3 reinforcements are not candidates."""
    _create_consolidated(store, "Low Reinforcement", "Some content", reinforcement_count=2)
    results = crystallizer.crystallize_candidates()
    assert results == []


# --- Grouping ---


def test_group_singletons(crystallizer, store):
    """Single candidates form singleton groups."""
    candidates = [
        {"id": "a", "tags": '["type:correction"]', "title": "A"},
        {"id": "b", "tags": '["type:workflow_pattern"]', "title": "B"},
    ]
    groups = crystallizer._group_candidates(candidates)
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_group_by_shared_type_and_tag(crystallizer):
    """Memories with same type AND shared non-type tag group together."""
    candidates = [
        {"id": "a", "tags": '["type:correction", "bedrock"]', "title": "A"},
        {"id": "b", "tags": '["type:correction", "bedrock"]', "title": "B"},
        {"id": "c", "tags": '["type:correction", "testing"]', "title": "C"},
    ]
    groups = crystallizer._group_candidates(candidates)
    # a and b share "bedrock" tag → grouped. c is separate.
    assert len(groups) == 2
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]


def test_group_transitive_clustering(crystallizer):
    """Tags expand transitively — if A-B share tag1 and B-C share tag2, all group."""
    candidates = [
        {"id": "a", "tags": '["type:correction", "aws"]', "title": "A"},
        {"id": "b", "tags": '["type:correction", "aws", "sdk"]', "title": "B"},
        {"id": "c", "tags": '["type:correction", "sdk"]', "title": "C"},
    ]
    groups = crystallizer._group_candidates(candidates)
    # a→b via "aws", b→c via "sdk" → all one group
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_group_ignores_source_backfill_tag(crystallizer):
    """The 'source:backfill' tag doesn't count for clustering."""
    candidates = [
        {"id": "a", "tags": '["type:correction", "source:backfill"]', "title": "A"},
        {"id": "b", "tags": '["type:correction", "source:backfill"]', "title": "B"},
        {"id": "c", "tags": '["type:correction", "source:backfill"]', "title": "C"},
    ]
    groups = crystallizer._group_candidates(candidates)
    # source:backfill is excluded from overlap check → 3 singletons
    assert len(groups) == 3


def test_group_no_tags(crystallizer):
    """Memories without tags form singleton groups."""
    candidates = [
        {"id": "a", "tags": "[]", "title": "A"},
        {"id": "b", "tags": "[]", "title": "B"},
        {"id": "c", "tags": "[]", "title": "C"},
    ]
    groups = crystallizer._group_candidates(candidates)
    assert len(groups) == 3


def test_group_two_candidates_always_singletons(crystallizer):
    """With 2 or fewer candidates, each forms its own group (not enough to cluster)."""
    candidates = [
        {"id": "a", "tags": '["type:correction", "aws"]', "title": "A"},
        {"id": "b", "tags": '["type:correction", "aws"]', "title": "B"},
    ]
    groups = crystallizer._group_candidates(candidates)
    assert len(groups) == 2


# --- Synthesis (with mocked LLM) ---


MOCK_LLM_RESPONSE = {
    "title": "Bedrock SDK diverges at every interface point",
    "insight": "AWS Bedrock wraps the Anthropic API but diverges at every surface: client class, model IDs, and feature set. Treat each as potentially different.",
    "observation_type": "correction",
    "tags": ["aws", "bedrock", "sdk"],
    "source_pattern": "Multiple corrections about Bedrock-specific API differences",
}


@patch("core.crystallizer._call_llm")
def test_crystallize_single_candidate(mock_llm, crystallizer, store):
    """Single candidate still gets synthesized (episodic → semantic)."""
    mock_llm.return_value = MOCK_LLM_RESPONSE

    mem_id = _create_consolidated(
        store, "Bedrock Client", "Use AnthropicBedrock() not Anthropic()",
        tags=["type:correction", "bedrock"], reinforcement_count=3,
    )

    results = crystallizer.crystallize_candidates()
    assert len(results) == 1
    assert results[0]["title"] == "Bedrock SDK diverges at every interface point"
    assert results[0]["group_size"] == 1

    # Source should be archived
    source = store.get(mem_id)
    assert source["archived_at"] is not None

    # Crystallized memory should exist
    crystal = store.get(results[0]["crystallized_id"])
    assert crystal["stage"] == "crystallized"
    assert "diverges at every surface" in crystal["content"]


@patch("core.crystallizer._call_llm")
def test_crystallize_group(mock_llm, crystallizer, store):
    """Multiple related candidates get synthesized into one insight."""
    mock_llm.return_value = MOCK_LLM_RESPONSE

    ids = [
        _create_consolidated(
            store, f"Bedrock Issue {i}", f"Bedrock problem #{i}",
            tags=["type:correction", "bedrock"], reinforcement_count=3,
        )
        for i in range(3)
    ]

    results = crystallizer.crystallize_candidates()
    assert len(results) == 1
    assert results[0]["group_size"] == 3
    assert len(results[0]["source_ids"]) == 3

    # All sources archived
    for mem_id in ids:
        source = store.get(mem_id)
        assert source["archived_at"] is not None


@patch("core.crystallizer._call_llm")
def test_crystallize_preserves_importance(mock_llm, crystallizer, store):
    """Crystallized memories start at 0.75 importance."""
    mock_llm.return_value = MOCK_LLM_RESPONSE

    _create_consolidated(
        store, "Test", "Content",
        tags=["type:correction"], reinforcement_count=3,
    )

    results = crystallizer.crystallize_candidates()
    crystal = store.get(results[0]["crystallized_id"])
    assert crystal["importance"] == 0.75


@patch("core.crystallizer._call_llm")
def test_crystallize_tags_include_source_marker(mock_llm, crystallizer, store):
    """Crystallized memories are tagged with source:crystallization."""
    mock_llm.return_value = MOCK_LLM_RESPONSE

    _create_consolidated(
        store, "Test", "Content",
        tags=["type:correction"], reinforcement_count=3,
    )

    results = crystallizer.crystallize_candidates()
    crystal = store.get(results[0]["crystallized_id"])
    tags = json.loads(crystal["tags"]) if isinstance(crystal["tags"], str) else crystal["tags"]
    assert "source:crystallization" in tags


@patch("core.crystallizer._call_llm")
def test_crystallize_logs_subsumed_action(mock_llm, crystallizer, store):
    """Source memories get a 'subsumed' consolidation log entry."""
    mock_llm.return_value = MOCK_LLM_RESPONSE

    mem_id = _create_consolidated(
        store, "Test", "Content",
        tags=["type:correction"], reinforcement_count=3,
    )

    crystallizer.crystallize_candidates()

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT action, rationale FROM consolidation_log WHERE memory_id = ?",
            (mem_id,),
        ).fetchall()
    actions = [r[0] for r in rows]
    assert "subsumed" in actions


# --- Fallback ---


@patch("core.crystallizer._call_llm", side_effect=Exception("LLM unavailable"))
def test_fallback_on_llm_failure(mock_llm, crystallizer, store):
    """LLM failure falls back to simple promotion."""
    mem_id = _create_consolidated(
        store, "Test", "Content",
        tags=["type:correction"], reinforcement_count=3,
    )

    results = crystallizer.crystallize_candidates()
    assert len(results) == 1
    assert results[0]["insight"] == "(fallback — no synthesis)"

    # Memory should be promoted (not archived)
    mem = store.get(mem_id)
    assert mem["stage"] == "crystallized"


# --- Prompt ---


def test_crystallization_prompt_has_required_placeholders():
    """Prompt template contains the observations placeholder."""
    assert "{observations}" in CRYSTALLIZATION_PROMPT


def test_crystallization_prompt_requests_json():
    """Prompt asks for JSON response."""
    assert "Respond ONLY with valid JSON" in CRYSTALLIZATION_PROMPT


# --- Integration with pre_compact ---


@patch("core.crystallizer._call_llm")
def test_mixed_candidates_produce_separate_crystallizations(mock_llm, crystallizer, store):
    """Candidates from different observation types crystallize separately."""
    mock_llm.side_effect = [
        {
            "title": "Correction pattern",
            "insight": "A correction insight",
            "observation_type": "correction",
            "tags": ["testing"],
            "source_pattern": "Testing corrections",
        },
        {
            "title": "Workflow pattern",
            "insight": "A workflow insight",
            "observation_type": "workflow_pattern",
            "tags": ["pr"],
            "source_pattern": "PR patterns",
        },
    ]

    _create_consolidated(
        store, "Test Correction", "Correction content",
        tags=["type:correction", "testing"], reinforcement_count=3,
    )
    _create_consolidated(
        store, "PR Workflow", "Workflow content",
        tags=["type:workflow_pattern", "pr"], reinforcement_count=3,
    )
    # Need a third for clustering to kick in
    _create_consolidated(
        store, "Another Correction", "More correction",
        tags=["type:correction", "testing"], reinforcement_count=3,
    )

    results = crystallizer.crystallize_candidates()
    # Two corrections group together, workflow is separate → 2 crystallizations
    assert len(results) == 2
    titles = {r["title"] for r in results}
    assert "Correction pattern" in titles
    assert "Workflow pattern" in titles
