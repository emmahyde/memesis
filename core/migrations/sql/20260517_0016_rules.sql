-- 20260517_0016_rules.sql
-- Behavioural rules — enforced guardrails, distinct from memories.
-- A rule is born from a memory or authored directly. It carries a
-- machine-checkable predicate, and the PreToolUse guard soft-blocks tool
-- calls that violate it. Rules have no stage progression and no decay.
-- Note for the migration runner: keep semicolons out of comment prose.

CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    check_kind TEXT NOT NULL,
    check_arg TEXT,
    severity TEXT NOT NULL DEFAULT 'block',
    status TEXT NOT NULL DEFAULT 'proposed',
    scope TEXT,
    source_memory_id TEXT,
    violation_count INTEGER NOT NULL DEFAULT 0,
    commit_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status);
