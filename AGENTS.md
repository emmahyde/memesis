# AGENTS.md — memesis

AI assistant guidance for working in the `memesis/` subdirectory.

## Directory Structure

```
memesis/
  core/                  # Python package — storage, lifecycle, retrieval, curation
    __init__.py          # Exports MemoryStore, LifecycleManager
    storage.py           # MemoryStore — dual-write markdown + SQLite FTS5
    lifecycle.py         # LifecycleManager — stage transitions with validation
    retrieval.py         # RetrievalEngine — three-tier injection + FTS search
    consolidator.py      # Consolidator — LLM-based curation at PreCompact
    llm.py               # Shared LLM transport — call_llm(), model constants
    manifest.py          # ManifestGenerator — writes MEMORY.md index
    feedback.py          # FeedbackLoop — usage tracking, importance scoring
    prompts.py           # Prompt templates + EMOTIONAL_STATE_PATTERNS list
  hooks/                 # Claude Code hook entry points
    session_start.py     # SessionStart hook — injects memory context
    pre_compact.py       # PreCompact hook — runs consolidation
  skills/                # Skill markdown files
    learn.md             # /memesis:learn skill definition
    memory.md            # /memesis:memory skill definition
    forget.md            # /memesis:forget skill definition
  tests/                 # pytest unit tests
    conftest.py          # Shared fixtures (tmp MemoryStore, etc.)
    test_storage.py      # MemoryStore CRUD + FTS
    test_lifecycle.py    # LifecycleManager transitions
    test_retrieval.py    # RetrievalEngine tiers + token budget
    test_consolidator.py # Consolidator decisions + privacy filter
    test_manifest.py     # ManifestGenerator output format
    test_feedback.py     # FeedbackLoop usage tracking + importance
  eval/                  # Eval harness (inspect-ai / ragas / deepeval)
  pyproject.toml         # Package metadata + dependencies
  pytest.ini             # Test discovery config
  .claude-plugin         # Plugin manifest (hooks, skills, config)
  requirements.txt       # Legacy pin (superseded by pyproject.toml)
```

## Test Commands

Run from the `memesis/` directory:

```bash
# Full unit test suite
python3 -m pytest tests/

# Single test file
python3 -m pytest tests/test_storage.py

# Single test by name
python3 -m pytest tests/test_consolidator.py -k test_filter_privacy

# With verbose output
python3 -m pytest tests/ -v

# Eval harness (requires eval extras installed)
python3 -m pytest eval/
```

Install dev dependencies first if needed:

```bash
pip install -e ".[dev]"          # from memesis/ dir
# or from repo root:
pip install -e "memesis/[dev]"
```

## Import Pattern

All `core` modules use package-relative imports internally:

```python
from core.storage import MemoryStore
from core.lifecycle import LifecycleManager
from core.retrieval import RetrievalEngine
from core.consolidator import Consolidator
from core.manifest import ManifestGenerator
from core.feedback import FeedbackLoop
```

