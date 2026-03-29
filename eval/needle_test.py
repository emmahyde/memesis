"""
Eval 1: Needle-in-the-Memory

Plants 3 unusual "needle" facts in the crystallized stage, then verifies that
inject_for_session() surfaces all of them in the injected context string.

Target score: needles_found / total_needles >= 85% (3/3 expected).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.retrieval import RetrievalEngine
from core.storage import MemoryStore
from eval.conftest import seed_store


# ---------------------------------------------------------------------------
# Needle definitions — deliberately unusual facts to avoid false positives
# ---------------------------------------------------------------------------

NEEDLES = [
    {
        "path": "needle_xzibit_fact.md",
        "title": "Needle Fact: Xzibit Cache Invalidation",
        "content": (
            "The cache invalidation key is always prefixed with XZIBIT_CACHE_NEEDLE_TOKEN_7734."
        ),
        "summary": "Unusual cache key prefix XZIBIT_CACHE_NEEDLE_TOKEN_7734",
        "importance": 0.9,
        "unique_token": "XZIBIT_CACHE_NEEDLE_TOKEN_7734",
    },
    {
        "path": "needle_starfish_endpoint.md",
        "title": "Needle Fact: Starfish Internal Endpoint",
        "content": (
            "Internal health-check endpoint is /_internal/starfish-heartbeat-zq99."
        ),
        "summary": "Health check at /_internal/starfish-heartbeat-zq99",
        "importance": 0.88,
        "unique_token": "starfish-heartbeat-zq99",
    },
    {
        "path": "needle_quorum_constant.md",
        "title": "Needle Fact: Quorum Magic Constant",
        "content": (
            "Distributed quorum algorithm uses constant QUORUM_MAGIC_PELICAN_42 for tie-breaking."
        ),
        "summary": "Quorum tie-break constant QUORUM_MAGIC_PELICAN_42",
        "importance": 0.91,
        "unique_token": "QUORUM_MAGIC_PELICAN_42",
    },
]


@pytest.fixture
def needle_store(tmp_path):
    """Store pre-seeded with background noise + 3 needle memories."""
    store = MemoryStore(base_dir=str(tmp_path / "needle_memory"))
    seed_store(store)  # 20 background memories

    # Plant all 3 needles in crystallized stage
    for needle in NEEDLES:
        store.create(
            path=needle["path"],
            content=needle["content"],
            metadata={
                "stage": "crystallized",
                "title": needle["title"],
                "summary": needle["summary"],
                "importance": needle["importance"],
                "tags": ["needle", "eval"],
            },
        )

    return store


@pytest.fixture
def needle_context(needle_store):
    """Pre-built injection context for needle tests."""
    engine = RetrievalEngine(needle_store)
    return engine.inject_for_session(session_id="needle_eval_session")


def _count_needles_in_context(context: str) -> int:
    """Return how many needle unique tokens appear in the context string."""
    return sum(1 for needle in NEEDLES if needle["unique_token"] in context)


def test_needle_retrieved_via_injection(needle_context):
    """Each needle fact should appear somewhere in the injected context block."""
    missing = [
        needle["unique_token"]
        for needle in NEEDLES
        if needle["unique_token"] not in needle_context
    ]
    assert not missing, (
        f"The following needle tokens were NOT found in injected context: {missing}\n\n"
        f"Context (first 800 chars):\n{needle_context[:800]}"
    )


def test_needle_score_above_threshold(needle_context):
    """Needle retrieval score must reach at least 85% (rounded up to 3/3 here)."""
    total = len(NEEDLES)
    found = _count_needles_in_context(needle_context)
    score = found / total
    threshold = 0.85

    assert score >= threshold, (
        f"Needle retrieval score {score:.0%} is below {threshold:.0%} threshold. "
        f"Found {found}/{total} needles."
    )


def test_needle_context_contains_memory_block(needle_context):
    """Sanity: injected context is wrapped in the expected delimiters."""
    assert "---MEMORY CONTEXT---" in needle_context
    assert "---END MEMORY CONTEXT---" in needle_context


def test_needle_background_memories_do_not_cause_false_positives(needle_store):
    """
    Verify that none of the background memory contents contain any needle token,
    ensuring needle detection is clean.
    """
    for needle in NEEDLES:
        for stage in ("ephemeral", "consolidated", "crystallized", "instinctive"):
            for record in needle_store.list_by_stage(stage):
                if needle["unique_token"] in (record.get("title") or ""):
                    continue  # Skip needle memories themselves
                mem = needle_store.get(record["id"])
                body = mem.get("content", "") or ""
                # Background memories should not contain needle tokens
                # (unless they are the needle memories we just planted)
                if record.get("title", "").startswith("Needle Fact:"):
                    continue
                assert needle["unique_token"] not in body, (
                    f"Needle token '{needle['unique_token']}' found in non-needle "
                    f"memory '{record['title']}' — test data contamination."
                )
