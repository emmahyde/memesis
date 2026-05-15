# Design Decisions & Panel Consensus

Key decisions that shape the codebase. When auditing or proposing changes, check here first — many constraints are load-bearing.

---

## Database & Persistence

**CLAUDE.md Rule 1:** All persistence through `MemoryStore` or `database.py`. Never write Memory rows directly via raw SQL. Atomic writes use `tempfile.mkstemp` + `shutil.move`.

**CLAUDE.md Rule 1 (critical):** Do NOT open separate `sqlite3.connect()` connections to `index.db`. The Peewee `db` singleton manages WAL mode and `busy_timeout=5000`. Bypassing it creates concurrent-writer races.

**Implication for audits:** If you see raw `sqlite3.connect()` in any module outside `core/database.py`, that is a bug.

---

## LLM Calls

**CLAUDE.md Rule 2:** All LLM calls through `core.llm.call_llm()`. Do not instantiate `anthropic.Anthropic()` in service modules.

**Why:** Centralizes retry logic, rate-limiting, model configuration, and token accounting.

---

## Observability / Shadow Mode

**`core/observability.py`, `SHADOW_ONLY=True`**
Pruning computes what would be deleted and logs to `backfill-output/observability/shadow-prune.jsonl` but executes NO database mutations.

This is a deliberate 30-day dry-run (Decision C3). It will be flipped to `False` after analyzing false-prune rates.

**Implication:** Any test that asserts a pruned memory is hard-deleted will fail or be testing the wrong thing. Tests for shadow mode should verify the JSONL log, not the DB.

---

## FTS5 SQL Splitter

**Memory (from project MEMORY.md):** `_apply_sql` in migrations splits on `;` ignoring strings and comments. Never use `;` inside `--` comments in migration SQL files.

---

## Activation Formula (`core/observability.py`)

```
activation = importance × exp(-age_hrs / τ) × (1 + log(1 + access_count))
```

- τ is the **time constant** (not half-life). Half-life = τ × ln(2).
- Access reinforcement is sub-linear (Matthew Effect prevention).
- NOT ACT-R base-level activation — do not cite ACT-R for this formula (panel C1 correction).
- Formula is NOT bounded to [0,1] — normalization is the caller's responsibility.

---

## Cosine Linking Thresholds (`core/linking.py`)

- `LINK_COSINE_THRESHOLD=0.72` (env: `MEMESIS_LINK_THRESHOLD`) — neighbors above this get linked
- `LINK_AUTO_PROMOTE_THRESHOLD=0.85` (env: `MEMESIS_AUTO_PROMOTE_THRESHOLD`) — above this, new memory is treated as duplicate and the existing one gets `reinforcement_count++`; new memory is archived

Calibrated for `bge-small-en-v1.5` at 384 dimensions. Prior value (0.90) was for `Titan` at 1024d — do not revert.

---

## Ordinal Indexing Mismatch (`core/models.py:Observation`)

`Observation.ordinal` is 0-indexed. The consolidator runtime and LLM response use 1-indexed ordinals (`OBSERVATION_ID` in prompts). When joining Observation rows to LLM `obs_ids`, **add 1** to `Observation.ordinal`.

---

## Hypothesis Promotion Gate (`core/self_reflection.py`)

Hypotheses (`kind='hypothesis'`) require ALL of:
- `evidence_count >= 3`
- `len(set(json.loads(evidence_session_ids))) >= 2`
- No `contradicts` edge in `MemoryEdge` involving this memory (bidirectional)

Explicit user-statement memories (`kind != 'hypothesis'`) are exempt.

---

## Stage Compression (`core/compression.py`)

Memories are progressively compressed as they advance through stages. The `compression_ratio` in `ConsolidationLog` tracks output/input token ratio per LLM call. Values > 1 mean expansion (rare), < 1 mean compression. NULL = legacy row or non-LLM action.

---

## Retrieval Tier Architecture

Decision: three-tier, not flat ranking.

- **Tier 1 (instinctive):** Zero overhead — always injected. Kept small (< 3 memories) to avoid bloat.
- **Tier 2 (crystallized):** Token-budgeted, context-matched. The main working set.
- **Tier 3 (active search):** Agent-initiated only. FTS5 + vector, progressive disclosure.

**Rationale (panel):** Flat ranking causes O(N) scoring overhead on every prompt. Tiers degrade gracefully as memory grows.

---

## Cognitive Modules (`core/retrieval.py`)

RISK-11 module registry. Non-experimental modules: `affect`, `coherence`, `habituation`, `orienting`, `replay`, `somatic`. Experimental (opt-in via `MEMESIS_EXPERIMENTAL_MODULES`): `self_reflection`.

Adding a new module requires:
1. Adding it to `_COGNITIVE_MODULES` list
2. Marking `experimental=True` if not production-ready
3. Writing a test in `tests/test_cognitive_modules.py`

---

## Testing Constraints

**CLAUDE.md Rule 3:** Tests never touch `~/.claude/memory`. Always use `conftest.py` tmp_path fixtures.

**Test runner:** `uv run pytest tests/` — never bare `python3 -m pytest` (misses locked deps).

**Eval harness:** `uv run pytest eval/` (requires `pip install -e ".[eval]"` first).
