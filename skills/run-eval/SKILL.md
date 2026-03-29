---
name: run-eval
description: Run the memesis eval suite. Use when the user says "run evals", "run eval", "check eval", "eval suite", or wants to measure retrieval/injection quality. Supports live (real observations) and synthetic modes.
---

# /memesis:run-eval — Run Eval Suite

Run the memesis evaluation harness against synthetic fixtures or real observation data from the reduce pipeline.

## Usage

```
/memesis:run-eval                  # Default: synthetic + live (if available)
/memesis:run-eval --live           # Live observations only
/memesis:run-eval --synthetic      # Synthetic fixtures only
/memesis:run-eval --baseline       # Re-capture baseline after changes
/memesis:run-eval --full-reduce    # Full pipeline: scan → reduce (10% sample) → eval
```

## Procedure

### 1. Check prerequisites

```bash
# Verify eval-observations.db exists for live mode
ls -la eval/eval-observations.db
```

If missing and live mode requested, tell the user to run:
```bash
python3 scripts/scan.py 60d --min-size 10
python3 scripts/reduce.py --db eval/eval-observations.db --reset --sample 10 --seed 42
```

### 2. Run the eval suite

**Default (both synthetic + live):**
```bash
python3 -m pytest eval/ -q --tb=short
```

**Synthetic only:**
```bash
python3 -m pytest eval/ -q --tb=short --ignore=eval/live_retrieval_test.py
```

**Live only:**
```bash
python3 -m pytest eval/live_retrieval_test.py -v --tb=short
```

### 3. Capture or compare baseline

**Re-capture baseline:**
```bash
python3 eval/capture_baseline.py --phase "<describe-what-changed>"
```

**Compare against baseline:**
```bash
python3 eval/verify_phase.py --phase "<phase-label>"
```

### 4. Full reduce pipeline (--full-reduce)

Run the complete scan → reduce → eval pipeline with a 10% deterministic sample:

```bash
# Step 1: Scan transcripts (no LLM, fast)
python3 scripts/scan.py 60d --min-size 10

# Step 2: Reduce with 10% sample (LLM calls, ~3 min)
python3 scripts/reduce.py --db eval/eval-observations.db --reset --sample 10 --seed 42

# Step 3: Run live evals
python3 -m pytest eval/live_retrieval_test.py -v --tb=short
```

### 5. Report results

After running, summarize:
- Tests passed/failed/skipped
- Any regressions vs baseline (if verify_phase was run)
- Memory count and stage distribution from live store
- Notable failures or new observations

## Key files

| File | Purpose |
|------|---------|
| `eval/conftest.py` | Synthetic fixtures (20 memories) + live observation loader |
| `eval/live_retrieval_test.py` | Tests against real observation data |
| `eval/capture_baseline.py` | Snapshot metrics to eval-baseline.json |
| `eval/verify_phase.py` | Compare current metrics against baseline |
| `eval/eval-observations.db` | Reduce output (gitignored, from pipeline) |
| `scripts/scan.py` | Transcript → summary (no LLM) |
| `scripts/reduce.py` | Summary → observations (LLM, ~$1 for 10% sample) |

## Examples

```
/memesis:run-eval                    # Quick check: all tests
/memesis:run-eval --live             # How does retrieval work on real data?
/memesis:run-eval --baseline         # Lock in current numbers after a change
/memesis:run-eval --full-reduce      # Fresh pipeline run from transcripts
```
