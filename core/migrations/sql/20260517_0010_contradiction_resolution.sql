ALTER TABLE memory_edges ADD COLUMN resolution_state TEXT NOT NULL DEFAULT 'unresolved';

UPDATE memory_edges SET resolution_state = 'resolved'
WHERE edge_type = 'contradicts' AND json_extract(metadata, '$.resolved') = 1;

CREATE TABLE IF NOT EXISTS contradiction_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id       TEXT NOT NULL,
    edge_id         INTEGER NOT NULL,
    other_memory_id TEXT NOT NULL,
    project         TEXT,
    llm_rationale   TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TEXT NOT NULL,
    recheck_fingerprint TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_contradiction_reviews_project
    ON contradiction_reviews (project);
CREATE UNIQUE INDEX IF NOT EXISTS idx_contradiction_reviews_edge_open
    ON contradiction_reviews (edge_id) WHERE status = 'open';
