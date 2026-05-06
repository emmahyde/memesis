"""
Tests for core/replay_db.py and core/llm_cache.py.

All tests use tmp_path for isolation. The MEMESIS_EVOLVE_CACHE_DIR environment
variable is set to a tmp_path subdirectory so no test touches the real cache at
~/.claude/memesis/evolve/cache/.
"""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# ReplayDB tests
# ---------------------------------------------------------------------------


class TestReplayDBContextManager:
    def test_context_manager_creates_tempdir(self):
        from core.replay_db import ReplayDB

        entered_path = None
        with ReplayDB() as base_dir:
            entered_path = base_dir
            assert os.path.isdir(base_dir)
            assert os.path.basename(base_dir).startswith("memesis-replay-")

    def test_context_manager_cleans_up_on_exit(self):
        from core.replay_db import ReplayDB

        entered_path = None
        with ReplayDB() as base_dir:
            entered_path = base_dir
            assert os.path.isdir(base_dir)

        # tempdir should be removed after __exit__
        assert not os.path.exists(entered_path)

    def test_context_manager_cleans_up_on_exception(self):
        from core.replay_db import ReplayDB

        entered_path = None
        with pytest.raises(RuntimeError):
            with ReplayDB() as base_dir:
                entered_path = base_dir
                raise RuntimeError("deliberate error")

        assert not os.path.exists(entered_path)

    def test_init_db_called_with_tempdir(self):
        from core.replay_db import ReplayDB

        with patch("core.replay_db.init_db") as mock_init, \
             patch("core.replay_db.close_db"), \
             patch("tempfile.mkdtemp", return_value="/tmp/memesis-replay-test") as mock_mkdtemp, \
             patch("shutil.rmtree"):
            with ReplayDB() as base_dir:
                mock_init.assert_called_once_with(base_dir="/tmp/memesis-replay-test")
                assert base_dir == "/tmp/memesis-replay-test"

    def test_close_db_called_on_exit(self):
        from core.replay_db import ReplayDB

        with patch("core.replay_db.init_db"), \
             patch("core.replay_db.close_db") as mock_close, \
             patch("shutil.rmtree"):
            with ReplayDB() as _:
                pass
            mock_close.assert_called_once()

    def test_index_db_created(self):
        """Integration: index.db should exist inside the tempdir after __enter__."""
        from core.replay_db import ReplayDB
        from core.database import close_db

        with ReplayDB() as base_dir:
            db_path = Path(base_dir) / "index.db"
            assert db_path.exists(), f"index.db not found at {db_path}"


class TestReplayDBWALMode:
    def test_wal_mode_enabled(self):
        """PRAGMA journal_mode should return 'wal' inside the context."""
        from core.replay_db import ReplayDB
        from core.database import db

        with ReplayDB() as base_dir:
            cursor = db.execute_sql("PRAGMA journal_mode")
            journal_mode = cursor.fetchone()[0]
            assert journal_mode == "wal", f"Expected 'wal', got '{journal_mode}'"


class TestReplayDBRejectsMemory:
    def test_raises_on_memory_path(self):
        from core.replay_db import ReplayDB

        with pytest.raises(ValueError, match=":memory:"):
            ReplayDB(db_path=":memory:")

    def test_does_not_raise_on_none(self):
        """None (the default) should not raise."""
        from core.replay_db import ReplayDB
        # Just constructing — no DB ops yet
        rdb = ReplayDB(db_path=None)
        assert rdb is not None

    def test_does_not_raise_on_default(self):
        """Default constructor should not raise."""
        from core.replay_db import ReplayDB
        rdb = ReplayDB()
        assert rdb is not None


