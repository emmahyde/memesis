"""
Global transcript cursor store at ~/.claude/memesis/cursors.db.

Tracks the last-read byte offset per Claude Code session JSONL so the
cron extractor can efficiently read only new content each tick.
"""

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time

MEMESIS_DIR = Path.home() / ".claude" / "memesis"
CURSORS_DB = MEMESIS_DIR / "cursors.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS transcript_cursors (
  session_id       TEXT PRIMARY KEY,
  transcript_path  TEXT NOT NULL,
  last_byte_offset INTEGER NOT NULL DEFAULT 0,
  first_seen_at    INTEGER NOT NULL,
  last_run_at      INTEGER NOT NULL
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_cursors_last_run ON transcript_cursors(last_run_at);
"""


@dataclass
class CursorRow:
    session_id: str
    transcript_path: str
    last_byte_offset: int
    first_seen_at: int
    last_run_at: int


class CursorStore:
    def __init__(self, db_path: Path = CURSORS_DB):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX)
        self._conn.commit()

    def get(self, session_id: str) -> CursorRow | None:
        row = self._conn.execute(
            "SELECT * FROM transcript_cursors WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return CursorRow(
            session_id=row["session_id"],
            transcript_path=row["transcript_path"],
            last_byte_offset=row["last_byte_offset"],
            first_seen_at=row["first_seen_at"],
            last_run_at=row["last_run_at"],
        )

    def upsert(
        self,
        session_id: str,
        transcript_path: str,
        last_byte_offset: int,
        *,
        first_seen_at: int | None = None,
    ) -> None:
        now = int(time.time())
        if first_seen_at is None:
            first_seen_at = now
        self._conn.execute(
            """
            INSERT OR REPLACE INTO transcript_cursors
              (session_id, transcript_path, last_byte_offset, first_seen_at, last_run_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, transcript_path, last_byte_offset, first_seen_at, now),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self.close()
        return False
