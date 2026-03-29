"""
Tests for the crystallization engine — episodic -> semantic memory transformation.
"""

import json
import struct
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.crystallizer import Crystallizer, CRYSTALLIZATION_PROMPT
from core.database import init_db, close_db, get_vec_store
from core.lifecycle import LifecycleManager
from core.models import Memory, ConsolidationLog, db


@pytest.fixture
def base(tmp_path):
    base_dir = init_db(base_dir=str(tmp_path / "memory"))
    yield base_dir
    close_db()


@pytest.fixture
def lifecycle(base):
    return LifecycleManager()


@pytest.fixture
def crystallizer(base, lifecycle):
    return Crystallizer(lifecycle)


def _create_consolidated(title, content, tags=None, reinforcement_count=0):
    """Helper to create a consolidated memory with specific reinforcement count."""
    now = datetime.now().isoformat()
    mem = Memory.create(
        stage="consolidated",
        title=title,
        summary=content[:100],
        content=content,
        tags=json.dumps(tags or []),
        importance=0.65,
        reinforcement_count=reinforcement_count,
        created_at=now,
        updated_at=now,
    )
    return mem.id


# --- Candidate detection ---


def test_no_candidates_returns_empty(crystallizer):
    results = crystallizer.crystallize_candidates()
    assert results == []


def test_candidates_below_threshold_not_crystallized(crystallizer, base):
    _create_consolidated("Low Reinforcement", "Some content", reinforcement_count=2)
    results = crystallizer.crystallize_candidates()
    assert results == []


# --- Grouping ---


def _fake_mem(id, tags_json, title):
    """Create a fake memory-like object with tag_list property for grouping tests."""
    tags_list = json.loads(tags_json)
    ns = SimpleNamespace(
        id=id,
        tags=tags_json,
        title=title,
        content=f"Content for {title}",
        tag_list=tags_list,
    )
    return ns


def test_group_singletons(crystallizer, base):
    candidates = [
        _fake_mem("a", '["type:correction"]', "A"),
        _fake_mem("b", '["type:workflow_pattern"]', "B"),
    ]
    groups = crystallizer._group_candidates(candidates)
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_group_by_shared_type_and_tag(crystallizer):
    candidates = [
        _fake_mem("a", '["type:correction", "bedrock"]', "A"),
        _fake_mem("b", '["type:correction", "bedrock"]', "B"),
        _fake_mem("c", '["type:correction", "testing"]', "C"),
    ]
    groups = crystallizer._group_candidates(candidates)
    assert len(groups) == 2
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]


def test_group_transitive_clustering(crystallizer):
    candidates = [
        _fake_mem("a", '["type:correction", "aws"]', "A"),
        _fake_mem("b", '["type:correction", "aws", "sdk"]', "B"),
        _fake_mem("c", '["type:correction", "sdk"]', "C"),
    ]
    groups = crystallizer._group_candidates(candidates)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_group_ignores_source_backfill_tag(crystallizer):
    candidates = [
        _fake_mem("a", '["type:correction", "source:backfill"]', "A"),
        _fake_mem("b", '["type:correction", "source:backfill"]', "B"),
        _fake_mem("c", '["type:correction", "source:backfill"]', "C"),
    ]
    groups = crystallizer._group_candidates(candidates)
    assert len(groups) == 3


def test_group_no_tags(crystallizer):
    candidates = [
        _fake_mem("a", "[]", "A"),
        _fake_mem("b", "[]", "B"),
        _fake_mem("c", "[]", "C"),
    ]
    groups = crystallizer._group_candidates(candidates)
    assert len(groups) == 3


