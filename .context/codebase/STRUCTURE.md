# Directory Structure

_Last updated: 2026-05-06. Prior version dated 2026-04-10._

## Layout

```
/Users/emmahyde/projects/memesis/
├── core/                         # Domain logic — no CLI, no hooks
│   ├── __init__.py               # Package exports: init_db, close_db, Memory, etc.
│   ├── affect.py                 # Affect tracking: frustration, satisfaction, momentum
│   ├── card_validators.py        # Issue-card post-processing: index validity, circular-evidence demotion [NEW T1/T2]
│   ├── coherence.py              # Ghost coherence check for self-model validation
│   ├── consolidator.py           # LLM-based memory curation (PreCompact); card→Memory field promotion
│   ├── crystallizer.py           # Episodic-to-semantic transformation
│   ├── database.py               # DB init, migrations, VecStore singleton, close_db
│   ├── embeddings.py             # Bedrock Titan v2 embedding service
│   ├── extraction_affect.py      # Window-level affect aggregation + importance prior for Stage 1 [NEW T1/T2]
│   ├── feedback.py               # Usage tracking, importance scoring
│   ├── flags.py                  # Feature flags (JSON-based, cached)
│   ├── graph.py                  # Edge computation, 1-hop expansion
│   ├── habituation.py            # Routine event suppression filter
│   ├── ingest.py                 # Native Claude Code memory bridge
│   ├── issue_cards.py            # Stage 1.5: issue-card synthesis + extract_card_memory_fields() [NEW T1/T2]
│   ├── lifecycle.py              # Stage promotion/demotion state machine
│   ├── llm.py                    # Anthropic/Bedrock client selection, call_llm()
│   ├── manifest.py               # MEMORY.md index generator
│   ├── models.py                 # Peewee ORM: Memory, MemoryEdge, NarrativeThread, etc. [schema additions T1/T2/T3]
│   ├── orienting.py              # Novelty/salience detection for observations
│   ├── prompts.py                # All LLM prompt templates
│   ├── reconsolidation.py        # Session-evidence comparison + causal edges
│   ├── relevance.py              # Scoring, archival, rehydration
│   ├── replay.py                 # Salience-based observation sort
│   ├── retrieval.py              # Multi-tier injection, hybrid RRF, Thompson sampling
│   ├── rule_registry.py          # Confirmed-rule → ParameterOverrides; RULE_OVERRIDES dict [NEW T2]
│   ├── self_reflection.py        # Periodic self-model updates (memory-system level)
│   ├── self_reflection_extraction.py  # Extraction-process metacognition: rules, audit, chunking [NEW T1/T2]
│   ├── somatic.py                # Valence classification + importance boost
│   ├── spaced.py                 # SM-2 spaced repetition for injection eligibility
│   ├── threads.py                # Narrative arc detection and synthesis
│   ├── transcript_ingest.py      # Full extraction pipeline: Stage 1 → 1.5; integrates all new subsystems
│   ├── validators.py             # Raw observation validators (pre-Stage 1.5)
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
│   ├── test_card_validators.py   # Tests for core/card_validators.py [NEW]
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
│   ├── test_issue_cards.py       # Tests for core/issue_cards.py [NEW]
│   ├── test_lifecycle.py
│   ├── test_llm.py
│   ├── test_manifest.py
│   ├── test_migration_w5.py      # W5 schema migration tests [NEW]
│   ├── test_models.py
│   ├── test_orienting.py
│   ├── test_prompts.py
│   ├── test_reconsolidation.py
│   ├── test_recurrent_failure_rule.py  # Cross-session meta-rule test [NEW]
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
│   ├── capture_baseline.py
│   ├── continuity_test.py
│   ├── curation_audit.py
│   ├── experiment.py
│   ├── judge_eval.py
│   ├── judges.py
│   ├── live_retrieval_test.py
│   ├── longmemeval_adapter.py
│   ├── longmemeval_test.py
│   ├── metrics.py
│   ├── metrics_test.py
│   ├── needle_test.py
│   ├── report.py
│   ├── spontaneous_recall.py
│   ├── staleness_test.py
│   ├── validate_judge.py
│   └── verify_phase.py
│
├── scripts/                      # Developer CLI utilities
│   ├── audit_lifecycle.py        # Memory lifecycle audit
│   ├── audit_pipeline_dimensions.py  # Pipeline quality dimensions audit [NEW T2]
│   ├── compare.py
│   ├── compute_baseline.py
│   ├── consolidate.py
│   ├── cost.py
│   ├── dashboard.py
│   ├── diagnose.py
│   ├── embed_backfill.py
│   ├── eval_protocol.py          # Evaluation protocol runner [NEW]
│   ├── heartbeat.py
│   ├── install-deps.sh
│   ├── migrate_stage15_fields.py # One-shot migration for Stage 1.5 schema additions [NEW T1/T2]
│   ├── migrate_tier3_fields.py   # One-shot migration for Tier-3 fields (criterion_weights, rejected_options) [NEW T3]
│   ├── migrate_w5_schema.py      # W5 Wave 5 schema migration
│   ├── observer_api.py
│   ├── prune_sweep.py
│   ├── reduce.py
│   ├── registry_status.py        # CLI for rule_registry RULE_METADATA — shows wired knobs [NEW T2]
│   ├── run_pipeline_audit.py     # Full pipeline audit runner
│   ├── run_selected_sessions.py  # Multi-session extraction sweep [NEW T2]
│   ├── scan.py
│   ├── seed.py
│   └── transcript_cron.py
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
| Stage 1.5 issue-card synthesis | `core/issue_cards.py` |
| Window-level affect aggregation | `core/extraction_affect.py` |
| Card post-processing validators | `core/card_validators.py` |
| Extraction-process self-reflection | `core/self_reflection_extraction.py` |
| Confirmed-rule → parameter overrides | `core/rule_registry.py` |
| Rule registry CLI | `scripts/registry_status.py` |
| Stage 1.5 / Tier-3 schema migrations | `scripts/migrate_stage15_fields.py`, `scripts/migrate_tier3_fields.py` |
| Runtime memory storage | `~/.claude/projects/<hash>/memory/` (project-scoped) or `~/.claude/memory/` (global) |
| SQLite database | `{memory_dir}/index.db` |
| Ephemeral observation buffer | `{memory_dir}/ephemeral/session-YYYY-MM-DD.md` |
| Feature flags override | `{memory_dir}/flags.json` |
| Manifest index | `{memory_dir}/MEMORY.md` |
| Consolidation counter | `{memory_dir}/meta/consolidation-count.json` |
| Affect state | `{memory_dir}/affect/` |
| Self-reflection model doc | `~/.claude/memesis/self-model/self_model.md` |
| Self-reflection audit log | `~/.claude/memesis/self-model/self_observations.jsonl` |
| Cron log | `/tmp/memory-consolidation.log` |

## Naming Conventions

- **Files:** `snake_case.py` throughout. Hook scripts are verbs/actions (`pre_compact.py`, `session_start.py`, `user_prompt_inject.py`). Core modules are nouns (`consolidator.py`, `lifecycle.py`, `retrieval.py`).
- **Directories:** `snake_case` for Python packages (`core/`, `hooks/`, `tests/`). Skills use `kebab-case` (`run-eval/`). Context directories use lowercase (`codebase/`, `research/`).
- **Modules:** Match file names exactly. No aliasing. All `core.*` imports use relative imports within the package (e.g., `from .models import Memory`). Hook scripts use `sys.path.insert(0, ...)` to resolve `core.*` without installation.
- **Classes:** `PascalCase`. One primary class per file in `core/` (e.g., `Consolidator`, `Crystallizer`, `RelevanceEngine`, `VecStore`). Some modules export module-level functions instead of classes (`reconsolidation.py` exports `reconsolidate()`, `graph.py` exports `compute_edges()` and `expand_neighbors()`). New modules follow the same pattern: `issue_cards.py` exports `synthesize_issue_cards()` and `extract_card_memory_fields()`; `extraction_affect.py` exports `aggregate_window_affect()`, `apply_affect_prior()`, `format_affect_hint()`; `rule_registry.py` exports `resolve_overrides()` and the `ParameterOverrides` dataclass; `self_reflection_extraction.py` exports `reflect_on_extraction()`, `aggregate_audit()`, `select_chunking()`.
- **Test files:** `test_{module_name}.py`, mirroring the `core/` module they cover. Some test files cover cross-cutting concerns (`test_integration.py`, `test_saturation_integration.py`, `test_causal_edges.py`, `test_recurrent_failure_rule.py`).
- **Memory files on disk:** Written into `{stage}/{path}` within the store's `base_dir`. Stage directories: `ephemeral/`, `consolidated/`, `crystallized/`, `instinctive/`, plus `archived/` and `meta/`.
- **Memory IDs:** UUID4 strings, stored in the SQLite `memories.id` column and returned by all create operations.
- **Pseudo-IDs for pruned observations:** `pruned-{md5[:8]}` — deterministic from content, used in consolidation log for audit tracing without creating a real memory record.
- **Edge types:** Lowercase with underscores: `thread_neighbor`, `tag_cooccurrence`, `caused_by`, `refined_from`, `subsumed_into`, `contradicts`, `echo`.
- **Feature flag names:** Lowercase with underscores: `thompson_sampling`, `causal_edges`, `contradiction_tensors`.
- **Rule IDs in self-reflection:** Lowercase with underscores, matching function `__rule_id__` attribute: `low_productive_rate`, `chunking_suboptimal`, `synthesis_overgreedy`, `recurrent_agent_failure`, etc.
- **ParameterOverrides fields:** Lowercase with underscores, matching `ParameterOverrides` dataclass fields. Defaults in the dataclass are the source of truth for current hardcoded extraction values.
- **criterion_weights / rejected_options on Memory:** Stored as JSON TEXT in SQLite. Write via `json.dumps()` in `consolidator.py`; read via `json.loads()` where needed. NULL when the memory was not derived from a decision-kind card.
- **Project directory hashing:** `re.sub(r'[^a-zA-Z0-9-]', '-', project_path)` — replaces all non-alphanumeric-non-dash characters with `-`. This mirrors Claude Code's native convention.
- **Python version:** See `pyproject.toml` (`requires-python = ">=3.10"`).
