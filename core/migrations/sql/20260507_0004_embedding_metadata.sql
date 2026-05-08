-- Migration: 20260507000001_embedding_metadata
-- Adds embedding metadata companion table and system settings table.
--
-- NOTE: vec_memories is a sqlite-vec vec0 virtual table and does not support
-- ALTER TABLE ADD COLUMN. Embedding metadata is stored in a companion regular
-- table (vec_embedding_meta) keyed by memory_id instead.
--
-- Sort note: this filename sorts before 20260507_000N files (digit < underscore).
-- This is intentional — these are new tables with no dependencies on prior migrations.

-- Companion metadata table for vec_memories.
-- Stores the embedding model, version, and dimension used for each memory's embedding.
CREATE TABLE IF NOT EXISTS vec_embedding_meta (
    memory_id TEXT PRIMARY KEY,
    embedding_model TEXT NOT NULL DEFAULT '',
    embedding_version TEXT NOT NULL DEFAULT '',
    embedding_dim INTEGER NOT NULL DEFAULT 0
);

-- System settings table.
-- Stores active embedding configuration for the running system.
CREATE TABLE IF NOT EXISTS _system (
    key TEXT PRIMARY KEY,
    value TEXT
);
