-- 20260517_0017_session_digest.sql
-- Per-session digest — a short topic label plus a summary, written by the
-- PreCompact hook. The SessionStart panel groups memories per session using
-- the topic label, and the post-compact session reads the summary to recover
-- what the pre-compact session was doing.
-- Note for the migration runner: keep semicolons out of comment prose.

CREATE TABLE IF NOT EXISTS session_digest (
    session_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    summary TEXT,
    memory_ids TEXT,
    created_at TEXT NOT NULL
);
