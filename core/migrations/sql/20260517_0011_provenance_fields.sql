-- Provenance fields: trace a memory back to its origin.
-- The source_session column already exists (the session that created the memory).
-- commit_ref is stamped at creation from git HEAD.
-- source_pr is reserved for later association with a pull request.
ALTER TABLE memories ADD COLUMN source_pr TEXT;
ALTER TABLE memories ADD COLUMN commit_ref TEXT;
