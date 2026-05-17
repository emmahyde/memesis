-- Bundled-row decomposer bookkeeping (canvas review 2026-05-15 section 6.6).
-- decompose_checked marks a memory the decomposer sweep has already audited, so
-- coherent memories are not re-evaluated on every cron run. Freshly split child
-- memories are created with the flag already set.
ALTER TABLE memories ADD COLUMN decompose_checked INTEGER NOT NULL DEFAULT 0;
