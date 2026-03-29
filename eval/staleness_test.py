"""
Eval 5: Memory Staleness

Creates a memory with an outdated fact, updates it with a new fact, injects
for session, and verifies that only the new fact appears while the stale one
does not.

No LLM calls required.

Target: stale content injection rate < 10% (i.e., the old fact must NOT appear
in the injected context after an update).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
from core.database import init_db, close_db
from core.models import Memory
from core.retrieval import RetrievalEngine


# ---------------------------------------------------------------------------
# Staleness scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    {
        "name": "api_endpoint_version",
        "path": "crystal_api_endpoint.md",
        "title": "API Endpoint Version",
        "summary": "Current API base path",
        "importance": 0.88,
        "tags": ["api", "endpoint"],
        "stale_content": "API endpoint is /v1/users",
        "fresh_content": "API endpoint changed to /v2/users",
        "stale_token": "/v1/users",
        "fresh_token": "/v2/users",
    },
    {
        "name": "deployment_host",
        "path": "crystal_deploy_host.md",
        "title": "Deployment Host",
        "summary": "Production deployment hostname",
        "importance": 0.85,
        "tags": ["deploy", "infrastructure"],
        "stale_content": "Production host: legacy-prod-server.internal",
        "fresh_content": "Production host: k8s-cluster.prod.internal",
        "stale_token": "legacy-prod-server.internal",
        "fresh_token": "k8s-cluster.prod.internal",
    },
    {
        "name": "auth_method",
        "path": "crystal_auth_method.md",
        "title": "Authentication Method",
        "summary": "Current auth mechanism",
        "importance": 0.9,
        "tags": ["auth", "security"],
        "stale_content": "Auth method: HTTP Basic Authentication with username/password",
        "fresh_content": "Auth method: Bearer tokens via OAuth 2.0 PKCE flow",
        "stale_token": "HTTP Basic Authentication",
        "fresh_token": "OAuth 2.0 PKCE flow",
    },
]


def _make_updated_memory(scenario: dict) -> str:
    """
    Create a memory with stale_content, then update it to fresh_content.
    Returns the memory_id.
    """
    mem = Memory.create(
        stage="crystallized",
        title=scenario["title"],
        summary=scenario["summary"],
        content=scenario["stale_content"],
        importance=scenario["importance"],
        tags=json.dumps(scenario["tags"]),
    )

    # Update to fresh content
    mem.content = scenario["fresh_content"]
    mem.save()
    return mem.id


@pytest.fixture
def staleness_store(tmp_path):
    """Database with 3 updated memories (each updated from stale to fresh content)."""
    init_db(base_dir=str(tmp_path / "staleness_memory"))
    for scenario in SCENARIOS:
        _make_updated_memory(scenario)
    yield tmp_path / "staleness_memory"
    close_db()


@pytest.fixture
def staleness_context(staleness_store):
    """Injected context from a database that only has fresh memory content."""
    engine = RetrievalEngine()
    return engine.inject_for_session(session_id="staleness_eval_session")


def test_updated_memory_supersedes_old(staleness_context):
    """
    After update, the fresh content should be present in injected context
    for every scenario.
    """
    missing_fresh = [
        s["name"]
        for s in SCENARIOS
        if s["fresh_token"] not in staleness_context
    ]
    assert not missing_fresh, (
        f"Fresh content tokens not found for scenarios: {missing_fresh}\n\n"
        f"Context (first 1000 chars):\n{staleness_context[:1000]}"
    )


def test_stale_content_not_injected(staleness_context):
    """
    Stale content must NOT appear in the injected context after an update.
    """
    found_stale = [
        s["name"]
        for s in SCENARIOS
        if s["stale_token"] in staleness_context
    ]
    assert not found_stale, (
        f"Stale content tokens found in injected context for scenarios: {found_stale}\n\n"
        f"Context (first 1000 chars):\n{staleness_context[:1000]}"
    )


def test_staleness_injection_rate_below_threshold(staleness_context):
    """
    Staleness injection rate = stale_tokens_found / total_scenarios.
    Must be < 10%.
    """
    total = len(SCENARIOS)
    stale_found = sum(1 for s in SCENARIOS if s["stale_token"] in staleness_context)
    staleness_rate = stale_found / total

    assert staleness_rate < 0.10, (
        f"Staleness injection rate {staleness_rate:.0%} exceeds 10% threshold. "
        f"Found {stale_found}/{total} stale tokens."
    )


def test_file_reflects_fresh_content_on_disk(staleness_store):
    """
    The database should contain only the fresh content after update.
    """
    for scenario in SCENARIOS:
        memories = list(Memory.by_stage("crystallized"))
        target = next(
            (m for m in memories if m.title == scenario["title"]),
            None
        )
        assert target is not None, f"Memory '{scenario['title']}' not found in crystallized stage."

        content = target.content or ""

        assert scenario["fresh_token"] in content, (
            f"Fresh token '{scenario['fresh_token']}' not in content for scenario '{scenario['name']}'."
        )
        assert scenario["stale_token"] not in content, (
            f"Stale token '{scenario['stale_token']}' still present in content "
            f"for scenario '{scenario['name']}' — update did not overwrite stale content."
        )


def test_single_memory_update_v1_to_v2(tmp_path):
    """
    Targeted test for the canonical example from the task spec:
    'API endpoint is /v1/users' → 'API endpoint changed to /v2/users'
    """
    init_db(base_dir=str(tmp_path / "single_update"))
    try:
        mem = Memory.create(
            stage="crystallized",
            title="API Endpoint",
            summary="Current API endpoint",
            content="API endpoint is /v1/users",
            importance=0.9,
        )

        # Verify stale content is present before update
        before = Memory.get_by_id(mem.id)
        assert "/v1/users" in before.content

        # Update
        mem.content = "API endpoint changed to /v2/users"
        mem.save()

        # Inject
        engine = RetrievalEngine()
        context = engine.inject_for_session(session_id="v1_v2_test")

        assert "/v2/users" in context, "Fresh /v2/users endpoint not found in injected context."
        assert "/v1/users" not in context, "Stale /v1/users endpoint found in injected context after update."
    finally:
        close_db()
