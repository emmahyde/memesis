-- Migration: 20260507_0003_hypothesis_schema
-- Adds hypothesis evidence tracking columns to memories table.
-- kind TEXT already exists (added in 20260507_0001) and is a no-op on live databases.
-- Runner catches duplicate column name errors per-statement.

-- kind column already exists from 20260507_0001_initial_alters (retained for completeness)
ALTER TABLE memories ADD COLUMN kind TEXT DEFAULT NULL;

-- Evidence accumulation for inferred hypotheses (RISK-12)
ALTER TABLE memories ADD COLUMN evidence_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN evidence_session_ids TEXT DEFAULT '[]';
