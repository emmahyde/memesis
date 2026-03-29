"""
Eval 3: Curation Quality

Creates 10 synthetic observations (4 important decisions + 6 trivial chit-chat),
mocks Consolidator._call_llm to return deterministic KEEP/PRUNE decisions, runs
consolidation, and audits the resulting memory tree.

No real LLM calls are made.  The mock returns a predefined JSON response.

Target: curation precision >= 80% (4 kept vs 6 pruned; precision = 4/4 = 100%
of keeps are genuinely important, recall = 4/4 = 100%).
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.consolidator import Consolidator
from core.lifecycle import LifecycleManager
from core.storage import MemoryStore


# ---------------------------------------------------------------------------
# Observations — 4 important decisions + 6 trivial items
# ---------------------------------------------------------------------------

IMPORTANT_OBSERVATIONS = [
    {
        "id": "keep_1",
        "text": "Decided to pin all third-party dependencies using exact versions in Gemfile.lock.",
        "title": "Dependency Pinning Policy",
        "summary": "Pin exact versions via Gemfile.lock",
        "tags": ["dependencies", "policy"],
    },
    {
        "id": "keep_2",
        "text": "All database migrations must be reversible (provide both up and down methods).",
        "title": "Reversible Migrations Policy",
        "summary": "Migrations require up and down methods",
        "tags": ["database", "policy"],
    },
    {
        "id": "keep_3",
        "text": "Feature flags are managed via LaunchDarkly; never toggle in code directly.",
        "title": "Feature Flag Management",
        "summary": "LaunchDarkly for feature flags",
        "tags": ["feature-flags", "tooling"],
    },
    {
        "id": "keep_4",
        "text": "Incident response: first responder pages on-call within 5 minutes of alert.",
        "title": "Incident Response SLA",
        "summary": "5-minute first-responder SLA",
        "tags": ["incident", "sla"],
    },
]

TRIVIAL_OBSERVATIONS = [
    {"id": "prune_1", "text": "User said 'thanks!' after the explanation."},
    {"id": "prune_2", "text": "User is running macOS Sonoma."},
    {"id": "prune_3", "text": "User prefers dark mode in their editor."},
    {"id": "prune_4", "text": "User asked about the weather."},
    {"id": "prune_5", "text": "Session started with 'Hello, Claude'."},
    {"id": "prune_6", "text": "User closed the conversation with 'see you later'."},
]


def _build_ephemeral_content() -> str:
    """Combine all observations into a single ephemeral markdown file."""
    lines = ["# Session Ephemeral Observations\n"]
    for obs in IMPORTANT_OBSERVATIONS + TRIVIAL_OBSERVATIONS:
        lines.append(f"- {obs['text']}")
    return "\n".join(lines)


def _build_mock_llm_decisions() -> str:
    """
    Return a JSON string that mimics the Consolidator LLM response format.
    4 KEEP decisions for important observations, 6 PRUNE for trivial ones.
    """
    decisions = []

    for obs in IMPORTANT_OBSERVATIONS:
        decisions.append({
            "action": "keep",
            "observation": obs["text"],
            "title": obs["title"],
            "summary": obs["summary"],
            "tags": obs["tags"],
            "rationale": "Important policy decision worth persisting.",
            "target_path": f"policy/{obs['id']}.md",
            "contradicts": None,
        })

    for obs in TRIVIAL_OBSERVATIONS:
        decisions.append({
            "action": "prune",
            "observation": obs["text"],
            "rationale": "Trivial chit-chat; no lasting value.",
            "contradicts": None,
        })

    return json.dumps({"decisions": decisions})


@pytest.fixture
def curation_store(tmp_path):
    """Isolated store for curation audit."""
    return MemoryStore(base_dir=str(tmp_path / "curation_memory"))


@pytest.fixture
def consolidation_result(curation_store, tmp_path):
    """
    Run consolidation with a mocked LLM response.

    The mock patches Consolidator._call_llm so no real API call is made.
    Returns the consolidation result dict and the store for inspection.
    """
    lifecycle = LifecycleManager(curation_store)
    consolidator = Consolidator(curation_store, lifecycle)

    # Write the ephemeral file to disk
    ephemeral_file = tmp_path / "session_ephemeral.md"
    ephemeral_file.write_text(_build_ephemeral_content(), encoding="utf-8")

    mock_response = _build_mock_llm_decisions()

    # Patch _call_llm directly — bypasses the Anthropic client entirely
    with patch.object(consolidator, "_call_llm", return_value=consolidator._parse_decisions(mock_response)):
        result = consolidator.consolidate_session(
            ephemeral_path=str(ephemeral_file),
            session_id="curation_eval_session",
        )

    return result, curation_store


def test_curation_keeps_important_observations(consolidation_result):
    """All 4 important observations should produce kept memory_ids."""
    result, store = consolidation_result
    kept_ids = result["kept"]

    assert len(kept_ids) == len(IMPORTANT_OBSERVATIONS), (
        f"Expected {len(IMPORTANT_OBSERVATIONS)} kept memories, "
        f"got {len(kept_ids)}: {kept_ids}"
    )

    # Each kept ID should be retrievable from the store
    for mid in kept_ids:
        mem = store.get(mid)
        assert mem["stage"] == "consolidated", (
            f"Kept memory {mid} is in stage '{mem['stage']}', expected 'consolidated'."
        )


def test_curation_prunes_trivial_observations(consolidation_result):
    """All 6 trivial observations should appear in pruned list."""
    result, _ = consolidation_result
    pruned = result["pruned"]

    assert len(pruned) == len(TRIVIAL_OBSERVATIONS), (
        f"Expected {len(TRIVIAL_OBSERVATIONS)} pruned observations, got {len(pruned)}"
    )

    pruned_observations = [p["observation"] for p in pruned]
    for obs in TRIVIAL_OBSERVATIONS:
        assert obs["text"] in pruned_observations, (
            f"Trivial observation not found in pruned list: '{obs['text']}'"
        )


def test_curation_precision_above_threshold(consolidation_result):
    """
    Curation precision = kept_important / total_kept.

    All kept memories come from IMPORTANT_OBSERVATIONS (no false positives),
    so precision should be 100%, well above the 80% threshold.
    """
    result, store = consolidation_result
    kept_ids = result["kept"]
    total_kept = len(kept_ids)

    if total_kept == 0:
        pytest.fail("No memories were kept — cannot compute precision.")

    # All kept memories should have content from IMPORTANT_OBSERVATIONS
    important_texts = {obs["text"] for obs in IMPORTANT_OBSERVATIONS}
    correctly_kept = 0

    for mid in kept_ids:
        mem = store.get(mid)
        content = mem.get("content", "") or ""
        # Strip frontmatter: body starts after the closing '---' line
        lines = content.split("\n")
        if lines and lines[0] == "---":
            end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
            body = "\n".join(lines[end + 1:]).strip() if end else content
        else:
            body = content
        if any(text in body for text in important_texts):
            correctly_kept += 1

    precision = correctly_kept / total_kept
    threshold = 0.80

    assert precision >= threshold, (
        f"Curation precision {precision:.0%} is below {threshold:.0%} threshold. "
        f"Correctly kept: {correctly_kept}/{total_kept}."
    )


def test_curation_memory_tree_state(consolidation_result):
    """
    After consolidation, the memory tree should have exactly 4 consolidated memories
    and 0 ephemeral memories from this session (none were persisted there).
    """
    result, store = consolidation_result

    consolidated = store.list_by_stage("consolidated")
    assert len(consolidated) == 4, (
        f"Expected 4 consolidated memories, found {len(consolidated)}"
    )

    ephemeral = store.list_by_stage("ephemeral")
    assert len(ephemeral) == 0, (
        f"Expected 0 ephemeral memories after consolidation, found {len(ephemeral)}"
    )


def test_curation_no_real_llm_call(curation_store, tmp_path):
    """
    Ensure the eval never makes a real Anthropic API call.
    Patch anthropic.Anthropic at the module level to catch any accidental import.
    """
    lifecycle = LifecycleManager(curation_store)
    consolidator = Consolidator(curation_store, lifecycle)

    ephemeral_file = tmp_path / "ephemeral_no_llm.md"
    ephemeral_file.write_text("- Some observation.", encoding="utf-8")

    fake_response = json.dumps({"decisions": [
        {
            "action": "prune",
            "observation": "Some observation.",
            "rationale": "Not worth keeping.",
            "contradicts": None,
        }
    ]})

    with patch("anthropic.Anthropic") as mock_anthropic_cls:
        # If a real call is attempted, the mock's return value won't have
        # .messages.create() wired up — it will raise AttributeError.
        # We also patch _call_llm to confirm it's our mock that's used.
        with patch.object(
            consolidator, "_call_llm",
            return_value=consolidator._parse_decisions(fake_response)
        ):
            result = consolidator.consolidate_session(
                ephemeral_path=str(ephemeral_file),
                session_id="no_llm_test",
            )

        # Anthropic class was never instantiated (our _call_llm mock bypasses it)
        mock_anthropic_cls.assert_not_called()

    assert result["pruned"][0]["observation"] == "Some observation."
