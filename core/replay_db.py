"""
ReplayDB — isolated tempfile database context manager for evolve/replay sessions.

Creates a fresh SQLite database under a temporary directory using the same
`init_db()` / `close_db()` path as production (same schema, same WAL pragmas,
same migrations). Callers receive `base_dir` as the context value; they can
construct a `MemoryStore` or any other component that accepts a `base_dir`
argument.

Rejects `:memory:` (D-06): in-memory databases use a different code path that
bypasses WAL pragmas and sqlite-vec extension loading. Replay fidelity requires
the full on-disk initialization sequence.

Usage::

    from core.replay_db import ReplayDB

    with ReplayDB() as base_dir:
        # base_dir is a tempdir str, index.db is initialized inside it
        from core.storage import MemoryStore
        store = MemoryStore(base_dir=base_dir)
        ...
    # tempdir is cleaned up on __exit__
"""

import shutil
import tempfile
from pathlib import Path

from core.database import close_db, init_db


class ReplayDB:
    """Context manager: tempfile-backed SQLite DB for in-process pipeline replay.

    Parameters
    ----------
    db_path:
        Intentionally limited. Pass ``None`` (default) to let the context
        manager create a fresh tempdir. Passing ``:memory:`` raises
        ``ValueError`` immediately — see module docstring for rationale.
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path == ":memory:":
            raise ValueError(
                "ReplayDB rejects ':memory:' (D-06): in-memory SQLite bypasses WAL "
                "pragmas and sqlite-vec extension loading; use the default tempfile path "
                "for replay fidelity."
            )
        self._db_path = db_path
        self._tempdir: str | None = None

    def __enter__(self) -> str:
        self._tempdir = tempfile.mkdtemp(prefix="memesis-replay-")
        init_db(base_dir=self._tempdir)
        return self._tempdir

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            close_db()
        except Exception:
            pass
        if self._tempdir is not None:
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None
        # Do not suppress exceptions
        return False
