"""
JSONL trace writer for memesis pipeline observability.

Emits per-session structured trace events to
``~/.claude/memesis/traces/<session_id>.jsonl``.  Each event is a single
JSON line: ``{"ts": "<iso-utc>", "stage": "...", "event": "...", "payload": {...}}``.

Writes are per-event (no batching) for crash-safety.  The traces directory
is globally shared alongside ``index.db``; it survives project moves.

Retention
---------
The last 50 session files are kept, FIFO.  A lightweight index file at
``traces/.sessions`` tracks the insertion order.  Index updates are atomic
(``tempfile.mkstemp`` + ``shutil.move``).  Replay sessions tagged
``replay-<orig>-<n>`` count against the same 50-session budget.

Payload shapes by event type
-----------------------------

stage_boundary
    ``{"name": "<stage-name>", "direction": "start"|"end", "extra": {...}}``

card_synth
    ``{"card_id": "<str>", "importance": <float>, "affect_valence": "<str>",
       "evidence_obs_indices": [<int>, ...], "n_evidence_quotes": <int>}``

keep
    ``{"memory_id": "<str>|None", "importance": <float>,
       "affect_valence": "<str>|None", "kensinger_applied": <bool>}``

prune
    ``{"observation": "<str>", "reason": "<str>"}``

promote
    ``{"memory_id": "<str>", "from_stage": "<stage>", "to_stage": "<stage>"}``

kensinger_bump
    ``{"memory_id": "<str>|None", "base_importance": <float>,
       "bumped_importance": <float>}``

validator_outcome
    ``{"validator": "<str>", "result": <bool>, "card_id": "<str>|None",
       "detail": "<str>|None"}``

llm_envelope
    ``{"prompt_hash": "<sha256-hex>", "model": "<str>",
       "input_tokens": <int>|None, "output_tokens": <int>|None,
       "response_chars": <int>}``
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_BASE: Optional[Path] = None
_MAX_SESSIONS = 50
_INDEX_FILE = ".sessions"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")

# Module-level active writer context (Wave 2 instrumentation seams).
_active_writer: Optional["TraceWriter"] = None


def _default_base_dir() -> Path:
    """Return the global traces directory, creating it if necessary."""
    global _DEFAULT_BASE
    if _DEFAULT_BASE is None:
        _DEFAULT_BASE = Path.home() / ".claude" / "memesis" / "traces"
    return _DEFAULT_BASE


def set_active_writer(writer: Optional["TraceWriter"]) -> None:
    """Set the module-level active TraceWriter.  Pass ``None`` to clear."""
    global _active_writer
    _active_writer = writer


def get_active_writer() -> Optional["TraceWriter"]:
    """Return the currently active TraceWriter, or ``None`` if none is set."""
    return _active_writer


class TraceWriter:
    """Append-only JSONL trace writer for a single pipeline session.

    Parameters
    ----------
    session_id:
        Identifier for this session.  Used as the JSONL filename stem.
    base_dir:
        Directory under which ``<session_id>.jsonl`` is written.  Defaults
        to ``~/.claude/memesis/traces/``.  Override in tests via
        monkeypatching or by passing explicitly.
    """

    def __init__(self, session_id: str, base_dir: Optional[Path | str] = None) -> None:
        if not _SESSION_ID_RE.match(session_id):
            raise ValueError(f"invalid session_id: {session_id!r}")
        self._session_id = session_id
        self._base_dir: Path = Path(base_dir) if base_dir is not None else _default_base_dir()
        self._trace_path: Optional[Path] = None  # created on first emit
        self._fh: Optional[IO[str]] = None  # opened lazily on first emit

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def trace_path(self) -> Optional[Path]:
        """Path to the JSONL file, or ``None`` before first ``emit()``."""
        return self._trace_path

    def emit(self, stage: str, event: str, payload: dict[str, Any]) -> None:
        """Append one structured event to the session JSONL file.

        Creates the file (and registers the session) on the first call.
        Writes are flushed to the OS buffer immediately; fsync is deferred
        to ``close()`` / context-manager ``__exit__`` for batched durability.
        """
        if self._fh is None:
            self._init_file()

        line = json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                "event": event,
                "payload": payload,
            },
            ensure_ascii=False,
        )
        assert self._fh is not None
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        """Fsync and close the underlying file handle (idempotent)."""
        if self._fh is not None:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            finally:
                self._fh.close()
                self._fh = None

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
        set_active_writer(None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_file(self) -> None:
        """Create the base directory, open the file handle, and register the session."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._trace_path = self._base_dir / f"{self._session_id}.jsonl"
        self._fh = open(self._trace_path, "a", encoding="utf-8", buffering=1)
        _register_session(self._session_id, self._base_dir)


# ---------------------------------------------------------------------------
# Session index and FIFO retention
# ---------------------------------------------------------------------------


def _index_path(base_dir: Path) -> Path:
    return base_dir / _INDEX_FILE


def _read_index(base_dir: Path) -> list[str]:
    """Return ordered list of session IDs from the index file."""
    idx = _index_path(base_dir)
    if not idx.exists():
        return []
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(s) for s in data]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _write_index(base_dir: Path, sessions: list[str]) -> None:
    """Atomically overwrite the index file."""
    fd, tmp = tempfile.mkstemp(dir=base_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sessions, fh, ensure_ascii=False)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    shutil.move(tmp, str(_index_path(base_dir)))


def _register_session(session_id: str, base_dir: Path) -> None:
    """Add *session_id* to the index and evict oldest if over budget."""
    sessions = _read_index(base_dir)

    # Deduplicate: if already present, remove then re-append (refresh order).
    sessions = [s for s in sessions if s != session_id]
    sessions.append(session_id)

    # Evict oldest sessions over the cap.
    while len(sessions) > _MAX_SESSIONS:
        oldest = sessions.pop(0)
        _evict_session(oldest, base_dir)

    _write_index(base_dir, sessions)


def _evict_session(session_id: str, base_dir: Path) -> None:
    """Delete the JSONL file for *session_id* if it exists."""
    if not _SESSION_ID_RE.match(session_id):
        logger.warning("trace: skipping eviction of unsafe session_id %r", session_id)
        return
    target = base_dir / f"{session_id}.jsonl"
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("trace: failed to evict session %s: %s", session_id, exc)
