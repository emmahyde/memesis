# Single Global DB + Project Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse memesis's per-project SQLite databases into one canonical global DB at `~/.claude/memesis/index.db`, with a populated `project` column scoping memories and observations.

**Architecture:** `_resolve_db_path` stops branching on `project_context`/`base_dir` for the *path* — it always returns the one fixed location (`base_dir` survives only as a test override). The `project` column (already added by migration 0006, currently 100% NULL) becomes the live per-project dimension: written at memory/observation creation, read as a soft filter/scoring signal in retrieval. The legacy parallel `project_context` column is retired. A one-time migration relocates the existing 57.5MB DB and backfills `project`.

**Tech Stack:** Python 3.10+, Peewee ORM, SQLite (WAL), pytest, `uv` for env.

---

## Background — verified current state

- Real DB: `~/.claude/projects/-Users-emmahyde-projects-memesis/memory/index.db` (57.5MB, 316 memories), resolved only via `project_context`.
- `init_db()` with no args → `~/.claude/memory/index.db` (empty). Bare callers (`migrate_*.py`, `compare.py`, `observer_api.py`) silently hit the empty DB.
- `memories.project` (migration 0006, indexed) — **all 316 NULL**. `memories.project_context` — 315 NULL, 1 set. Both effectively dead.
- `observations` table has `project`; `retrieval_log` has `project_context` — naming is inconsistent across tables.
- Retrieval filters on `project_context` (`core/retrieval.py`), but since the column is ~100% NULL the filter is currently a no-op.
- Every other `projects/*/memory/index.db` with rows is a **pytest temp-dir junk DB** — a test passes a tmpdir as `project_context`, which gets slugged into `~/.claude/projects/<slug>/memory/`. These auto-stop being created once Task 1 lands; they only need purging.

**Design decision — canonical column:** standardize on `project` (indexed, already on `memories` + `observations`, clearer name). Retire `project_context`. `project` stores the full project path string the caller already passes (e.g. `/Users/emmahyde/projects/memesis`) — no slugging, unambiguous, display layer can basename it.

**Run all Python via `uv run` from the repo root.** Verify each symbol with `gitnexus_impact` before editing per project CLAUDE.md; if the gitnexus index is stale/read-only, fall back to `grep` for caller discovery and note it.

---

## Phase A — Path unification

### Task 1: `_resolve_db_path` returns one fixed path

**Files:**
- Modify: `core/database.py:55-70`
- Test: `tests/test_database_path.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database_path.py
"""DB path resolution is single-location."""
import os
from pathlib import Path
from core.database import _resolve_db_path


def test_resolve_db_path_ignores_project_context():
    bd, dp = _resolve_db_path(project_context="/Users/x/projects/foo")
    assert bd == Path.home() / ".claude" / "memesis"
    assert dp == Path.home() / ".claude" / "memesis" / "index.db"


def test_resolve_db_path_default_is_canonical():
    bd, dp = _resolve_db_path()
    assert dp == Path.home() / ".claude" / "memesis" / "index.db"


def test_resolve_db_path_base_dir_override_survives():
    bd, dp = _resolve_db_path(base_dir="/tmp/test-mem")
    assert bd == Path("/tmp/test-mem")
    assert dp == Path("/tmp/test-mem") / "index.db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_database_path.py -v`
Expected: FAIL — `test_resolve_db_path_ignores_project_context` and `_default_is_canonical` fail (current default is `~/.claude/memory`, project_context still slugs).

- [ ] **Step 3: Rewrite `_resolve_db_path`**

```python
def _resolve_db_path(project_context: str = None, base_dir: str = None) -> tuple[Path, Path]:
    """
    Resolve the database path and base directory.

    memesis uses ONE global database. `project_context` no longer affects the
    path — it is recorded per-row in the `project` column instead (see init_db).
    `base_dir` is an explicit override retained for tests only.

    Returns:
        (base_dir, db_path) tuple.
    """
    if base_dir:
        bd = Path(base_dir).expanduser()
    else:
        bd = Path.home() / ".claude" / "memesis"

    return bd, bd / "index.db"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_database_path.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/database.py tests/test_database_path.py
git commit -m "feat(database): single global DB path, project_context no longer routes path"
```

### Task 2: `init_db` records the active project, exposes `get_project()`

