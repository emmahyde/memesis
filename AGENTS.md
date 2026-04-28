# AGENTS.md — memesis

AI assistant guidance for working in the `memesis/` subdirectory.

## Directory Structure

```
memesis/
  core/                  # Python package — models, storage, lifecycle, retrieval, curation
  hooks/                 # Claude Code hook entry points (SessionStart, PreCompact, UserPromptSubmit)
  skills/                # Skill directories (14 skills — see skills/ listing)
  scripts/               # CLI utilities (consolidate, diagnose, dashboard, etc.)
  tests/                 # pytest unit + integration tests
  eval/                  # Eval harness (inspect-ai / ragas / deepeval)
  pyproject.toml         # Package metadata + dependencies (source of truth for versions)
  pytest.ini             # Test discovery config
  requirements.txt       # Legacy pin (superseded by pyproject.toml)
```

Core has ~25 modules. Read `core/__init__.py` for the public API. Key subsystems:
- **Storage/persistence:** `database.py`, `models.py`, `manifest.py`, `vec.py`, `embeddings.py`
- **Lifecycle:** `lifecycle.py`, `consolidator.py`, `crystallizer.py`, `reconsolidation.py`, `spaced.py`
- **Retrieval:** `retrieval.py`, `relevance.py`, `graph.py`, `threads.py`
- **Cognitive models:** `affect.py`, `coherence.py`, `habituation.py`, `orienting.py`, `self_reflection.py`, `somatic.py`, `replay.py`
- **Infrastructure:** `llm.py`, `prompts.py`, `flags.py`, `ingest.py`, `feedback.py`

## Test Commands

Run from the `memesis/` directory:

```bash
# Full unit test suite
python3 -m pytest tests/

# Single test file
python3 -m pytest tests/test_storage.py

# Single test by name
python3 -m pytest tests/test_consolidator.py -k test_consolidate_session

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

Database lives at `{base_dir}/index.db`. WAL mode enabled. Schema is defined in `core/database.py` — read that file for the authoritative table definitions, migrations, and constraints. Key tables: `memories`, `memories_fts` (FTS5), `retrieval_log`, `consolidation_log`.

## Hook Behavior

Three hooks registered in `hooks/hooks.json`:

### SessionStart (`hooks/session_start.py`)

1. Injects memory context into the system prompt via stdout.
2. Three-tier retrieval: instinctive → crystallized → FTS search.
3. Logs each injected memory. Timeout: 5s.

### PreCompact (`hooks/pre_compact.py`)

1. Privacy-filters ephemeral session content, then LLM-curates.
2. Executes decisions: `keep` → consolidated memory, `prune` → log only, `promote` → reinforce.
3. Checks stage advancement eligibility. Timeout: 30s.

### UserPromptSubmit (`hooks/user_prompt_inject.py`)

1. Appends context-relevant memories to the user prompt. Timeout: 3s.

## Key Entry Points

Start here when navigating the codebase:

| File                   | Purpose                                                              |
| ---------------------- | -------------------------------------------------------------------- |
| `core/__init__.py`     | Public API — all exports. Read this first.                           |
| `core/database.py`     | SQLite schema, migrations, WAL mode. Source of truth for all tables. |
| `core/models.py`       | Data models and enums (Memory, Stage, etc.).                         |
| `core/llm.py`          | Shared LLM transport: `call_llm()`, model constants.                 |
| `core/prompts.py`      | All prompt templates + `EMOTIONAL_STATE_PATTERNS`.                   |
| `hooks/hooks.json`     | Hook registration — which hooks fire when, with timeouts.            |

## Skills

14 skills in `skills/`. Always use the full `/memesis:` prefix in docs and code:

`/memesis:learn`, `/memesis:recall`, `/memesis:forget`, `/memesis:reflect`, `/memesis:teach`, `/memesis:connect`, `/memesis:threads`, `/memesis:health`, `/memesis:stats`, `/memesis:usage`, `/memesis:dashboard`, `/memesis:ideate`, `/memesis:backfill`, `/memesis:run-eval`

Each skill is a directory under `skills/` containing at minimum a markdown definition file.

## Conventions

- Stage names are lowercase strings: `ephemeral`, `consolidated`, `crystallized`, `instinctive`.
- `importance` is a float in [0.0, 1.0]; the SQLite CHECK constraint enforces this.
- Runtime deps listed in `pyproject.toml` — includes `anthropic`, `nltk`, `peewee`, `scikit-learn`, `sqlite-vec`, `apsw`, `boto3`.
