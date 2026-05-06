# Runbook: `/memesis:evolve` — Collaborative Pipeline Reverse-Engineering Protocol

A protocol for you and Claude to sit down with a session transcript, watch it flow through the full memesis pipeline, identify the memories you wished had been captured, and reverse-engineer the pipeline until they are.

This is not a one-shot command. It is a **dialogue** in three phases — replay together → diff against your expectations → iterate (manually or with `--autoresearch`).

---

## The protocol in one paragraph

You bring a transcript. Claude replays it through the pipeline against an isolated DB, with full trace logging at every stage (extract, synthesize, consolidate, crystallize). You and Claude read the trace together — what was extracted, which cards were synthesized, what was kept, what was pruned. You describe the memories you would have expected to capture. The skill compiles those into pytest evals. The guard suite tells you which expectations passed and which failed. For each failure, the trace tells you which stage lost the signal. From there: either you propose a fix manually, or you hand the eval to `--autoresearch`, which iterates a Modify→Verify→Keep/Discard loop on the responsible stage until the eval passes (or it runs out of budget).

The point is not automation. The point is **closing the loop between "what I wanted captured" and "what the pipeline actually captures"**, in a way that's debuggable end-to-end.

---

## Phase 1: Sit down with the transcript together

### Step 1.1 — Pick a transcript

You want a session where you have a clear intuition about what *should* have been captured. Recent ones are easier — the memory is fresh.

```bash
ls ~/.claude/projects/-Users-$(whoami)-projects-memesis/*.jsonl | tail -5
```

Pick one. Skim it briefly so you can describe expected memories later.

### Step 1.2 — Run the replay

```
/memesis:evolve <transcript_path>
```

Or invoke the driver directly (skips skill harness — useful when debugging the skill itself):

```bash
python3 scripts/evolve.py --transcript <path> [--live] [--autoresearch]
```

What happens behind the scenes:

1. **Isolated replay DB** — `tempfile.mkdtemp` + `init_db()`. Production database is never touched.
2. **LLM transparently cached** — first replay hits live LLM, subsequent replays of the same transcript hit `~/.claude/memesis/evolve/cache/`. Cache key is `sha256(model + prompt)`, so prompt mutations auto-invalidate.
3. **Trace writer initialized** — every stage boundary, every card synthesized, every keep/prune/promote decision, every validator outcome, every LLM envelope (prompt hash + response + token usage) gets a JSONL line in `~/.claude/memesis/traces/replay-<orig>-<n>.jsonl`.
4. **Pipeline runs in-process** — `core.transcript_ingest.extract_observations` → `append_to_ephemeral` → consolidator → crystallizer. Direct Python calls, fully debuggable.

When this finishes you have a complete trace of what the pipeline did with that transcript.

### Step 1.3 — Read the trace together

This is the **debug logging** you wanted — not stdout `print` calls, but a structured trace you can grep and replay.

```bash
# What sessions are available?
python3 scripts/trace_query.py --list-sessions

# Read everything from this replay
python3 scripts/trace_query.py --session replay-<orig>-<n>

# Just the observations extracted in stage 1
python3 scripts/trace_query.py --session replay-<orig>-<n> --stage stage1_extract_end

# Just the cards synthesized
python3 scripts/trace_query.py --session replay-<orig>-<n> --event card_synth

# Just the keep/prune/promote decisions
python3 scripts/trace_query.py --session replay-<orig>-<n> --event keep
python3 scripts/trace_query.py --session replay-<orig>-<n> --event prune
python3 scripts/trace_query.py --session replay-<orig>-<n> --event promote

# Crystallization gates
python3 scripts/trace_query.py --session replay-<orig>-<n> --stage crystallizer_*

# Pipe to jq for analysis
python3 scripts/trace_query.py --session replay-<orig>-<n> --json \
  | jq '.[] | select(.event == "validator_outcome" and .payload.passed == false)'
```

