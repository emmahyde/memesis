-- Verifier predicates: declarative staleness checks evaluated by the cron sweep.
-- verify_kind is one of grep_present, grep_absent, file_exists, test_passes.
-- verify_arg is the predicate argument (regex, path, or pytest node id).
-- A memory whose predicate definitively fails is auto-archived.
ALTER TABLE memories ADD COLUMN verify_kind TEXT;
ALTER TABLE memories ADD COLUMN verify_arg TEXT;
