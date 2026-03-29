"""
Eval 2: Cross-Session Continuity

Simulates two sessions sharing the same base_dir:

  Session A — creates a memory, promotes it to crystallized via 3 reinforcements.
  Session B — opens a new MemoryStore on the same base_dir and injects for session.

The memory created in Session A must surface in Session B's injected context.

Target score: 80%+ of important cross-session memories survive the boundary.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.lifecycle import LifecycleManager
from core.retrieval import RetrievalEngine
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Synthetic session-A memory definitions
# ---------------------------------------------------------------------------

SESSION_A_DECISION = {
    "path": "decision_architecture.md",
    "title": "Architecture Decision: Event-Driven Microservices",
    "content": (
        "Decision recorded in Session A: We will migrate to an event-driven "
        "microservices architecture using Kafka as the message broker. "
        "Reasoning: current monolith has deployment bottlenecks; Kafka provides "
        "durable replay and decoupled scaling. Owner: platform-team."
    ),
    "summary": "Event-driven microservices via Kafka; owner: platform-team",
    "importance": 0.9,
    "tags": ["architecture", "decision", "kafka"],
}

SESSION_A_REASONING = {
    "path": "reasoning_test_strategy.md",
    "title": "Test Strategy Reasoning: Integration Over Unit",
    "content": (
        "Decision recorded in Session A: Prefer integration tests over unit tests "
        "for service boundaries. Reasoning: unit tests alone miss contract violations "
        "between services. Chosen framework: RSpec + WebMock for external calls."
    ),
    "summary": "Integration tests preferred for service boundaries; RSpec + WebMock",
    "importance": 0.85,
    "tags": ["testing", "decision", "rspec"],
}

# Unique tokens for deterministic assertion
DECISION_TOKEN = "event-driven microservices architecture using Kafka"
REASONING_TOKEN = "integration tests over unit tests"


def _make_crystallized(store: MemoryStore, spec: dict) -> str:
    """
    Create a memory, then promote it to crystallized by setting
    reinforcement_count to 3 and calling LifecycleManager.promote().
    """
    # Start in consolidated (required stage before crystallized)
    memory_id = store.create(
        path=spec["path"],
        content=spec["content"],
        metadata={
            "stage": "consolidated",
            "title": spec["title"],
            "summary": spec["summary"],
            "importance": spec["importance"],
            "tags": spec["tags"],
        },
    )

    # Set reinforcement_count to 3 so promote() passes validation
    store.update(memory_id, metadata={"reinforcement_count": 3})

    lifecycle = LifecycleManager(store)
    lifecycle.promote(memory_id, rationale="Promoted by eval seeder: 3 reinforcements")

    return memory_id


@pytest.fixture
def session_a_base_dir(tmp_path):
    """Return a stable base_dir path shared across both sessions."""
    return str(tmp_path / "shared_memory")


@pytest.fixture
def session_a_memory_ids(session_a_base_dir):
    """Session A: create and promote two decisions to crystallized."""
    store_a = MemoryStore(base_dir=session_a_base_dir)
    decision_id = _make_crystallized(store_a, SESSION_A_DECISION)
    reasoning_id = _make_crystallized(store_a, SESSION_A_REASONING)
    return {"decision": decision_id, "reasoning": reasoning_id}


@pytest.fixture
def session_b_context(session_a_base_dir, session_a_memory_ids):
    """
    Session B: fresh MemoryStore from the same base_dir, inject for session.
    """
    store_b = MemoryStore(base_dir=session_a_base_dir)
    engine_b = RetrievalEngine(store_b)
    return engine_b.inject_for_session(session_id="session_b_continuity_eval")


def test_decision_survives_session_boundary(session_b_context):
    """
    The architecture decision from Session A must appear in Session B's context.
    """
    assert DECISION_TOKEN in session_b_context, (
        f"Decision token '{DECISION_TOKEN}' not found in Session B context.\n\n"
        f"Context (first 1000 chars):\n{session_b_context[:1000]}"
    )


def test_reasoning_preserved_in_continuity(session_b_context):
    """
    The test strategy reasoning from Session A must appear in Session B's context.
    """
    assert REASONING_TOKEN in session_b_context, (
        f"Reasoning token '{REASONING_TOKEN}' not found in Session B context.\n\n"
        f"Context (first 1000 chars):\n{session_b_context[:1000]}"
    )


def test_session_b_store_sees_crystallized_memories(session_a_base_dir, session_a_memory_ids):
    """
    A fresh MemoryStore opened on the shared base_dir should list both memories
    in the crystallized stage.
    """
    store_b = MemoryStore(base_dir=session_a_base_dir)
    crystallized = store_b.list_by_stage("crystallized")
    crystallized_ids = {m["id"] for m in crystallized}

    assert session_a_memory_ids["decision"] in crystallized_ids, (
        "Decision memory not found in crystallized stage on Session B store."
    )
    assert session_a_memory_ids["reasoning"] in crystallized_ids, (
        "Reasoning memory not found in crystallized stage on Session B store."
    )


def test_continuity_score_above_threshold(session_b_context):
    """
    Continuity score: both memories present = 100%, one present = 50%.
    Must be >= 80%.
    """
    tokens = [DECISION_TOKEN, REASONING_TOKEN]
    found = sum(1 for t in tokens if t in session_b_context)
    score = found / len(tokens)

    assert score >= 0.80, (
        f"Continuity score {score:.0%} is below 80% threshold. "
        f"Found {found}/{len(tokens)} memories."
    )


def test_promoted_memory_carries_correct_metadata(session_a_base_dir, session_a_memory_ids):
    """
    After promotion, memories should have reinforcement_count >= 3 and be in crystallized.
    """
    store = MemoryStore(base_dir=session_a_base_dir)
    for key, memory_id in session_a_memory_ids.items():
        mem = store.get(memory_id)
        assert mem["stage"] == "crystallized", (
            f"Memory '{key}' (id={memory_id}) is in stage '{mem['stage']}', expected 'crystallized'."
        )
        assert mem["reinforcement_count"] >= 3, (
            f"Memory '{key}' has reinforcement_count={mem['reinforcement_count']}, expected >= 3."
        )
