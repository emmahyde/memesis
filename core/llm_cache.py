"""
LLM cache wrapper for evolve/replay sessions.

Wraps `core.llm.call_llm()` with a filesystem cache keyed by
``sha256(model + prompt)``. Cached responses are stored at::

    ~/.claude/memesis/evolve/cache/<sha256>.json

This allows replay sessions to avoid redundant LLM calls when re-running the
same transcript with unchanged prompts/model. When the prompt or model changes
(e.g., during autoresearch mutation), the hash changes and the live API is
called automatically.

Configuration
-------------
The cache directory can be overridden via the ``MEMESIS_EVOLVE_CACHE_DIR``
environment variable (useful for tests — always set this in tests to avoid
touching the real cache directory).

``force_live=True`` bypasses the cache entirely and calls the live API.

Eviction
--------
When the total size of the cache directory exceeds 500 MB, the oldest files
(by mtime) are removed until the total is under the limit.

Atomic writes
-------------
Cache files are written atomically via ``tempfile.mkstemp`` + ``shutil.move``
to prevent partial reads if the process is interrupted during a write.
"""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from core.llm import call_llm

# 500 MB eviction threshold (bytes)
_CACHE_EVICTION_THRESHOLD = 500 * 1024 * 1024

_DEFAULT_CACHE_DIR = Path.home() / ".claude" / "memesis" / "evolve" / "cache"


def _cache_dir() -> Path:
    """Return the active cache directory, respecting env override."""
    env_override = os.environ.get("MEMESIS_EVOLVE_CACHE_DIR")
    if env_override:
        return Path(env_override)
    return _DEFAULT_CACHE_DIR


def _cache_key(model: str, prompt: str) -> str:
    """Return sha256 hex digest of ``model + prompt``."""
    raw = (model + prompt).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _dir_size(directory: Path) -> int:
    """Return total byte size of all files directly in *directory*."""
    total = 0
    try:
        for entry in directory.iterdir():
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _evict_if_needed(directory: Path) -> None:
    """Remove oldest files (by mtime) until cache is under 500 MB."""
    total = _dir_size(directory)
    if total <= _CACHE_EVICTION_THRESHOLD:
        return

    files = []
    try:
        for entry in directory.iterdir():
            if entry.is_file():
                try:
                    files.append((entry.stat().st_mtime, entry))
                except OSError:
                    pass
    except OSError:
        return

    # Oldest first
    files.sort(key=lambda x: x[0])

    for _, path in files:
        if total <= _CACHE_EVICTION_THRESHOLD:
            break
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
        except OSError:
            pass


def cached_call_llm(
    prompt: str,
    *,
    max_tokens: int = 8192,
    temperature: float = 0,
    model: str | None = None,
    force_live: bool = False,
) -> str:
    """Call ``core.llm.call_llm()`` with disk caching by sha256(model+prompt).

    Parameters
    ----------
    prompt:
        The full prompt to send (same as ``call_llm``).
    max_tokens:
        Maximum tokens in the response (passed through to ``call_llm``).
    temperature:
        Sampling temperature (passed through).
    model:
        Model override (passed through). ``None`` selects the default model.
        Used as part of the cache key; ``None`` is normalized to the empty
        string for key purposes.
    force_live:
        If ``True``, skip cache lookup and always call the live API. The
        fresh response is still written to cache.

    Returns
    -------
    str
        Response text from cache or live API.
    """
    # Normalize model for cache key (None means "default" — use empty string)
    model_key = model if model is not None else ""
    key = _cache_key(model_key, prompt)

    cache_directory = _cache_dir()
    cache_file = cache_directory / f"{key}.json"

    if not force_live and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return data["response"]
        except (OSError, KeyError, json.JSONDecodeError):
            # Corrupt cache entry — fall through to live call
            pass

    # Live call
    response = call_llm(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        model=model,
    )

    # Atomic write to cache
    cache_directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"model": model_key, "prompt_sha256": key, "response": response})
    fd, tmp_path = tempfile.mkstemp(dir=str(cache_directory), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        shutil.move(tmp_path, str(cache_file))
    except Exception:
        # Best-effort cleanup of temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    _evict_if_needed(cache_directory)

    return response
