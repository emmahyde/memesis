---
name: evolve
description: Replay a session transcript through the full memesis pipeline against an isolated database, elicit expected-memory descriptions from the user, compile them to pytest evals, run the guard suite, and report a structured diagnostic delta. Use when you want to validate pipeline fidelity against a known transcript or to identify where the pipeline loses signal.
---

# Evolve — Replay & Eval

Replay a session transcript through the full memesis pipeline, compile expected-memory specs into pytest evals, and report which memories were retained vs. lost and at which stage.

## Usage

```
/memesis:evolve <transcript_path> [--live]
/memesis:evolve /path/to/transcript.jsonl
/memesis:evolve /path/to/transcript.jsonl --live
```

> `--autoresearch` is accepted as a flag but wires to the mutation loop in Task 4.2. If passed before that ships, a placeholder message is printed and the command exits 0.

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--transcript PATH` | yes | Path to the `.jsonl` transcript file to replay |
| `--live` | no | Force live LLM calls; bypass the replay LLM cache |
| `--autoresearch` | no | *(Stub — wires in Task 4.2)* Trigger the autoresearch mutation loop after eval compilation |

## What It Does

1. **Spins up an isolated replay DB** (`tempfile.mkdtemp` + `init_db()`) — no writes to the production database.
2. **Patches `core.llm.call_llm`** to route through the disk-backed LLM cache (`core.llm_cache.cached_call_llm`) for the duration of the replay. `--live` forces fresh calls. This means all pipeline modules (`transcript_ingest`, `issue_cards`, `consolidator`, etc.) transparently use the cache without modification.
3. **Initialises a `TraceWriter`** with `session_id = f"replay-{orig_session_id}-{n}"` where `n` is a monotonic counter persisted alongside the cache. Replay traces land in `~/.claude/memesis/traces/`.
4. **Invokes the ingest pipeline** (`core.transcript_ingest.extract_observations` + `append_to_ephemeral`) against the tempfile store.
5. **Prompts you (stdin)** for expected-memory descriptions — one per line, blank line or EOF to finish.
6. **Compiles each description** via `core.eval_compile.extract_spec_from_text()` → `compile_to_pytest()` → writes `eval/recall/<slug>_recall.py`.
7. **Runs the guard suite**: `python3 -m pytest tests/ eval/recall/<slug>_recall.py -x --tb=short`. Captures pass/fail per compiled eval.
8. **Emits a diagnostic delta**: for each failing eval, reports which pipeline stage lost the memory (traceable via the JSONL trace file) and which match mode failed.

## Replay LLM Cache

Cached responses live at `~/.claude/memesis/evolve/cache/<sha256>.json`.  
The cache key is `sha256(model + prompt)` — prompt mutations automatically invalidate the cache.  
`--live` bypasses the cache and writes fresh responses back.  
Cache is evicted (oldest-first) when it exceeds 500 MB.

## Replay Session ID

Replay counter is persisted at:
```
~/.claude/memesis/evolve/<orig_session_id>/replay_count.json
```
Each run increments `n` and writes back atomically. The replay session_id format is:
```
replay-<orig_session_id>-<n>
```

## Eval Output

Compiled evals land at `eval/recall/<slug>_recall.py` and are auto-collected by `eval/conftest.py` on subsequent `pytest eval/` runs.

## Implementation

The skill is driven by `scripts/evolve.py`:

```bash
python3 scripts/evolve.py --transcript /path/to/transcript.jsonl [--live]
```

## Diagnostic Delta Format

```
=== DIAGNOSTIC DELTA ===

PASS  oauth-token-expiry          (entity_presence)
FAIL  user-auth-flow              (semantic_similarity)
      Lost at: stage15_synthesis_end
      Match mode failed: cosine 0.31 < 0.50 threshold

FAIL  error-handling-pattern      (entity_presence)
      Lost at: stage1_extract_end
      Match mode failed: entity 'retry' not found in any memory
```

## Notes

- The tempfile DB is cleaned up on exit **and** on exception.
- Plugin cache staleness: if you changed `core/` code after install, rebuild the plugin cache — `evolve` runs from project source during development.
- The `--autoresearch` mutation loop (Task 4.2) reads the diagnostic delta and iterates a Modify→Verify→Keep/Discard loop over the mutation surface defined in D-14.