Sit with Claude here. Ask things like: "Why was this observation discarded? Which card consumed it? What was the importance score? Did the Kensinger friction bump fire?" — every one of those is a query against the trace.

---

## Phase 2: Tell Claude what you expected to see

The skill prompts you on stdin during the replay. One line per expected memory, blank line / EOF to finish.

```
Expected memory descriptions (one per line, blank to finish):
> the user's frustration with the oauth refresh token returning 401 on the second retry
> the decision to switch from exponential backoff to a fixed 30s retry window
> the user remembering that we already tried this approach last week and it failed
> 
```

### Phrasing cues drive match mode (D-10)

Your phrasing tells the compiler how strict to be:

| You phrase it as... | Match mode | Asserts |
|---|---|---|
| Named entities ("oauth refresh token", "30s retry window") | `entity_presence` | those tokens appear in some retained memory |
| Conceptual / paraphrased ("the user's frustration with...") | `semantic_similarity` | embedding cosine ≥ 0.50 |
| Emotional valence ("frustration", "delight", "friction") | `polarity_match` | `affect_valence` field matches |
| Negative ("the user remembering that we already tried...") | `absence` ← **NB:** "should NOT", "forgotten", "pruned" trigger this; check the compiled file if you didn't intend it |

If the compiled match mode is wrong, edit the spec or rephrase and re-run.

### What the compiler produces

Each description compiles to a deterministic pytest file at:

```
eval/recall/<slug>_<spec_slug>_recall.py
```

These are versioned with the codebase. Future runs of `pytest eval/recall/` re-run them — so the eval *becomes* a permanent regression test for that pipeline behavior.

Re-run a single compiled eval later, outside the skill:

```bash
python3 -m pytest eval/recall/<slug>_recall.py -v
```

---

## Phase 3: Reverse-engineer the gaps

After Phase 2 the skill runs the guard suite (`pytest tests/ eval/recall/<slug>_recall.py -x`) and emits a structured diagnostic delta:

```
=== DIAGNOSTIC DELTA ===

PASS  oauth-401-retry              (entity_presence)
FAIL  exponential-to-fixed-30s     (semantic_similarity)
      Lost at: stage15_synthesis_end
      Match mode failed: cosine 0.31 < 0.50 threshold

FAIL  already-tried-last-week      (entity_presence)
      Lost at: stage1_extract_end
      Match mode failed: entity 'last week' not found in any memory
```

For each FAIL, the **lost-at stage** tells you which step in the pipeline dropped the signal. That maps directly to the file you'd want to edit:

| Lost stage | Where signal dropped | File to edit |
|---|---|---|
| `stage1_extract_end` | extraction prompt missed the observation entirely | `core/prompts.py` — `OBSERVATION_EXTRACT_PROMPT`, `SESSION_TYPE_GUIDANCE` |
| `stage15_synthesis_end` | observation extracted but no card formed for it | `core/issue_cards.py` — `ISSUE_SYNTHESIS_PROMPT` |
| `consolidator_keep` | card formed but not kept (importance/threshold) | `core/consolidator.py:_execute_keep()`, `core/rule_registry.py` thresholds |
| `crystallizer_*` | kept ephemeral but never crystallized | `core/crystallizer.py` gates |

These are exactly the **D-14 mutation surface** files — the ones autoresearch is allowed to touch.

### Path A: manual reverse-engineering (you drive)

You read the trace at the lost-at stage. You form a hypothesis: "the extraction prompt doesn't have a rule about temporal references like 'last week', so it filters them out." You edit `core/prompts.py`, save, and re-run:

```
/memesis:evolve <same transcript>
```

The cache invalidates automatically (the prompt content changed, so its sha256 changed, so the cache key is new). You get a fresh diagnostic. Iterate until your eval passes. The compiled `eval/recall/*.py` is now a regression test that future commits cannot silently break.

### Path B: hand it to `--autoresearch` (Claude drives the loop)

This is the autoresearch-baked-into-evolve part. You have an eval that's failing and you don't want to hand-tune the prompt yourself. Run:

