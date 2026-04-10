# Directory Structure

## Layout

```
/Users/emma.hyde/projects/memesis/
├── core/                         # Domain logic — no CLI, no hooks
│   ├── __init__.py               # Package exports: init_db, close_db, Memory, etc.
│   ├── affect.py                 # Affect tracking: frustration, satisfaction, momentum
│   ├── coherence.py              # Ghost coherence check for self-model validation
│   ├── consolidator.py           # LLM-based memory curation (PreCompact)
│   ├── crystallizer.py           # Episodic-to-semantic transformation
│   ├── database.py               # DB init, migrations, VecStore singleton, close_db
│   ├── embeddings.py             # Bedrock Titan v2 embedding service
│   ├── feedback.py               # Usage tracking, importance scoring
│   ├── flags.py                  # Feature flags (JSON-based, cached)
│   ├── graph.py                  # Edge computation, 1-hop expansion
│   ├── habituation.py            # Routine event suppression filter
│   ├── ingest.py                 # Native Claude Code memory bridge
│   ├── lifecycle.py              # Stage promotion/demotion state machine
│   ├── llm.py                    # Anthropic/Bedrock client selection, call_llm()
│   ├── manifest.py               # MEMORY.md index generator
│   ├── models.py                 # Peewee ORM: Memory, MemoryEdge, NarrativeThread, etc.
│   ├── orienting.py              # Novelty/salience detection for observations
│   ├── prompts.py                # All LLM prompt templates
│   ├── reconsolidation.py        # Session-evidence comparison + causal edges
│   ├── relevance.py              # Scoring, archival, rehydration
│   ├── replay.py                 # Salience-based observation sort
│   ├── retrieval.py              # Multi-tier injection, hybrid RRF, Thompson sampling
│   ├── self_reflection.py        # Periodic self-model updates
│   ├── somatic.py                # Valence classification + importance boost
│   ├── spaced.py                 # SM-2 spaced repetition for injection eligibility
│   ├── threads.py                # Narrative arc detection and synthesis
│   └── vec.py                    # sqlite-vec via apsw — KNN, embedding storage
│
├── hooks/                        # Claude Code hook entry points
│   ├── __init__.py
│   ├── hooks.json                # Hook registration (SessionStart, PreCompact, UserPromptSubmit)
│   ├── append_observation.py     # Called by /memesis:learn skill
│   ├── consolidate_cron.py       # Hourly headless cron worker
│   ├── pre_compact.py            # PreCompact hook (30 s timeout)
│   ├── session_start.py          # SessionStart hook (5 s timeout)
│   └── user_prompt_inject.py     # UserPromptSubmit hook (3 s timeout)
│
├── tests/                        # pytest unit + integration tests
│   ├── conftest.py               # Shared fixtures: tmp_path DB init, cleanup
│   ├── test_affect.py
│   ├── test_causal_edges.py
│   ├── test_coherence.py
│   ├── test_consolidator.py
│   ├── test_crystallizer.py
│   ├── test_feedback.py
│   ├── test_graph.py
│   ├── test_habituation.py
│   ├── test_hooks.py
│   ├── test_ingest.py
│   ├── test_integration.py
│   ├── test_lifecycle.py
│   ├── test_llm.py
│   ├── test_manifest.py
│   ├── test_models.py
│   ├── test_orienting.py
│   ├── test_reconsolidation.py
│   ├── test_relevance.py
│   ├── test_replay.py
│   ├── test_retrieval.py
│   ├── test_saturation_integration.py
│   ├── test_scripts.py
│   ├── test_self_reflection.py
│   ├── test_skills.py
│   ├── test_somatic.py
│   ├── test_spaced.py
│   ├── test_threads.py
│   └── test_user_prompt_inject.py
│
├── eval/                         # Offline quality evaluation harness
│   ├── conftest.py
│   ├── capture_baseline.py       # Capture baseline metrics for comparison
│   ├── continuity_test.py        # Memory continuity across sessions
│   ├── curation_audit.py         # Keep-rate and correction-rate analysis
│   ├── experiment.py             # Experiment runner for feature flag A/B testing
│   ├── judge_eval.py             # LLM judge evaluation framework
│   ├── judges.py                 # Judge definitions for eval
│   ├── live_retrieval_test.py    # Live retrieval quality checks
│   ├── longmemeval_adapter.py    # LongMemEval benchmark adapter
│   ├── longmemeval_test.py       # LongMemEval benchmark runner
│   ├── metrics.py                # Metric definitions and computation
│   ├── metrics_test.py           # Metric unit tests
│   ├── needle_test.py            # Retrieval precision for planted facts
│   ├── report.py                 # Report generation from eval results
│   ├── spontaneous_recall.py     # Unprompted memory surfacing quality
│   ├── staleness_test.py         # Archival/rehydration timing checks
│   ├── validate_judge.py         # Judge validation harness
│   └── verify_phase.py           # Phase milestone verification
│
├── scripts/                      # Developer CLI utilities
│   ├── compare.py                # Compare memory stores or configurations
│   ├── consolidate.py            # One-shot consolidation for a buffer
│   ├── cost.py                   # LLM cost analysis and attribution
│   ├── dashboard.py              # Terminal dashboard for memory stats
│   ├── diagnose.py               # Health check / store inspection
│   ├── embed_backfill.py         # Backfill missing embeddings
│   ├── heartbeat.py              # Liveness check for cron
│   ├── install-deps.sh           # Dependency installer (venv + pip)
│   ├── reduce.py                 # Bulk reduction / pruning tool (TF-IDF dedup)
│   ├── scan.py                   # Scan memory dirs, print stats
│   └── seed.py                   # Seed a store with test memories
│
├── skills/                       # Claude skill definitions (SKILL.md files)
│   ├── backfill/                 # /memesis:backfill
│   ├── connect/                  # /memesis:connect
│   ├── dashboard/                # /memesis:dashboard
│   ├── forget/                   # /memesis:forget
│   ├── health/                   # /memesis:health
│   ├── ideate/                   # /memesis:ideate
│   ├── learn/                    # /memesis:learn
│   ├── recall/                   # /memesis:recall
│   ├── reflect/                  # /memesis:reflect
│   ├── run-eval/                 # /memesis:run-eval
│   ├── stats/                    # /memesis:stats
│   ├── teach/                    # /memesis:teach
│   ├── threads/                  # /memesis:threads
│   └── usage/                    # /memesis:usage
│
├── docs/                         # Design documents and specs
│   └── relationship-engine-spec.md
│
├── .context/                     # Codebase context documents
│   ├── codebase/                 # Architecture, structure, conventions, stack, etc.
│   └── research/                 # Ecosystem research (stack, pitfalls)
│
├── .planning/                    # Phase planning documents
├── backfill-output/              # Backfill run artifacts (.jsonl, .db)
├── backtest-output/              # Backtest run artifacts (.jsonl)
│
├── pyproject.toml                # Build config + dependency declarations
├── pytest.ini                    # Test runner config
├── requirements.txt              # Pinned dev dependencies
├── AGENTS.md                     # Agent/skill index
├── CLAUDE.md                     # Project-level Claude instructions
└── README.md
```

