"""Tests for scripts/autoresearch_config.py."""

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def evolve_base(tmp_path, monkeypatch):
    """Redirect _EVOLVE_BASE to a tmp_path subdir."""
    import scripts.autoresearch_config as ac

    base = tmp_path / "evolve"
    monkeypatch.setattr(ac, "_EVOLVE_BASE", base)
    return base


class TestWriteAutoresearchConfig:
    def test_writes_to_session_dir(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config("sess-1")
        assert path.exists()
        assert path.parent == evolve_base / "sess-1"
        assert path.name == "autoresearch.yaml"

    def test_creates_nested_session_dir(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        assert not evolve_base.exists()
        write_autoresearch_config("nested-sess")
        assert (evolve_base / "nested-sess").is_dir()

    def test_default_max_iterations_is_10(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config("s")
        text = path.read_text(encoding="utf-8")
        assert "max_iterations: 10" in text

    def test_default_token_budget_is_100000(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config("s")
        text = path.read_text(encoding="utf-8")
        assert "token_budget: 100000" in text

    def test_overrides_max_iterations(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config("s", max_iterations=42)
        assert "max_iterations: 42" in path.read_text(encoding="utf-8")

    def test_overrides_token_budget(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config("s", token_budget=5)
        assert "token_budget: 5" in path.read_text(encoding="utf-8")

    def test_default_mutation_surface_matches_d14(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config("s")
        text = path.read_text(encoding="utf-8")
        for f in (
            "core/prompts.py",
            "core/issue_cards.py",
            "core/rule_registry.py",
            "core/consolidator.py",
            "core/crystallizer.py",
        ):
            assert f"  - {f}" in text

    def test_overrides_mutation_surface(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config(
            "s", mutation_surface=["core/only.py"]
        )
        text = path.read_text(encoding="utf-8")
        assert "  - core/only.py" in text
        assert "core/prompts.py" not in text

    def test_default_guard_suite_present(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config("s")
        text = path.read_text(encoding="utf-8")
        assert "python3 -m pytest tests/" in text
        assert "TestCardImportance" in text
        assert "eval/recall/" in text
        assert "test_manifest" in text

    def test_overrides_guard_suite(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config("s", guard_suite=["echo hi"])
        text = path.read_text(encoding="utf-8")
        assert '  - "echo hi"' in text
        assert "TestCardImportance" not in text

    def test_guard_command_with_double_quotes_escaped(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config(
            "s", guard_suite=['echo "hi"']
        )
        text = path.read_text(encoding="utf-8")
        assert '\\"hi\\"' in text

    def test_no_temp_files_remain_after_success(self, evolve_base):
        from scripts.autoresearch_config import write_autoresearch_config

        write_autoresearch_config("s")
        session_dir = evolve_base / "s"
        leftovers = list(session_dir.glob("*.tmp"))
        assert leftovers == []

    def test_yaml_round_trip_with_loader(self, evolve_base):
        """Verify autoresearch._load_yaml can parse the generated file."""
        from scripts.autoresearch_config import write_autoresearch_config

        path = write_autoresearch_config(
            "s", max_iterations=7, token_budget=999
        )

        try:
            from core.autoresearch import _load_yaml
        except ImportError:
            pytest.skip("core.autoresearch._load_yaml not available")

        data = _load_yaml(path)
        assert data["max_iterations"] == 7
        assert data["token_budget"] == 999
        assert "core/prompts.py" in data["mutation_surface"]

    def test_atomic_write_cleanup_on_error(self, evolve_base, monkeypatch):
        """If shutil.move fails, the .tmp file should be cleaned up."""
        import scripts.autoresearch_config as ac

        def boom(src, dst):
            raise OSError("simulated move failure")

        monkeypatch.setattr(ac.shutil, "move", boom)

        with pytest.raises(OSError, match="simulated move failure"):
            ac.write_autoresearch_config("s")

        session_dir = evolve_base / "s"
        leftovers = list(session_dir.glob("*.tmp"))
        assert leftovers == [], f"Leftover tmp files: {leftovers}"