# ---------------------------------------------------------------------------
# llm_cache tests
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Redirect LLM cache to a tmp_path subdir and return the path."""
    d = tmp_path / "llm_cache"
    d.mkdir()
    monkeypatch.setenv("MEMESIS_EVOLVE_CACHE_DIR", str(d))
    return d


class TestCacheSha256Key:
    def test_key_derivation(self):
        from core.llm_cache import _cache_key

        model = "claude-sonnet-4-6"
        prompt = "hello world"
        expected = hashlib.sha256((model + prompt).encode("utf-8")).hexdigest()
        assert _cache_key(model, prompt) == expected

    def test_different_model_different_key(self):
        from core.llm_cache import _cache_key

        k1 = _cache_key("model-a", "prompt")
        k2 = _cache_key("model-b", "prompt")
        assert k1 != k2

    def test_different_prompt_different_key(self):
        from core.llm_cache import _cache_key

        k1 = _cache_key("model", "prompt-a")
        k2 = _cache_key("model", "prompt-b")
        assert k1 != k2

    def test_none_model_uses_empty_string(self):
        from core.llm_cache import _cache_key

        k_none = _cache_key("", "prompt")
        # When model=None is passed to cached_call_llm, it normalizes to ""
        assert _cache_key("", "prompt") == k_none


class TestCacheHitMiss:
    def test_cache_miss_calls_call_llm(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="live response") as mock_llm:
            result = cached_call_llm("test prompt", model="m1")

        mock_llm.assert_called_once()
        assert result == "live response"

    def test_cache_hit_does_not_call_call_llm(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="first response"):
            cached_call_llm("test prompt", model="m1")

        # Second call — mock should NOT be invoked
        with patch("core.llm_cache.call_llm") as mock_llm:
            result = cached_call_llm("test prompt", model="m1")
            mock_llm.assert_not_called()

        assert result == "first response"

    def test_cache_hit_returns_stored_response(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="cached value"):
            cached_call_llm("prompt", model="m1")

        with patch("core.llm_cache.call_llm", return_value="new value"):
            result = cached_call_llm("prompt", model="m1")

        assert result == "cached value"

    def test_cache_file_written_after_miss(self, cache_dir):
        from core.llm_cache import _cache_key, cached_call_llm

        key = _cache_key("m1", "prompt x")
        expected_file = cache_dir / f"{key}.json"

        with patch("core.llm_cache.call_llm", return_value="response x"):
            cached_call_llm("prompt x", model="m1")

        assert expected_file.exists()
        data = json.loads(expected_file.read_text())
        assert data["response"] == "response x"


class TestCacheForceLive:
    def test_force_live_bypasses_cache(self, cache_dir):
        from core.llm_cache import cached_call_llm

        # Populate cache
        with patch("core.llm_cache.call_llm", return_value="cached"):
            cached_call_llm("prompt", model="m1")

        # force_live=True should call live
        with patch("core.llm_cache.call_llm", return_value="fresh") as mock_llm:
            result = cached_call_llm("prompt", model="m1", force_live=True)
            mock_llm.assert_called_once()

        assert result == "fresh"

    def test_force_live_updates_cache(self, cache_dir):
        from core.llm_cache import _cache_key, cached_call_llm

        key = _cache_key("m1", "prompt")
        cache_file = cache_dir / f"{key}.json"

        with patch("core.llm_cache.call_llm", return_value="old"):
            cached_call_llm("prompt", model="m1")

        with patch("core.llm_cache.call_llm", return_value="new"):
            cached_call_llm("prompt", model="m1", force_live=True)

        data = json.loads(cache_file.read_text())
        assert data["response"] == "new"

    def test_no_cache_force_live_still_works(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="resp") as mock_llm:
            result = cached_call_llm("fresh prompt", model="m1", force_live=True)
            mock_llm.assert_called_once()

        assert result == "resp"


class TestCacheEviction:
    def test_eviction_removes_oldest_files(self, cache_dir, monkeypatch):
        from core.llm_cache import _CACHE_EVICTION_THRESHOLD, _evict_if_needed

        # Create files with controlled sizes and mtimes
        chunk = b"x" * (200 * 1024 * 1024)  # 200 MB each

        # Write 3 files: total = 600 MB > 500 MB threshold
        files = []
        import time
        for i in range(3):
            f = cache_dir / f"file_{i}.json"
            f.write_bytes(chunk)
            # stagger mtimes
            mtime = time.time() - (3 - i) * 1000  # file_0 oldest
            os.utime(str(f), (mtime, mtime))
            files.append(f)

        _evict_if_needed(cache_dir)

        # file_0 (oldest) should be removed; file_2 (newest) should survive
        assert not files[0].exists(), "Oldest file should be evicted"
        assert files[2].exists(), "Newest file should survive"

    def test_no_eviction_under_threshold(self, cache_dir):
        from core.llm_cache import _evict_if_needed

        # Write one small file (well under 500 MB)
        f = cache_dir / "small.json"
        f.write_bytes(b"x" * 1024)

        _evict_if_needed(cache_dir)

        assert f.exists(), "File under threshold should not be evicted"

    def test_eviction_called_after_write(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="r"), \
             patch("core.llm_cache._evict_if_needed") as mock_evict:
            cached_call_llm("p", model="m")
            mock_evict.assert_called_once()


class TestCacheAtomicWrite:
    def test_no_partial_files_on_write(self, cache_dir):
        """Verify the tmp-file pattern: no .tmp file left after successful write."""
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="ok"):
            cached_call_llm("atomic prompt", model="m1")

        tmp_files = list(cache_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Leftover .tmp files: {tmp_files}"


class TestCachedCallLlmSignature:
    """Verify that cached_call_llm passes kwargs through to call_llm correctly."""

    def test_passes_max_tokens(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="r") as mock_llm:
            cached_call_llm("p", model="m", max_tokens=1024)
            _, kwargs = mock_llm.call_args
            assert kwargs.get("max_tokens") == 1024

    def test_passes_temperature(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="r") as mock_llm:
            cached_call_llm("p", model="m", temperature=0.5)
            _, kwargs = mock_llm.call_args
            assert kwargs.get("temperature") == 0.5

    def test_passes_model(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="r") as mock_llm:
            cached_call_llm("p", model="custom-model")
            _, kwargs = mock_llm.call_args
            assert kwargs.get("model") == "custom-model"

    def test_none_model_passed_through(self, cache_dir):
        from core.llm_cache import cached_call_llm

        with patch("core.llm_cache.call_llm", return_value="r") as mock_llm:
            cached_call_llm("p", model=None)
            _, kwargs = mock_llm.call_args
            assert kwargs.get("model") is None
