-- Curated memory-kind taxonomy (canvas review 2026-05-15 section 1).
-- memory_kind is a new additive column. The existing `kind` column keeps the
-- raw observation-extraction taxonomy (decision/finding/correction/...), which
-- the question-lifecycle hook and other code branch on. memory_kind carries the
-- curated 10-value taxonomy that scoring and retrieval reason about.
ALTER TABLE memories ADD COLUMN memory_kind TEXT;

-- Backfill from the existing kind column via the deterministic map in
-- core/validators.py derive_memory_kind. A finding with multi-session evidence
-- is a lesson, otherwise a fact. open_question stays NULL -- it is a lifecycle
-- state, not a knowledge kind.
UPDATE memories SET memory_kind = CASE
    WHEN kind = 'decision' THEN 'decision'
    WHEN kind = 'correction' THEN 'gotcha'
    WHEN kind = 'constraint' THEN 'invariant'
    WHEN kind = 'preference' THEN 'opinion'
    WHEN kind = 'finding' AND COALESCE(evidence_count, 0) >= 2 THEN 'lesson'
    WHEN kind = 'finding' THEN 'fact'
    ELSE memory_kind
END
WHERE kind IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_memory_kind ON memories (memory_kind);