def test_group_two_candidates_always_singletons(crystallizer):
    candidates = [
        _fake_mem("a", '["type:correction", "aws"]', "A"),
        _fake_mem("b", '["type:correction", "aws"]', "B"),
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


@patch("core.crystallizer.call_llm")
def test_crystallize_single_candidate(mock_llm, crystallizer, base):
    mock_llm.return_value = json.dumps(MOCK_LLM_RESPONSE)

    mem_id = _create_consolidated(
        "Bedrock Client", "Use AnthropicBedrock() not Anthropic()",
        tags=["type:correction", "bedrock"], reinforcement_count=3,
    )

    results = crystallizer.crystallize_candidates()
    assert len(results) == 1
    assert results[0]["title"] == "Bedrock SDK diverges at every interface point"
    assert results[0]["group_size"] == 1

    source = Memory.get_by_id(mem_id)
    assert source.archived_at is not None

    crystal = Memory.get_by_id(results[0]["crystallized_id"])
    assert crystal.stage == "crystallized"
    assert "diverges at every surface" in crystal.content


@patch("core.crystallizer.call_llm")
def test_crystallize_group(mock_llm, crystallizer, base):
    mock_llm.return_value = json.dumps(MOCK_LLM_RESPONSE)

    ids = [
        _create_consolidated(
            f"Bedrock Issue {i}", f"Bedrock problem #{i}",
            tags=["type:correction", "bedrock"], reinforcement_count=3,
        )
        for i in range(3)
    ]

    results = crystallizer.crystallize_candidates()
    assert len(results) == 1
    assert results[0]["group_size"] == 3
    assert len(results[0]["source_ids"]) == 3

    for mem_id in ids:
        source = Memory.get_by_id(mem_id)
        assert source.archived_at is not None


@patch("core.crystallizer.call_llm")
def test_crystallize_preserves_importance(mock_llm, crystallizer, base):
    mock_llm.return_value = json.dumps(MOCK_LLM_RESPONSE)
    _create_consolidated("Test", "Content", tags=["type:correction"], reinforcement_count=3)

    results = crystallizer.crystallize_candidates()
    crystal = Memory.get_by_id(results[0]["crystallized_id"])
    assert crystal.importance == 0.75


@patch("core.crystallizer.call_llm")
def test_crystallize_tags_include_source_marker(mock_llm, crystallizer, base):
    mock_llm.return_value = json.dumps(MOCK_LLM_RESPONSE)
    _create_consolidated("Test", "Content", tags=["type:correction"], reinforcement_count=3)

    results = crystallizer.crystallize_candidates()
    crystal = Memory.get_by_id(results[0]["crystallized_id"])
    tags = crystal.tag_list
    assert "source:crystallization" in tags


@patch("core.crystallizer.call_llm")
def test_crystallize_logs_subsumed_action(mock_llm, crystallizer, base):
    mock_llm.return_value = json.dumps(MOCK_LLM_RESPONSE)
    mem_id = _create_consolidated("Test", "Content", tags=["type:correction"], reinforcement_count=3)

    crystallizer.crystallize_candidates()

    rows = list(
        ConsolidationLog.select().where(ConsolidationLog.memory_id == mem_id)
    )
    actions = [r.action for r in rows]
    assert "subsumed" in actions


# --- Fallback ---


@patch("core.llm.call_llm", side_effect=Exception("LLM unavailable"))
def test_fallback_on_llm_failure(mock_llm, crystallizer, base):
    mem_id = _create_consolidated("Test", "Content", tags=["type:correction"], reinforcement_count=3)

    results = crystallizer.crystallize_candidates()
    assert len(results) == 1
    assert results[0]["insight"] == "(fallback — no synthesis)"

    mem = Memory.get_by_id(mem_id)
    assert mem.stage == "crystallized"


# --- Prompt ---


def test_crystallization_prompt_has_required_placeholders():
    assert "{observations}" in CRYSTALLIZATION_PROMPT


def test_crystallization_prompt_requests_json():
    assert "Respond ONLY with valid JSON" in CRYSTALLIZATION_PROMPT


# --- Integration with pre_compact ---


@patch("core.crystallizer.call_llm")
def test_mixed_candidates_produce_separate_crystallizations(mock_llm, crystallizer, base):
    mock_llm.side_effect = [
        json.dumps({
            "title": "Correction pattern",
            "insight": "A correction insight",
            "observation_type": "correction",
            "tags": ["testing"],
            "source_pattern": "Testing corrections",
        }),
        json.dumps({
            "title": "Workflow pattern",
            "insight": "A workflow insight",
            "observation_type": "workflow_pattern",
            "tags": ["pr"],
            "source_pattern": "PR patterns",
        }),
    ]

    _create_consolidated(
        "Test Correction", "Correction content",
        tags=["type:correction", "testing"], reinforcement_count=3,
    )
    _create_consolidated(
        "PR Workflow", "Workflow content",
        tags=["type:workflow_pattern", "pr"], reinforcement_count=3,
    )
    _create_consolidated(
        "Another Correction", "More correction",
        tags=["type:correction", "testing"], reinforcement_count=3,
    )

    results = crystallizer.crystallize_candidates()
    assert len(results) == 2
    titles = {r["title"] for r in results}
    assert "Correction pattern" in titles
    assert "Workflow pattern" in titles


# ---------------------------------------------------------------------------
# Embedding helpers (deterministic, no real model loaded)
# ---------------------------------------------------------------------------


def _fake_embeddings(n, dim=384, seed=42):
    rng = np.random.default_rng(seed)
    raw = rng.standard_normal((n, dim))
    norms = np.linalg.norm(raw, axis=1, keepdims=True)
    return raw / np.maximum(norms, 1e-9)


def _cluster_embeddings(n, cluster_size=2):
    rng = np.random.default_rng(0)
    base = rng.standard_normal(384)
    base /= np.linalg.norm(base)
    similar = base + rng.standard_normal((cluster_size, 384)) * 0.01
    dissimilar = rng.standard_normal((n - cluster_size, 384))
    all_vecs = np.vstack([similar, dissimilar])
    norms = np.linalg.norm(all_vecs, axis=1, keepdims=True)
    return all_vecs / np.maximum(norms, 1e-9)


def _make_embedding_bytes(values):
    return struct.pack(f"{len(values)}f", *values)


# ---------------------------------------------------------------------------
# Embedding-based grouping (D-09, D-10)
# ---------------------------------------------------------------------------


class TestEmbeddingGrouping:

    def _make_candidates(self, crystallizer, n=3, tags=None):
        candidates = []
        for i in range(n):
            mem_tags = tags or ["type:correction", f"topic{i}"]
            mem_id = _create_consolidated(
                title=f"Candidate {i}",
                content=f"Content for candidate {i}",
                tags=mem_tags,
                reinforcement_count=3,
            )
            mem = Memory.get_by_id(mem_id)
            candidates.append(mem)
        return candidates

    def test_similar_candidates_grouped_by_embeddings(self, crystallizer, base):
        candidates = self._make_candidates(crystallizer, n=3)
        embeddings = _cluster_embeddings(3, cluster_size=2)

        id_to_bytes = {
            c.id: _make_embedding_bytes(embeddings[i].tolist())
            for i, c in enumerate(candidates)
        }

        vec = get_vec_store()
        with patch.object(vec, "get_embedding", side_effect=lambda mid: id_to_bytes.get(mid)):
            groups = crystallizer._group_candidates(candidates)

        group_sets = [frozenset(c.id for c in g) for g in groups]
        similar_ids = frozenset(c.id for c in candidates[:2])
        assert any(similar_ids <= gs for gs in group_sets)

    def test_dissimilar_candidates_not_grouped(self, crystallizer, base):
        candidates = self._make_candidates(crystallizer, n=3)
        embeddings = _fake_embeddings(3)

        id_to_bytes = {
            c.id: _make_embedding_bytes(embeddings[i].tolist())
            for i, c in enumerate(candidates)
        }

        vec = get_vec_store()
        with patch.object(vec, "get_embedding", side_effect=lambda mid: id_to_bytes.get(mid)):
            groups = crystallizer._group_candidates(candidates)

        assert len(groups) == 3
        assert all(len(g) == 1 for g in groups)

    def test_embedding_fallback_when_unavailable(self, crystallizer, base):
        ids = [
            _create_consolidated(
                f"Tagged {i}", f"Content {i}",
                tags=["type:correction", "shared-tag"] if i < 2 else ["type:correction", "other-tag"],
                reinforcement_count=3,
            )
            for i in range(3)
        ]
        candidates = [Memory.get_by_id(mid) for mid in ids]

        vec = get_vec_store()
        with patch.object(vec, "get_embedding", return_value=None):
            groups = crystallizer._group_candidates(candidates)

        group_sets = [frozenset(c.id for c in g) for g in groups]
        shared_ids = frozenset([ids[0], ids[1]])
        assert any(shared_ids <= gs for gs in group_sets)
