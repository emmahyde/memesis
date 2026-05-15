---
name: index
description: >
  Hub for memesis developer operations. Lists all available ops skills and provides system
  orientation, quick diagnostics, and the steelmanning protocol. Use when you need orientation
  on which debugging or auditing skill to invoke, or when asked to debug memesis, audit the
  system, what ops skills are there, or any open-ended dev-ops question. Triggers on:
  "ops mode", "debug memesis", "which skill should I use", "memesis developer mode",
  "system orientation", /memesis:index.
---

# Memesis Ops — Index

**Invoked as:** `/memesis:index`

Expert developer hub for working ON the memesis codebase. Pick the workflow skill that fits your task.

---

## Available Skills

| Skill | Invocation | Use when |
|-------|-----------|---------|
| Behavioral Expectation Audit | `/memesis:audit` | Something seems wrong; pre-change verification |
| Hook Chain Trace | `/memesis:trace` | Memories not appearing; injection silent; consolidation not running |
| Lifecycle Audit | `/memesis:lifecycle` | Stage transitions wrong; memories stuck |
| Consolidation Pipeline Trace | `/memesis:pipeline` | Specific observation didn't produce a memory |
| Test Coverage Gap Analysis | `/memesis:coverage` | Before a refactor; new feature needs test planning |
| Architecture & Design Review | `/memesis:review` | Evaluating a proposed change; assessing latent risks |
| Observation Failure Audit | `/memesis:failures` | Auditing consolidation quality; systematic under-observation |
| Session Signal Audit | `/memesis:analyze-sessions` | What did the pipeline miss from a specific session? |
| Next Steps Discussion | `/memesis:discuss` | What should we work on? What's the project state? |

---

## System at a Glance

**Pipeline:**
```
transcript → SessionStart hook   → inject + rehydrate
           → UserPromptSubmit    → dynamic retrieval
           → PreCompact hook     → extract observations → Consolidator → Memory rows
                                                       → Lifecycle → stage transitions
                                                       → Linking   → semantic edges
```

**Stage order:** `ephemeral → consolidated → crystallized → instinctive`

**Three retrieval tiers** (`core/retrieval.py`): instinctive (always), crystallized (context-matched), active search (agent-initiated FTS + vec).

**Key invariants:**
- All DB access via Peewee `db` singleton — never `sqlite3.connect()` directly
- `SHADOW_ONLY=True` in `core/observability.py` — prune logic logs but does NOT execute
- FTS SQL splitter is naive: never put `;` inside `--` comments in migration SQL
- `uv run pytest tests/` from project root (not bare `python3`)

**For full data model:** read `.claude/skills/index/references/data-model.md`
**For design decisions and panel consensus:** read `.claude/skills/index/references/design-decisions.md`
**For all algorithms, packages, concepts, and patterns:** read `.claude/skills/index/references/glossary.md`

---

## Quick Diagnostics

Standard setup (run from project root):

```python
import os, sys
sys.path.insert(0, os.environ.get("CLAUDE_PLUGIN_ROOT", os.getcwd()))
from core.database import init_db
from core.models import Memory, ConsolidationLog, Observation, RetrievalLog
init_db(project_context=os.getcwd())
```

Common one-liners:

```python
# Memories stuck in ephemeral > 7 days
from datetime import datetime, timedelta
cutoff = (datetime.now() - timedelta(days=7)).isoformat()
stuck = Memory.select().where(Memory.stage=='ephemeral', Memory.created_at<cutoff, Memory.archived_at.is_null())

# Observations that didn't produce memories
failed = Observation.select().where(Observation.memory_id.is_null(), Observation.status!='filtered')

# High-importance memories never injected
neglected = Memory.select().where(Memory.importance>0.7, Memory.injection_count==0, Memory.archived_at.is_null())

# Recent consolidation log
recent = ConsolidationLog.select().order_by(ConsolidationLog.timestamp.desc()).limit(20)
```

Run full scripts:
```bash
uv run python skills/ops/scripts/lifecycle_audit.py
uv run python skills/ops/scripts/observation_audit.py
uv run python skills/ops/scripts/session_trace.py <session-id>
```

---

## Steelmanning Protocol

Every recommendation before it is stated:

1. **Against:** State the strongest case against your proposal (2-4 sentences). What assumptions does it require? What would flip it?
2. **Falsifying condition:** Name the one thing that, if true, would make the opposition win.
3. **Tiebreaker:** Cite specific evidence (code, log data, test results) that breaks the tie in your favor.
4. **Recommendation:** State it.

---

## Key File Map

```
core/
  models.py           # All DB models
  lifecycle.py        # Stage transition rules
  consolidator.py     # LLM curation pipeline (PreCompact)
  retrieval.py        # Three-tier injection engine
  linking.py          # Cosine similarity linking (threshold=0.72)
  observability.py    # Activation formula; SHADOW_ONLY flag
  self_reflection.py  # Hypothesis promotion; SelfReflector
  relevance.py        # Archival/rehydration scoring
  prompts.py          # All LLM prompt templates
  schemas.py          # Pydantic schemas for LLM responses

hooks/
  session_start.py        # SessionStart
  user_prompt_inject.py   # UserPromptSubmit
  pre_compact.py          # PreCompact: extract + consolidate
  consolidate_cron.py     # Background consolidation runner

tests/                    # uv run pytest tests/
  conftest.py             # Fixtures: tmp_path DB, session isolation
  test_lifecycle.py
  test_consolidator.py    # 80KB — most comprehensive
  test_retrieval.py       # 89KB

.planning/                # Design decisions, panel consensus records
.context/codebase/        # Architecture map
```
