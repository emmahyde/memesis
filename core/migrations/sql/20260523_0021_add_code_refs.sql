-- Migration 0021: add code_refs column to memories table (task #18).
--
-- code_refs stores a JSON array of {symbol, file, lang, line} dicts produced
-- by core/code_refs.py (regex baseline) or overridden by the consolidation LLM.
-- NULL = not yet extracted; "[]" = extracted but none found.
ALTER TABLE memories ADD COLUMN code_refs TEXT DEFAULT NULL;
