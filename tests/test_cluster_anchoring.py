"""Tests for cluster anchoring — core/graph.py:expand_clusters."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.database import close_db, init_db
from core.graph import expand_clusters
from core.models import Memory


@pytest.fixture
def db(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield
    close_db()


def _mem(cluster=None, archived=False) -> Memory:
    return Memory.create(
        stage="consolidated",
        title="t",
        content="c",
        cluster=cluster,
        archived_at="2026-01-01T00:00:00" if archived else None,
    )


def test_surfacing_one_member_pulls_in_siblings(db):
    a1 = _mem(cluster="consolidator-invariants")
    a2 = _mem(cluster="consolidator-invariants")
    a3 = _mem(cluster="consolidator-invariants")
    b1 = _mem(cluster="numpy-knn")

    siblings = expand_clusters([a1.id])

    assert set(siblings) == {a2.id, a3.id}
    assert a1.id not in siblings  # seed itself excluded
    assert b1.id not in siblings  # other cluster excluded


def test_no_cluster_on_seed_returns_empty(db):
    m = _mem(cluster=None)
    _mem(cluster="some-cluster")
    assert expand_clusters([m.id]) == []


def test_empty_seeds_returns_empty(db):
    assert expand_clusters([]) == []


def test_archived_siblings_excluded(db):
    a1 = _mem(cluster="reflection-system")
    live = _mem(cluster="reflection-system")
    _mem(cluster="reflection-system", archived=True)

    siblings = expand_clusters([a1.id])
    assert siblings == [live.id]


def test_max_expansion_bounds_result(db):
    seed = _mem(cluster="big")
    for _ in range(8):
        _mem(cluster="big")
    assert len(expand_clusters([seed.id], max_expansion=3)) == 3


def test_gated_off_when_graph_expansion_flag_false(db):
    a1 = _mem(cluster="x")
    _mem(cluster="x")
    with patch("core.graph.get_flag", return_value=False):
        assert expand_clusters([a1.id]) == []


def test_multiple_seed_clusters_union(db):
    a1 = _mem(cluster="alpha")
    a2 = _mem(cluster="alpha")
    b1 = _mem(cluster="beta")
    b2 = _mem(cluster="beta")

    siblings = set(expand_clusters([a1.id, b1.id]))
    assert siblings == {a2.id, b2.id}
