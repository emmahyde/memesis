-- Add raw_importance column to memories table (panel C7).
-- Stores the Stage 1 LLM-assigned importance before Stage 2 re-scoring.
-- Allows calibration audit: compare raw_importance vs importance distribution
-- to detect LLM over-assignment bias (target: median Stage 2 importance < 0.65).
-- NULL for memories created before this migration.
ALTER TABLE memories ADD COLUMN raw_importance REAL;