```
/memesis:evolve <transcript> --autoresearch
```

The loop:

```
┌──────────────────────────────────────────────┐
│ Read diagnostic delta. Pick a failing eval.  │
└────────────────────┬─────────────────────────┘
                     ▼
┌──────────────────────────────────────────────┐  ← halt if iteration ≥ max_iterations (10)
│ Loop:                                        │  ← halt if token_spend ≥ token_budget (mid-iter, no grace)
│                                              │
│   1. Pick mutation target (D-14 surface)     │  e.g. core/prompts.py
│   2. _propose_mutation(target) → new_content │  LLM proposes an edit
│   3. Apply mutation (edit file in-place)     │
│   4. Run guard suite (D-15):                 │
│        - python3 -m pytest tests/            │  full unit suite
│        - tier-3 invariants explicitly        │  TestCardImportance,
│        - eval/recall/ regression             │  TestRule3KensingerRemoved,
│        - manifest JSON round-trip            │  etc.
│   5. All pass? → keep (atomic write,         │
│                  update YAML counters)       │  tempfile.mkstemp + shutil.move
│      Any fail? → discard                     │  git checkout -- <file>
└──────────────────────────────────────────────┘
```

The eval you compiled in Phase 2 is the **convergence signal**. The full guard suite is the **keep gate** — autoresearch cannot fix your session by breaking another session, by relaxing a Tier-3 invariant, or by introducing a JSON round-trip regression.

#### Mutation surface (D-14) — autoresearch may ONLY edit these:

| File | What may change |
|------|-----------------|
| `core/prompts.py` | `OBSERVATION_EXTRACT_PROMPT`, `SESSION_TYPE_GUIDANCE` |
| `core/issue_cards.py` | `ISSUE_SYNTHESIS_PROMPT` (fragile per CONCERNS — full guard suite must hold) |
| `core/rule_registry.py` | `ParameterOverrides` thresholds (Kensinger bump, importance gates, numeric knobs) |
| `core/consolidator.py` | `_execute_keep()` logic |
| `core/crystallizer.py` | crystallization gates |

Out-of-surface targets raise `ValueError` immediately. No silent scope creep.

#### Tuning the budget

After the first run a config lands at:

```
~/.claude/memesis/evolve/<session>/autoresearch.yaml
```

```yaml
max_iterations: 10
token_budget: 100000
mutation_surface: [...]   # default from D-14
guard_suite: [...]        # default from D-15
```

Raise `token_budget` if the loop halts mid-iteration with the eval still failing. Narrow `mutation_surface` to one file if you want to scope the search ("only mutate the extraction prompt, not the consolidator").

#### Reviewing what changed

```bash
git diff core/                    # what autoresearch kept
git log --oneline core/ -5        # if you committed between iterations
git checkout -- core/<file>       # revert one mutation by hand
```

Important: **commit or stash any unrelated WIP in the D-14 surface before running `--autoresearch`**. Discard uses `git checkout -- <file>` and will eat your in-flight edits.

---

## The collaborative cadence

This skill works best as a sit-down session, not a fire-and-forget. A typical cadence:

1. You: "Here's a transcript from yesterday. I expected us to remember the bit about the schema migration plan but I don't think we did."
2. Claude (runs `/memesis:evolve <transcript>`): "Here's the trace. The schema-migration discussion is in observation #4, but I don't see a card synthesized for it. Want to look at the synthesis stage?"
3. You (with Claude reading the trace): "The synthesis prompt has DECISION-KIND RULES D1-D3 — rule D2 says only synthesize cards for explicit decisions. The migration discussion was exploratory, no decision. That's why."
4. You (typing into the stdin prompt): "Expected memory: the schema migration plan with the three-phase rollback strategy."
5. Skill compiles → guard suite → diagnostic FAIL at `stage15_synthesis_end`.
6. You: "Either we expand D2 to capture exploratory plans, or we add a new D4 rule for plans-with-rollback. What do you think?"
7. Either: hand-edit `core/issue_cards.py` and re-run. Or: `/memesis:evolve <transcript> --autoresearch` and let the loop find a phrasing that satisfies the eval without breaking the rest.

