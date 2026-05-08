"""
Tests for RISK-11: Cognitive module audit, experimental flag, and module_scores in retrieval.

Covers:
- experimental: bool constant on each cognitive module
- Experimental modules excluded from scoring by default
- MEMESIS_EXPERIMENTAL_MODULES env var opt-in
- module_scores key present in retrieval output (active_search and inject_for_session)
"""

import importlib
import os
import pytest

from core.database import close_db, init_db
from core.models import Memory
from core.retrieval import (
    RetrievalEngine,
    _get_enabled_modules,
    compute_module_scores,
    _COGNITIVE_MODULES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base(tmp_path):
    init_db(base_dir=str(tmp_path / "memory"))
    yield tmp_path
    close_db()


def _make_memory(**kwargs) -> Memory:
    defaults = {
        "title": "Test Memory",
        "content": "Some test content",
        "stage": "consolidated",
        "importance": 0.6,
        "project_context": "/test/project",
    }
    defaults.update(kwargs)
    return Memory.create(**defaults)


# ---------------------------------------------------------------------------
# TestExperimentalFlag: each module exposes experimental: bool
# ---------------------------------------------------------------------------

class TestExperimentalFlag:
    """Every cognitive module must expose an 'experimental' boolean constant."""

    @pytest.mark.parametrize("module_name", [
        "affect",
        "coherence",
        "habituation",
        "orienting",
        "replay",
        "self_reflection",
        "somatic",
    ])
    def test_module_has_experimental_attribute(self, module_name):
        mod = importlib.import_module(f"core.{module_name}")
        assert hasattr(mod, "experimental"), (
            f"core.{module_name} is missing the 'experimental' attribute"
        )
        assert isinstance(mod.experimental, bool), (
            f"core.{module_name}.experimental must be a bool, got {type(mod.experimental)}"
        )

    def test_self_reflection_is_experimental(self):
        """self_reflection must be experimental=True (writer path not yet validated)."""
        import core.self_reflection as sr
        assert sr.experimental is True

    def test_production_modules_not_experimental(self):
        """All production modules (non self_reflection) must be experimental=False."""
        production_modules = ["affect", "coherence", "habituation", "orienting", "replay", "somatic"]
        for module_name in production_modules:
            mod = importlib.import_module(f"core.{module_name}")
            assert mod.experimental is False, (
                f"core.{module_name}.experimental should be False (production-validated)"
            )


# ---------------------------------------------------------------------------
# TestEnabledModules: env-var opt-in and default exclusion
# ---------------------------------------------------------------------------

class TestEnabledModules:
    """Experimental module exclusion and env-var opt-in behavior."""

    def test_experimental_module_excluded_by_default(self):
        """self_reflection (experimental=True) excluded from enabled set by default."""
        # Ensure env var is not set
        env_backup = os.environ.pop("MEMESIS_EXPERIMENTAL_MODULES", None)
        try:
            enabled = _get_enabled_modules()
            assert "self_reflection" not in enabled, (
                "self_reflection should be excluded from enabled modules by default"
            )
        finally:
            if env_backup is not None:
                os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = env_backup

    def test_production_modules_enabled_by_default(self):
        """All non-experimental modules appear in enabled set by default."""
        env_backup = os.environ.pop("MEMESIS_EXPERIMENTAL_MODULES", None)
        try:
            enabled = _get_enabled_modules()
            for name in ["affect", "coherence", "habituation", "orienting", "replay", "somatic"]:
                assert name in enabled, f"{name} should be enabled by default"
        finally:
            if env_backup is not None:
                os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = env_backup

    def test_env_var_opt_in_enables_experimental_module(self):
        """MEMESIS_EXPERIMENTAL_MODULES=self_reflection enables self_reflection scoring."""
        old_val = os.environ.get("MEMESIS_EXPERIMENTAL_MODULES")
        os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = "self_reflection"
        try:
            enabled = _get_enabled_modules()
            assert "self_reflection" in enabled, (
                "self_reflection should be enabled when listed in MEMESIS_EXPERIMENTAL_MODULES"
            )
        finally:
            if old_val is None:
                os.environ.pop("MEMESIS_EXPERIMENTAL_MODULES", None)
            else:
                os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = old_val

    def test_env_var_multiple_modules(self):
        """Comma-separated env var enables multiple experimental modules."""
        old_val = os.environ.get("MEMESIS_EXPERIMENTAL_MODULES")
        os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = "self_reflection,affect"
        try:
            enabled = _get_enabled_modules()
            assert "self_reflection" in enabled
            assert "affect" in enabled
        finally:
            if old_val is None:
                os.environ.pop("MEMESIS_EXPERIMENTAL_MODULES", None)
            else:
                os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = old_val

    def test_empty_env_var_does_not_enable_experimental(self):
        """Empty MEMESIS_EXPERIMENTAL_MODULES string does not opt anything in."""
        old_val = os.environ.get("MEMESIS_EXPERIMENTAL_MODULES")
        os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = ""
        try:
            enabled = _get_enabled_modules()
            assert "self_reflection" not in enabled
        finally:
            if old_val is None:
                os.environ.pop("MEMESIS_EXPERIMENTAL_MODULES", None)
            else:
                os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = old_val


# ---------------------------------------------------------------------------
# TestComputeModuleScores: compute_module_scores returns expected shape
# ---------------------------------------------------------------------------

class TestComputeModuleScores:
    """compute_module_scores returns correct structure and values."""

    def test_empty_memories_returns_all_zero(self, base):
        scores = compute_module_scores([])
        assert isinstance(scores, dict)
        for module_name in _COGNITIVE_MODULES:
            assert module_name in scores
            assert scores[module_name] == 0.0

    def test_returns_all_module_keys(self, base):
        mem = _make_memory()
        scores = compute_module_scores([mem])
        for module_name in _COGNITIVE_MODULES:
            assert module_name in scores, f"Missing module_scores key: {module_name}"

    def test_scores_are_floats_in_range(self, base):
        mem = _make_memory(importance=0.8)
        scores = compute_module_scores([mem])
        for k, v in scores.items():
            assert isinstance(v, float), f"{k} score should be float"
            assert 0.0 <= v <= 1.0, f"{k} score {v} out of [0, 1] range"

    def test_experimental_module_excluded_when_not_opted_in(self, base):
        """self_reflection score is 0.0 when not in enabled_modules."""
        env_backup = os.environ.pop("MEMESIS_EXPERIMENTAL_MODULES", None)
        try:
            mem = _make_memory(stage="instinctive")
            enabled = _get_enabled_modules()
            assert "self_reflection" not in enabled
            scores = compute_module_scores([mem], enabled_modules=enabled)
            # self_reflection should be 0.0 when excluded
            assert scores["self_reflection"] == 0.0
        finally:
            if env_backup is not None:
                os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = env_backup

    def test_experimental_module_scores_when_opted_in(self, base):
        """self_reflection scores > 0 for instinctive memory when explicitly enabled."""
        mem = _make_memory(stage="instinctive")
        # Explicitly enable self_reflection
        enabled = set(_COGNITIVE_MODULES)  # force-enable all
        scores = compute_module_scores([mem], enabled_modules=enabled)
        assert scores["self_reflection"] == 1.0

    def test_affect_valence_friction_boosts_affect_score(self, base):
        mem = _make_memory(affect_valence="friction")
        scores = compute_module_scores([mem])
        assert scores["affect"] == 1.0

    def test_neutral_affect_gives_zero_affect_score(self, base):
        mem = _make_memory(affect_valence="neutral")
        scores = compute_module_scores([mem])
        assert scores["affect"] == 0.0


# ---------------------------------------------------------------------------
# TestModuleScoresInRetrieval: module_scores present in active_search output
# ---------------------------------------------------------------------------

class TestModuleScoresInRetrieval:
    """module_scores key is present in retrieval output."""

    def test_active_search_result_has_module_scores(self, base):
        """Each result dict from active_search includes module_scores."""
        _make_memory(
            title="Python testing patterns",
            content="Use pytest fixtures for database isolation",
            stage="crystallized",
        )

        engine = RetrievalEngine()
        results = engine.active_search(query="python testing", session_id="test-session-1")

        # Results may be empty if FTS doesn't find a match — that's fine,
        # just check that if there are results, they have module_scores.
        for result in results:
            assert "module_scores" in result, "active_search result missing module_scores key"
            assert isinstance(result["module_scores"], dict)
            for module_name in _COGNITIVE_MODULES:
                assert module_name in result["module_scores"]

    def test_last_module_scores_populated_after_active_search(self, base):
        """engine._last_module_scores is populated after active_search call."""
        _make_memory(
            title="Memory retrieval patterns",
            content="RRF fusion combines FTS and vector scores",
            stage="crystallized",
        )

        engine = RetrievalEngine()
        engine.active_search(query="retrieval", session_id="test-session-2")

        assert isinstance(engine._last_module_scores, dict)
        for module_name in _COGNITIVE_MODULES:
            assert module_name in engine._last_module_scores

    def test_last_module_scores_populated_after_inject_for_session(self, base):
        """engine._last_module_scores is populated after inject_for_session call."""
        _make_memory(
            title="Instinctive guideline",
            content="Always use atomic writes",
            stage="instinctive",
        )

        engine = RetrievalEngine()
        engine.inject_for_session(session_id="test-session-3")

        assert isinstance(engine._last_module_scores, dict)
        for module_name in _COGNITIVE_MODULES:
            assert module_name in engine._last_module_scores

    def test_module_scores_self_reflection_excluded_by_default(self, base):
        """self_reflection score is 0.0 in active_search results by default."""
        _make_memory(
            title="Self reflection test",
            content="Self model update content here",
            stage="consolidated",
        )

        env_backup = os.environ.pop("MEMESIS_EXPERIMENTAL_MODULES", None)
        try:
            engine = RetrievalEngine()
            engine.active_search(query="self reflection", session_id="test-session-4")

            # self_reflection excluded → score is 0.0
            assert engine._last_module_scores.get("self_reflection", 0.0) == 0.0
        finally:
            if env_backup is not None:
                os.environ["MEMESIS_EXPERIMENTAL_MODULES"] = env_backup