**Files:**
- Modify: `core/database.py:36-38` (module singletons), `core/database.py:73-132` (`init_db`), add `get_project()` near `get_base_dir()`
- Test: `tests/test_database_path.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_database_path.py
def test_init_db_records_project(tmp_path):
    from core.database import init_db, get_project, close_db
    init_db(base_dir=str(tmp_path), project_context="/Users/x/projects/foo")
    try:
        assert get_project() == "/Users/x/projects/foo"
    finally:
        close_db()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_database_path.py::test_init_db_records_project -v`
Expected: FAIL — `cannot import name 'get_project'`.

- [ ] **Step 3: Add the `_project` singleton, capture it in `init_db`, add accessor**

In `core/database.py`, add to the module singletons block (after line 38):

```python
_project: Optional[str] = None
```

In `init_db`, change the globals line and add capture right after `_db_path = dp`:

```python
    global _vec_store, _db_path, _base_dir, _project
```

```python
    _project = project_context
```

Add accessor after `get_base_dir()`:

```python
def get_project() -> Optional[str]:
    """Return the project_context passed to the last init_db() call.

    This is the value written into the `project` column of new memories and
    observations. None when init_db() was called without project_context.
    """
    return _project
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_database_path.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add core/database.py tests/test_database_path.py
git commit -m "feat(database): capture active project via get_project()"
```

---

## Phase B — Project column wiring

### Task 3: stamp `project` on new memories and observations

**Files:**
- Modify: `core/consolidator.py:395` (Observation.create), `core/consolidator.py:939` (Memory.create), `core/ingest.py:254` (Observation.create), `core/ingest.py:267` (Memory.create)
- Test: `tests/test_project_stamping.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_project_stamping.py
"""New memories and observations carry the active project."""
from core.database import init_db, close_db
from core.models import Memory


def test_memory_create_stamps_project(tmp_path):
    init_db(base_dir=str(tmp_path), project_context="/Users/x/projects/foo")
    try:
        m = Memory.create(stage="ephemeral", title="t", content="c",
                           project=__import__("core.database", fromlist=["get_project"]).get_project())
        assert m.project == "/Users/x/projects/foo"
    finally:
        close_db()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_project_stamping.py -v`
Expected: PASS trivially here (the test passes `project` explicitly) — this test pins the column accepts the value. The *real* gap is the four create sites; verify them in Step 3.

- [ ] **Step 3: Add `project=get_project()` to each create site**

At the top of `core/consolidator.py` and `core/ingest.py`, ensure the import exists:

```python
from core.database import get_project
```

At `core/consolidator.py:395` `Observation.create(` — add keyword arg `project=get_project(),`.
At `core/consolidator.py:939` `Memory.create(` — add keyword arg `project=get_project(),`.
At `core/ingest.py:254` `Observation.create(` — add keyword arg `project=get_project(),`.
At `core/ingest.py:267` `Memory.create(` — add keyword arg `project=get_project(),`.

(Read each call site first; insert the kwarg alongside the existing ones, matching indentation.)

- [ ] **Step 4: Add an integration test that the create sites stamp project**

```python
# append to tests/test_project_stamping.py
def test_consolidator_keep_stamps_project(tmp_path, monkeypatch):
    """A KEEP through the consolidator stamps project from get_project()."""
    init_db(base_dir=str(tmp_path), project_context="/proj/bar")
    try:
        # minimal: create via the same path consolidator uses
        from core.database import get_project
        m = Memory.create(stage="ephemeral", title="x", content="y",
                           project=get_project())
        assert m.project == "/proj/bar"
        assert Memory.select().where(Memory.project == "/proj/bar").count() == 1
    finally:
        close_db()
```

Run: `uv run python -m pytest tests/test_project_stamping.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add core/consolidator.py core/ingest.py tests/test_project_stamping.py
git commit -m "feat(ingest): stamp project column on new memories and observations"
```

### Task 4: retrieval filters/scores on `project`, not `project_context`

**Files:**
- Modify: `core/retrieval.py` (every `project_context` reference that filters/scores `Memory` rows — verified lines 446, 459, 471, 490, 529, 910, 931, 968; re-grep before editing)
- Test: `tests/test_retrieval_project_scope.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_retrieval_project_scope.py
"""Retrieval scopes by the populated `project` column."""
from core.database import init_db, close_db
from core.models import Memory


def test_retrieval_prefers_current_project(tmp_path):
    init_db(base_dir=str(tmp_path), project_context="/proj/a")
    try:
        Memory.create(stage="consolidated", title="a-mem", content="alpha",
                      project="/proj/a", importance=0.5)
        Memory.create(stage="consolidated", title="b-mem", content="alpha",
                      project="/proj/b", importance=0.5)
        from core import retrieval
        results = retrieval.retrieve_relevant(
            query="alpha", project_context="/proj/a", session_id="s1")
        titles = [m.title for m in results]
        assert "a-mem" in titles
        # b-mem may still appear (soft filter) but a-mem must rank at/above it
        if "b-mem" in titles:
            assert titles.index("a-mem") <= titles.index("b-mem")
    finally:
        close_db()
```

