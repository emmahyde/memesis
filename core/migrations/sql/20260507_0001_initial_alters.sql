-- Migration: 20260507_0001_initial_alters
-- Adds all columns that were previously added via inline try/except ALTER TABLE
-- in database._run_migrations(). Each ALTER is intentionally idempotent:
-- SQLite raises "duplicate column name" which the migration runner catches per-statement.

-- memories table additions
ALTER TABLE memories ADD COLUMN content TEXT;
ALTER TABLE memories ADD COLUMN archived_at TEXT;
ALTER TABLE memories ADD COLUMN subsumed_by TEXT;
ALTER TABLE memories ADD COLUMN echo_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN next_injection_due TEXT;
ALTER TABLE memories ADD COLUMN injection_ease_factor REAL DEFAULT 2.5;
ALTER TABLE memories ADD COLUMN injection_interval_days REAL DEFAULT 1.0;
ALTER TABLE memories ADD COLUMN files_modified TEXT DEFAULT '[]';
ALTER TABLE memories ADD COLUMN kind TEXT;
ALTER TABLE memories ADD COLUMN knowledge_type TEXT;
ALTER TABLE memories ADD COLUMN knowledge_type_confidence TEXT;
ALTER TABLE memories ADD COLUMN subject TEXT;
ALTER TABLE memories ADD COLUMN work_event TEXT;
ALTER TABLE memories ADD COLUMN subtitle TEXT;
ALTER TABLE memories ADD COLUMN cwd TEXT;
ALTER TABLE memories ADD COLUMN session_type TEXT;
ALTER TABLE memories ADD COLUMN raw_importance REAL;
ALTER TABLE memories ADD COLUMN linked_observation_ids TEXT;
ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_accessed_at TEXT;
ALTER TABLE memories ADD COLUMN w2_created_at TEXT;
ALTER TABLE memories ADD COLUMN resolves_question_id TEXT;
ALTER TABLE memories ADD COLUMN resolved_at TEXT;
ALTER TABLE memories ADD COLUMN is_pinned INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN temporal_scope TEXT;
ALTER TABLE memories ADD COLUMN extraction_confidence REAL;
ALTER TABLE memories ADD COLUMN actor TEXT;
ALTER TABLE memories ADD COLUMN polarity TEXT;
ALTER TABLE memories ADD COLUMN revisable TEXT;
ALTER TABLE memories ADD COLUMN confidence REAL;
ALTER TABLE memories ADD COLUMN affect_valence TEXT;
ALTER TABLE memories ADD COLUMN expires_at INTEGER DEFAULT NULL;
ALTER TABLE memories ADD COLUMN source TEXT DEFAULT 'human';

-- retrieval_log table additions
ALTER TABLE retrieval_log ADD COLUMN project_context TEXT;
ALTER TABLE retrieval_log ADD COLUMN query_text TEXT;
ALTER TABLE retrieval_log ADD COLUMN limit_count INTEGER;
ALTER TABLE retrieval_log ADD COLUMN selected_count INTEGER;
ALTER TABLE retrieval_log ADD COLUMN metadata TEXT;

-- narrative_threads table additions
ALTER TABLE narrative_threads ADD COLUMN last_surfaced_at TEXT;
ALTER TABLE narrative_threads ADD COLUMN arc_affect TEXT;

-- memory_edges table additions
ALTER TABLE memory_edges ADD COLUMN metadata TEXT;

-- consolidation_log observer instrumentation columns
ALTER TABLE consolidation_log ADD COLUMN prompt TEXT;
ALTER TABLE consolidation_log ADD COLUMN llm_response TEXT;
ALTER TABLE consolidation_log ADD COLUMN model TEXT;
ALTER TABLE consolidation_log ADD COLUMN input_tokens INTEGER;
ALTER TABLE consolidation_log ADD COLUMN output_tokens INTEGER;
ALTER TABLE consolidation_log ADD COLUMN latency_ms INTEGER;
ALTER TABLE consolidation_log ADD COLUMN input_observation_refs TEXT;

-- composite index for cards_unused_high_importance cross-session join
CREATE INDEX IF NOT EXISTS idx_retrieval_log_memid_session
    ON retrieval_log(memory_id, session_id, was_used);
