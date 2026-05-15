-- Add compression_ratio column referenced by core.models.ConsolidationLog
-- and written by core.consolidator (output_tokens / input_tokens per decision).
-- Missing column made ConsolidationLog.select() error with
--   "no such column: t1.compression_ratio"
-- which masked all consolidation failures during ingest.

ALTER TABLE consolidation_log ADD COLUMN compression_ratio REAL;
