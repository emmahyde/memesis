"""
Eval 4: Retrieval Without Prompting (Spontaneous Recall)

Populates the store with 5 preference memories in the instinctive stage, calls
inject_for_session(), then checks that the injected context contains the
preferences without the agent being explicitly told to check memory.

A mock "agent response" function returns text that uses the injected context —
we verify the preferences are present in the context handed to the agent.

No LLM calls required; the "agent response" is a deterministic mock.

Target score: 70%+ of injected preference tokens are present in context.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.retrieval import RetrievalEngine
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Preference memories — these should be recalled automatically (Tier 1)
# ---------------------------------------------------------------------------

PREFERENCE_MEMORIES = [
    {
        "path": "pref_code_style.md",
        "title": "User Preference: Code Style",
        "content": "User prefers snake_case for all Python identifiers, never camelCase.",
        "summary": "snake_case for Python identifiers",
        "importance": 0.92,
        "tags": ["preference", "python", "style"],
        "token": "snake_case for all Python identifiers",
    },
    {
        "path": "pref_response_length.md",
        "title": "User Preference: Response Length",
        "content": "User wants concise answers (3 bullet points max) unless asked for detail.",
        "summary": "Concise answers, 3 bullet points max",
        "importance": 0.90,
        "tags": ["preference", "communication"],
        "token": "3 bullet points max",
    },
    {
        "path": "pref_no_emojis.md",
        "title": "User Preference: No Emojis",
        "content": "User explicitly dislikes emojis in responses. Never include them.",
        "summary": "No emojis in responses",
        "importance": 0.91,
        "tags": ["preference", "communication"],
        "token": "User explicitly dislikes emojis",
    },
    {
        "path": "pref_tool_choice.md",
        "title": "User Preference: Tooling",
        "content": "User prefers mise for version management over asdf or rbenv.",
        "summary": "mise for version management",
        "importance": 0.88,
        "tags": ["preference", "tooling"],
        "token": "mise for version management",
    },
    {
        "path": "pref_test_runner.md",
        "title": "User Preference: Test Runner",
        "content": "User always runs tests with pytest --tb=short -q for compact output.",
        "summary": "pytest --tb=short -q preferred",
        "importance": 0.89,
        "tags": ["preference", "testing"],
        "token": "pytest --tb=short -q",
    },
]


@pytest.fixture
def preference_store(tmp_path):
    """Store populated with 5 preference memories in instinctive stage."""
    store = MemoryStore(base_dir=str(tmp_path / "preference_memory"))

    for pref in PREFERENCE_MEMORIES:
        store.create(
            path=pref["path"],
            content=pref["content"],
            metadata={
                "stage": "instinctive",
                "title": pref["title"],
                "summary": pref["summary"],
                "importance": pref["importance"],
                "tags": pref["tags"],
            },
        )

    return store


@pytest.fixture
def preference_context(preference_store):
    """Context injected for a session, without any agent prompt about memory."""
    engine = RetrievalEngine(preference_store)
    return engine.inject_for_session(session_id="spontaneous_recall_session")


def _mock_agent_response(injected_context: str, user_message: str) -> str:
    """
    Simulate an agent response that has access to the injected context.

    In a real system this context would be prepended to the system prompt.
    Here we just return a response that includes the context content, simulating
    an agent that reads and uses its injected memory.
    """
    return f"[AGENT using context]\n{injected_context}\n[Response to: {user_message}]"


def test_preferences_present_in_injected_context(preference_context):
    """All 5 preference tokens should appear in the injected context."""
    missing = [
        pref["token"]
        for pref in PREFERENCE_MEMORIES
        if pref["token"] not in preference_context
    ]
    assert not missing, (
        f"Preferences not found in injected context: {missing}\n\n"
        f"Context (first 800 chars):\n{preference_context[:800]}"
    )


def test_spontaneous_recall_score(preference_context):
    """
    Spontaneous recall score = tokens_found / total_preferences >= 70%.

    Since all memories are instinctive (Tier 1, always injected), we expect
    100% recall. The 70% threshold allows one failure and still passes.
    """
    total = len(PREFERENCE_MEMORIES)
    found = sum(1 for pref in PREFERENCE_MEMORIES if pref["token"] in preference_context)
    score = found / total
    threshold = 0.70

    assert score >= threshold, (
        f"Spontaneous recall score {score:.0%} is below {threshold:.0%} threshold. "
        f"Found {found}/{total} preferences."
    )


def test_agent_response_reflects_preferences(preference_context):
    """
    Mock agent response should include preference content because the context
    is provided to it — simulates spontaneous recall without explicit instruction.
    """
    agent_response = _mock_agent_response(
        injected_context=preference_context,
        user_message="How should I format my Python code?",
    )

    # The agent has the context — it should contain at least 3/5 preference tokens
    tokens_in_response = [
        pref["token"]
        for pref in PREFERENCE_MEMORIES
        if pref["token"] in agent_response
    ]
    assert len(tokens_in_response) >= 3, (
        f"Agent response only reflected {len(tokens_in_response)}/5 preferences.\n"
        f"Tokens found: {tokens_in_response}"
    )


def test_context_contains_instinctive_section(preference_context):
    """Injected context should contain the 'Behavioral Guidelines' section header."""
    assert "## Your Behavioral Guidelines (always active)" in preference_context, (
        "Expected '## Your Behavioral Guidelines (always active)' section in context."
    )


def test_all_preferences_in_instinctive_stage(preference_store):
    """Confirm all 5 preference memories are stored in the instinctive stage."""
    instinctive = preference_store.list_by_stage("instinctive")
    assert len(instinctive) == 5, (
        f"Expected 5 instinctive memories, found {len(instinctive)}"
    )

    titles = {m["title"] for m in instinctive}
    expected_titles = {pref["title"] for pref in PREFERENCE_MEMORIES}
    assert titles == expected_titles, (
        f"Instinctive stage titles mismatch.\nExpected: {expected_titles}\nGot: {titles}"
    )