Hook scripts and tests that run outside the package must add the
`memesis/` directory to `sys.path`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(__file__))  # or explicit path
from core.storage import MemoryStore
```

The `conftest.py` fixture already handles this for the test suite.

## SQLite Schema

Database lives at `{base_dir}/index.db`. WAL mode is enabled.

### `memories` table

| Column                | Type    | Description                                                |
| --------------------- | ------- | ---------------------------------------------------------- |
| `id`                  | TEXT PK | UUID v4                                                    |
| `file_path`           | TEXT    | Absolute path to the markdown file                         |
| `stage`               | TEXT    | `ephemeral`, `consolidated`, `crystallized`, `instinctive` |
| `title`               | TEXT    | Display name                                               |
| `summary`             | TEXT    | ~150-char description                                      |
| `tags`                | TEXT    | JSON array of strings                                      |
| `importance`          | REAL    | 0.0–1.0, default 0.5                                       |
| `reinforcement_count` | INTEGER | Times this memory has been reinforced                      |
| `created_at`          | TEXT    | ISO 8601                                                   |
| `updated_at`          | TEXT    | ISO 8601                                                   |
| `last_injected_at`    | TEXT    | ISO 8601, nullable                                         |
| `last_used_at`        | TEXT    | ISO 8601, nullable                                         |
| `injection_count`     | INTEGER | Total injection count                                      |
| `usage_count`         | INTEGER | Total times marked as used                                 |
| `project_context`     | TEXT    | Project path hash, nullable                                |
| `source_session`      | TEXT    | Session ID at creation, nullable                           |
| `content_hash`        | TEXT    | MD5 of full markdown content (dedup)                       |

### `memories_fts` virtual table (FTS5)

Content table backed by `memories`. Indexes: `title`, `summary`, `tags`,
`content`. Manually synced on create/update/delete.

### `retrieval_log` table

| Column            | Type       | Description                                    |
| ----------------- | ---------- | ---------------------------------------------- |
| `id`              | INTEGER PK | Autoincrement                                  |
| `timestamp`       | TEXT       | ISO 8601                                       |
| `session_id`      | TEXT       | Session identifier                             |
| `memory_id`       | TEXT       | FK → memories.id                               |
| `retrieval_type`  | TEXT       | `injected`, `active_search`, `user_prompted`   |
| `was_used`        | INTEGER    | 0/1 — updated by FeedbackLoop                  |
| `relevance_score` | REAL       | Optional score, nullable                       |
| `project_context` | TEXT       | Project dir where injection occurred, nullable |

### `consolidation_log` table

| Column       | Type       | Description                                                     |
| ------------ | ---------- | --------------------------------------------------------------- |
| `id`         | INTEGER PK | Autoincrement                                                   |
| `timestamp`  | TEXT       | ISO 8601                                                        |
| `session_id` | TEXT       | Session identifier, nullable                                    |
| `action`     | TEXT       | `kept`, `pruned`, `promoted`, `demoted`, `merged`, `deprecated` |
| `memory_id`  | TEXT       | FK → memories.id                                                |
| `from_stage` | TEXT       | Source stage                                                    |
| `to_stage`   | TEXT       | Target stage                                                    |
| `rationale`  | TEXT       | Explanation from LLM or lifecycle logic                         |

## Hook Behavior

### SessionStart (`hooks/session_start.py`)

1. Instantiates `MemoryStore` and `RetrievalEngine`.
2. Calls `RetrievalEngine.inject_for_session(session_id, project_context)`.
3. Writes the resulting `---MEMORY CONTEXT---` block to stdout (Claude Code
   reads stdout from hook commands and prepends it to the system prompt).
4. Logs each injected memory via `store.record_injection()`.
5. Timeout: 5 000 ms (configured in `.claude-plugin`).

### PreCompact (`hooks/pre_compact.py`)

1. Reads the ephemeral session file for the current session.
2. Instantiates `MemoryStore`, `LifecycleManager`, and `Consolidator`.
3. Calls `Consolidator.consolidate_session(ephemeral_path, session_id)`.
4. The consolidator privacy-filters the content (strips emotional state lines),
   builds a manifest summary of existing memories, and calls the Anthropic API
   with `CONSOLIDATION_PROMPT`.
5. Executes LLM decisions: `keep` → create consolidated memory, `prune` →
   log only, `promote` → increment reinforcement_count.
6. Checks newly promoted memories for stage advancement eligibility.
7. Timeout: 30 000 ms (configured in `.claude-plugin`).

## Key Files

| File                   | Class               | Purpose                                                                        |
| ---------------------- | ------------------- | ------------------------------------------------------------------------------ |
| `core/storage.py`      | `MemoryStore`       | CRUD with atomic dual-write (markdown + SQLite). Use this for all persistence. |
| `core/lifecycle.py`    | `LifecycleManager`  | Stage transitions with promotion/demotion rules. Wraps `MemoryStore`.          |
| `core/retrieval.py`    | `RetrievalEngine`   | Three-tier injection (instinctive → crystallized → FTS).                       |
| `core/consolidator.py` | `Consolidator`      | LLM-based curation. Owns the privacy filter.                                   |
| `core/llm.py`          | —                   | Shared LLM transport: `call_llm()`, model constants, fence stripping.          |
| `core/manifest.py`     | `ManifestGenerator` | Writes `MEMORY.md` index for human inspection.                                 |
| `core/feedback.py`     | `FeedbackLoop`      | Usage heuristic (2+ keyword hits), importance score deltas.                    |
| `core/prompts.py`      | —                   | `CONSOLIDATION_PROMPT` template and `EMOTIONAL_STATE_PATTERNS` regex list.     |

## Skill Invocations

Always use the full form in documentation and skill files:

- `/memesis:learn` — store a new observation
- `/memesis:memory` — search or inspect memories
- `/memesis:forget` — deprecate or delete a memory

Do not use shorthand forms (`:learn`, `learn`, etc.).

## Conventions

- Python 3.10+, no third-party deps at runtime except `anthropic`.
- All file I/O goes through `MemoryStore` — do not write memory files directly.
- Atomic writes use `tempfile.mkstemp` + `shutil.move`.
- Privacy filter runs before any LLM call — never bypass it.
- All LLM calls go through `core.llm.call_llm()` — do not create
  `anthropic.Anthropic()` clients directly in service modules.
- Stage names are lowercase strings: `ephemeral`, `consolidated`,
  `crystallized`, `instinctive`.
- `importance` is a float in [0.0, 1.0]; the SQLite CHECK constraint enforces
  this.
- Tests use a temporary directory fixture from `conftest.py`; never write to
  `~/.claude/memory` in tests.