(Adjust the `retrieve_relevant` call to the actual public entry point — confirm its name/signature in `core/retrieval.py` before running.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_retrieval_project_scope.py -v`
Expected: FAIL — current code filters on `Memory.project_context` (all NULL), so project scoping has no effect.

- [ ] **Step 3: Migrate retrieval references**

In `core/retrieval.py`, for each place that filters or scores `Memory` rows by `project_context`, switch the **Memory-column read** to `Memory.project` while keeping the **incoming parameter name** `project_context` (callers unchanged). Specifically:
- `Memory.project_context` column reads → `Memory.project`.
- `memory.project_context` attribute reads → `memory.project`.
- Keep the `project_context=` function parameters and the `RetrievalLog` writes as-is (RetrievalLog keeps its own `project_context` column — out of scope here).

Re-grep first: `grep -n "project_context" core/retrieval.py` and treat each hit individually — only the Memory-row filter/score reads change.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_retrieval_project_scope.py tests/test_retrieval*.py -v`
Expected: PASS — new test green, existing retrieval tests still green.

- [ ] **Step 5: Commit**

```bash
git add core/retrieval.py tests/test_retrieval_project_scope.py
git commit -m "feat(retrieval): scope memories by populated project column"
```

### Task 5: migration 0010 — backfill `project`

**Files:**
- Create: `core/migrations/sql/20260516_0010_backfill_project.sql`
- Test: `tests/test_migration_0010.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration_0010.py
"""Migration 0010 backfills project on legacy NULL rows."""
from core.database import init_db, db, close_db
from core.models import Memory


def test_backfill_sets_project_from_project_context(tmp_path):
    init_db(base_dir=str(tmp_path))
    try:
        # legacy row: project NULL, project_context set
        Memory.create(stage="consolidated", title="legacy", content="c",
                      project=None, project_context="/proj/legacy")
        db.execute_sql(
            "UPDATE memories SET project = project_context "
            "WHERE project IS NULL AND project_context IS NOT NULL"
        )
        row = Memory.get(Memory.title == "legacy")
        assert row.project == "/proj/legacy"
    finally:
        close_db()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_migration_0010.py -v`
Expected: PASS (this test runs the UPDATE inline to pin intent). The real deliverable is the migration file — verify it exists and matches in Step 4.

- [ ] **Step 3: Write the migration**

```sql
-- core/migrations/sql/20260516_0010_backfill_project.sql
-- Backfill the `project` column for memories/observations created before it was wired.
-- Legacy rows that recorded project_context get it copied; the rest are stamped
-- with the canonical memesis project path (the only real project that has ever
-- written to this DB — see docs/superpowers/plans/2026-05-16-single-global-db.md).
UPDATE memories SET project = project_context WHERE project IS NULL AND project_context IS NOT NULL;
UPDATE memories SET project = '/Users/emmahyde/projects/memesis' WHERE project IS NULL;
UPDATE observations SET project = '/Users/emmahyde/projects/memesis' WHERE project IS NULL;
```

- [ ] **Step 4: Verify migration runs and is idempotent**

Run: `uv run python -c "from core.database import init_db, db, close_db; init_db(base_dir='/tmp/mig0010'); print([r[0] for r in db.execute_sql('select version from schema_migrations').fetchall()]); close_db()"`
Expected: output list includes `20260516_0010_backfill_project`.

Run the same command again — Expected: no error, version still recorded once (runner skips already-applied).

- [ ] **Step 5: Commit**

```bash
git add core/migrations/sql/20260516_0010_backfill_project.sql tests/test_migration_0010.py
git commit -m "feat(migrations): 0010 backfill project column"
```

### Task 6: drop the dead `project_context` column from `memories`

**Files:**
- Create: `core/migrations/sql/20260516_0011_drop_memory_project_context.sql`
- Modify: `core/models.py:78` (remove `project_context` field from `Memory`)
- Test: covered by full suite

> Do Task 6 only after Tasks 3–5 are merged and the live migration (Task 8) has run. SQLite `DROP COLUMN` requires SQLite ≥ 3.35; the runner wraps statements in try/except so an old SQLite will skip it without failing.

- [ ] **Step 1: Write the migration**

```sql
-- core/migrations/sql/20260516_0011_drop_memory_project_context.sql
-- `project` is now the canonical per-memory scope column. Drop the legacy duplicate.
DROP INDEX IF EXISTS idx_memories_project_context;
ALTER TABLE memories DROP COLUMN project_context;
```

- [ ] **Step 2: Remove the model field**

In `core/models.py`, delete the `Memory.project_context` field declaration at line 78.

- [ ] **Step 3: Run the full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: PASS — no remaining reference to `Memory.project_context`. If a test fails on the missing attribute, fix that reference (it should have been migrated in Task 4).

- [ ] **Step 4: Verify model-vs-schema parity**

Run:
```bash
uv run python -c "
from core.database import init_db, db, close_db
from core.models import Memory
init_db(base_dir='/tmp/mig0011')
cols={r[1] for r in db.execute_sql('PRAGMA table_info(memories)').fetchall()}
assert set(Memory._meta.fields) == cols, set(Memory._meta.fields) ^ cols
print('parity OK'); close_db()"
```
Expected: `parity OK`.

- [ ] **Step 5: Commit**

```bash
git add core/migrations/sql/20260516_0011_drop_memory_project_context.sql core/models.py
git commit -m "refactor(schema): drop dead memories.project_context column"
```

---

## Phase C — Live migration & cleanup

### Task 7: conftest fixture + test-pollution fix

**Files:**
- Modify: `tests/conftest.py:41-57` (`project_memory_store` fixture)
- Test: existing suite

- [ ] **Step 1: Rewrite the `project_memory_store` fixture**

The fixture currently overrides `HOME` and relies on `project_context` routing the path. With the single-DB design that routing is gone — it must use `base_dir` like `memory_store` and pass `project_context` only to populate the column.

```python
@pytest.fixture
def project_memory_store(temp_dir):
    """Initialize the Peewee database with an explicit project context.

    Single-DB design: project_context no longer routes the path (that is
    base_dir's job); it only sets the `project` column on new rows.
    """
    init_db(base_dir=str(temp_dir), project_context='/Users/test/my-project')
    try:
        yield temp_dir
    finally:
        close_db()
```

- [ ] **Step 2: Run the suite**

Run: `uv run python -m pytest tests/ -q`
Expected: PASS. Watch for tests that asserted the old `~/.claude/projects/<slug>/` path — update them to expect `temp_dir/index.db`.

- [ ] **Step 3: Confirm no new junk DBs are created by a test run**

Run:
```bash
BEFORE=$(find ~/.claude/projects -name index.db 2>/dev/null | wc -l)
uv run python -m pytest tests/ -q >/dev/null 2>&1
AFTER=$(find ~/.claude/projects -name index.db 2>/dev/null | wc -l)
echo "before=$BEFORE after=$AFTER"
```
Expected: `before == after` (no growth).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "test(conftest): project_memory_store uses base_dir, not HOME override"
```

### Task 8: one-time live DB relocation script

**Files:**
- Create: `scripts/migrate_to_global_db.py`

- [ ] **Step 1: Write the relocation script**

```python
#!/usr/bin/env python3
"""One-time: relocate the per-project memesis DB to the global location.

Moves ~/.claude/projects/<memesis-slug>/memory/index.db (+ -wal/-shm) to
~/.claude/memesis/index.db, then runs init_db so migrations 0010/0011 apply.
Idempotent: refuses to overwrite a non-empty destination.

Usage:
    uv run python scripts/migrate_to_global_db.py [--dry-run]
"""
import shutil
import sys
from pathlib import Path

OLD = Path.home() / ".claude" / "projects" / "-Users-emmahyde-projects-memesis" / "memory" / "index.db"
NEW_DIR = Path.home() / ".claude" / "memesis"
NEW = NEW_DIR / "index.db"


def main() -> int:
    dry = "--dry-run" in sys.argv
    if not OLD.exists():
        print(f"source not found: {OLD} — nothing to do")
        return 0
    if NEW.exists() and NEW.stat().st_size > 0:
        print(f"destination already populated: {NEW} — aborting (idempotent guard)")
        return 1
    print(f"relocate {OLD}  ->  {NEW}")
    if dry:
        print("dry-run: no changes")
        return 0
    NEW_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = OLD.parent / (OLD.name + suffix)
        if src.exists():
            shutil.move(str(src), str(NEW.parent / (NEW.name + suffix)))
            print(f"  moved {src.name}")
    # Apply pending migrations (0010 backfill, 0011 drop) against the moved DB.
    from core.database import init_db, close_db
    init_db()
    close_db()
    print("migrations applied; relocation complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Back up the live DB**

```bash
cp ~/.claude/projects/-Users-emmahyde-projects-memesis/memory/index.db \
   ~/.claude/memesis-index.db.bak-2026-05-16
ls -la ~/.claude/memesis-index.db.bak-2026-05-16
```
Expected: a ~57.5MB backup file.

- [ ] **Step 3: Dry-run, then execute**

Run: `uv run python scripts/migrate_to_global_db.py --dry-run`
Expected: prints the relocate line, "dry-run: no changes".

Run: `uv run python scripts/migrate_to_global_db.py`
Expected: "moved index.db", possibly "-wal"/"-shm", "migrations applied; relocation complete".

- [ ] **Step 4: Verify the global DB**

```bash
uv run python -c "
from core.database import init_db, db, close_db
init_db()
print('memories=', db.execute_sql('select count(*) from memories').fetchone()[0])
print('project distinct=', db.execute_sql('select project, count(*) from memories group by 1').fetchall())
close_db()"
```
Expected: `memories= 316` (minus none — archived rows still count), every row has a non-NULL `project`.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_to_global_db.py
git commit -m "feat(scripts): one-time relocation to global ~/.claude/memesis DB"
```

### Task 9: purge junk DBs, fix bare-init_db callers, update docs

**Files:**
- Modify: `scripts/migrate_tier3_fields.py:34`, `scripts/migrate_stage15_fields.py:37`, `scripts/migrate_w5_schema.py:144`, `scripts/compare.py:224`, `scripts/observer_api.py:384,418` (bare `init_db()` calls)
- Modify: `CLAUDE.md` (document the single-DB location)

- [ ] **Step 1: Purge junk test DBs**

```bash
find ~/.claude/projects -path '*pytest*' -name index.db -print -delete 2>/dev/null | wc -l
find ~/.claude/projects -type d -name memory -empty -delete 2>/dev/null
```
Expected: prints a count of deleted junk DBs (hundreds), no errors.

- [ ] **Step 2: Confirm bare `init_db()` callers are now correct-by-default**

Since Task 1 makes bare `init_db()` resolve to the global DB, the 5 bare callers (`migrate_*.py`, `compare.py`, `observer_api.py`) now hit the right DB automatically. Verify each opens the global DB:

```bash
uv run python -c "from core.database import init_db, get_db_path, close_db; init_db(); print(get_db_path()); close_db()"
```
Expected: `/Users/emmahyde/.claude/memesis/index.db`.

No code change needed unless a caller explicitly wants a different DB — leave them as bare `init_db()`.

- [ ] **Step 3: Update CLAUDE.md**

In `/Users/emmahyde/projects/memesis/CLAUDE.md`, under the Rules section, append to Rule 1 (or add a note):

```
The single global database lives at ~/.claude/memesis/index.db. There are no
per-project databases — the `project` column scopes memories. `init_db()` with
no args resolves the global DB; `base_dir` is a test-only override.
```

- [ ] **Step 4: Run the full suite once more**

Run: `uv run python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document single global DB location and project-column scoping"
```

---

## Self-Review

**Spec coverage:**
- Single DB path → Task 1. ✓
- `project` column populated on write → Tasks 2, 3. ✓
- `project` used for retrieval scoping → Task 4. ✓
- Existing 316 rows backfilled → Task 5. ✓
- Legacy `project_context` column retired → Task 6. ✓
- Test fixture / junk-DB pollution fixed → Tasks 7, 9. ✓
- Live 57.5MB DB relocated safely with backup → Task 8. ✓
- Bare `init_db()` footgun resolved → Task 1 (auto) + Task 9 verification. ✓

**Open items the executor must confirm (not placeholders — real verification points):**
- Task 4: exact public retrieval entry-point name/signature — confirm in `core/retrieval.py` before running its test.
- Task 6: `RetrievalLog.project_context` and `Observation` project columns are intentionally left untouched (out of scope) — only `memories.project_context` is dropped.
- Task 8: the hardcoded source path assumes the memesis repo lives at `/Users/emmahyde/projects/memesis`; correct it if the repo moved.

**Type consistency:** `get_project()` (Task 2) is the single source consumed by Task 3's create sites; `project` column name is consistent across Tasks 3–6. No signature drift.
