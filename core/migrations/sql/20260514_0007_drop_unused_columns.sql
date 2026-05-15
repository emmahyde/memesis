-- Drop unused/dead columns.
-- Evidence:
--   access_count    : 0/34 set, no read path
--   w2_created_at   : shadow of created_at, no readers
--   raw_importance  : write-only, no read path in relevance.py or retrieval.py

ALTER TABLE memories DROP COLUMN access_count;
ALTER TABLE memories DROP COLUMN w2_created_at;
ALTER TABLE memories DROP COLUMN raw_importance;