## Key Locations

| What | Where |
| --- | --- |
| Source code (core library) | `core/` |
| Hook entry points | `hooks/` |
| Hook registration config | `hooks/hooks.json` |
| Unit + integration tests | `tests/` |
| Eval harness (quality metrics) | `eval/` |
| Developer scripts | `scripts/` |
| Claude skill definitions | `skills/` (each has a `SKILL.md`) |
| Build / dependency config | `pyproject.toml` |
| Test runner config | `pytest.ini` |
| LLM prompt templates | `core/prompts.py` |
| Feature flag definitions | `core/flags.py` |
| LLM transport layer | `core/llm.py` |
| ORM models | `core/models.py` |
| Database init + migrations | `core/database.py` |
| Embedding service | `core/embeddings.py` |
| Vector store | `core/vec.py` |
| Runtime memory storage | `~/.claude/projects/<hash>/memory/` (project-scoped) or `~/.claude/memory/` (global) |
| SQLite database | `{memory_dir}/index.db` |
| Ephemeral observation buffer | `{memory_dir}/ephemeral/session-YYYY-MM-DD.md` |
| Feature flags override | `{memory_dir}/flags.json` |
| Manifest index | `{memory_dir}/MEMORY.md` |
| Consolidation counter | `{memory_dir}/meta/consolidation-count.json` |
| Affect state | `{memory_dir}/affect/` |
| Cron log | `/tmp/memory-consolidation.log` |

## Naming Conventions

- **Files:** `snake_case.py` throughout. Hook scripts are verbs/actions (`pre_compact.py`, `session_start.py`, `user_prompt_inject.py`). Core modules are nouns (`consolidator.py`, `lifecycle.py`, `retrieval.py`).
- **Directories:** `snake_case` for Python packages (`core/`, `hooks/`, `tests/`). Skills use `kebab-case` (`run-eval/`). Context directories use lowercase (`codebase/`, `research/`).
- **Modules:** Match file names exactly. No aliasing. All `core.*` imports use relative imports within the package (e.g., `from .models import Memory`). Hook scripts use `sys.path.insert(0, ...)` to resolve `core.*` without installation.
- **Classes:** `PascalCase`. One primary class per file in `core/` (e.g., `Consolidator`, `Crystallizer`, `RelevanceEngine`, `VecStore`). Some modules export module-level functions instead of classes (`reconsolidation.py` exports `reconsolidate()`, `graph.py` exports `compute_edges()` and `expand_neighbors()`).
- **Test files:** `test_{module_name}.py`, mirroring the `core/` module they cover. Some test files cover cross-cutting concerns (`test_integration.py`, `test_saturation_integration.py`, `test_causal_edges.py`).
- **Memory files on disk:** Written into `{stage}/{path}` within the store's `base_dir`. Stage directories: `ephemeral/`, `consolidated/`, `crystallized/`, `instinctive/`, plus `archived/` and `meta/`.
- **Memory IDs:** UUID4 strings, stored in the SQLite `memories.id` column and returned by all create operations.
- **Pseudo-IDs for pruned observations:** `pruned-{md5[:8]}` -- deterministic from content, used in consolidation log for audit tracing without creating a real memory record.
- **Edge types:** Lowercase with underscores: `thread_neighbor`, `tag_cooccurrence`, `caused_by`, `refined_from`, `subsumed_into`, `contradicts`, `echo`.
- **Feature flag names:** Lowercase with underscores: `thompson_sampling`, `causal_edges`, `contradiction_tensors`.
- **Project directory hashing:** `re.sub(r'[^a-zA-Z0-9-]', '-', project_path)` -- replaces all non-alphanumeric-non-dash characters with `-`. This mirrors Claude Code's native convention.
- **Python version:** See `pyproject.toml` (`requires-python = ">=3.10"`).