Either way you end up with a permanent eval at `eval/recall/<slug>_recall.py` that will catch this regression forever.

---

## Storage map

| What | Path | Lifetime |
|---|---|---|
| Trace JSONL | `~/.claude/memesis/traces/<session_id>.jsonl` | Last 50 sessions, FIFO |
| LLM cache | `~/.claude/memesis/evolve/cache/<sha256>.json` | Until 500 MB, then oldest-first eviction |
| Replay counter | `~/.claude/memesis/evolve/<orig>/replay_count.json` | Persistent |
| Autoresearch config | `~/.claude/memesis/evolve/<session>/autoresearch.yaml` | Persistent, edit by hand |
| Compiled evals | `eval/recall/<slug>_<spec_slug>_recall.py` | Versioned with codebase |
| Replay DB | `tempfile.mkdtemp()` | Cleaned on exit + on exception |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Diagnostic shows `Lost at: unknown` | trace event not flushed for that memory | check `~/.claude/memesis/traces/<session>.jsonl` exists and has lines for the relevant stage |
| Cache returns stale response after I edited a prompt | cache key includes prompt hash, so this should NOT happen — confirm the file actually changed | `git diff core/prompts.py`; if needed, `--live` to force fresh |
| `--autoresearch` halts at iteration 0 | `token_spend >= token_budget` already at start | raise `token_budget` in `<session>/autoresearch.yaml` |
| `git checkout -- <file>` ate my WIP | autoresearch reverted in-flight edits in D-14 surface | always commit/stash before `--autoresearch` |
| Hooks run stale code despite editing `core/` | plugin cache is a snapshot, not a symlink | reinstall plugin so `~/.claude/plugins/cache/memesis-local/memesis/0.2.0/` updates |
| `ValueError: refused :memory:` | replay tried in-memory SQLite | by design (D-06) — WAL/extension divergence; tempfile only |
| Compiled eval picked wrong match mode | phrasing triggered `absence` (negation cues) when you meant presence | rephrase, or hand-edit the compiled `eval/recall/*.py` |

---

## Safety properties (why this is debuggable, not destructive)

- **Production DB never touched** — replay writes go to a `tempfile.mkdtemp()` cleaned on exit and on exception.
- **LLM cache invalidates automatically** — key is `sha256(model + prompt)`, so prompt edits force a fresh call without manual cache clearing.
- **Mutation surface gated** — autoresearch raises `ValueError` on any out-of-surface target.
- **Guard suite gates `keep`** — any single guard failure → `git checkout -- <file>` revert.
- **Atomic writes everywhere** — `tempfile.mkstemp` + `shutil.move` for trace JSONL, LLM cache entries, autoresearch config, and kept mutations.

---

## What's actually new about this protocol

The whole point is that you can no longer hand-wave about "the pipeline lost something." Every loss is now:

- **Localized** — the trace says exactly which stage dropped it
- **Reproducible** — replay against the same transcript yields identical pipeline behavior (cache + tempfile DB)
- **Testable** — your expectation is now a pytest file that runs forever
- **Closeable** — manually or via `--autoresearch`, you can iterate until the eval passes, gated by a guard suite that prevents regressions elsewhere

That's the loop. Everything else is plumbing.

---

## See also

- `.context/CONTEXT-evolve-skill.md` — design decisions D-01..D-16
- `.context/PLAN-evolve-skill.md` — implementation wave breakdown
- `skills/evolve/SKILL.md` — skill front matter + flag list
- `skills/autoresearch/SKILL.md` — autoresearch loop spec
- `core/trace.py` — event schema (ts/stage/event/payload)
- `core/eval_compile.py` — match mode compiler
- `core/autoresearch.py` — Autoresearcher class + guard suite
- `scripts/trace_query.py` — trace inspection CLI
