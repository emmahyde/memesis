# Memory Lifecycle Evaluation Suite

Five evals that measure the correctness and robustness of the memesis
system across retrieval, persistence, curation, spontaneous recall, and
staleness handling.

## How to Run

From the `memesis/` directory:

```
python3 -m pytest eval/
```

Or to run a single eval file:

```
python3 -m pytest eval/needle_test.py -v
python3 -m pytest eval/continuity_test.py -v
python3 -m pytest eval/curation_audit.py -v
python3 -m pytest eval/spontaneous_recall.py -v
python3 -m pytest eval/staleness_test.py -v
```

All evals run with mocked LLM calls by default. No `ANTHROPIC_API_KEY` or
network access is required.

### Running Against a Real LLM

Set `MEMORY_LIFECYCLE_REAL_LLM=1` to opt into real LLM calls (affects
curation_audit.py only, which currently patches `_call_llm`):

```
MEMORY_LIFECYCLE_REAL_LLM=1 python3 -m pytest eval/curation_audit.py -v
```

Note: when `MEMORY_LIFECYCLE_REAL_LLM` is set, `ANTHROPIC_API_KEY` must be
present in the environment.

## Target Scores

| Eval                        | Metric                            | Target |
| --------------------------- | --------------------------------- | ------ |
| Needle (Eval 1)             | needles_found / total_needles     | >= 85% |
| Continuity (Eval 2)         | cross-session memories present    | >= 80% |
| Curation (Eval 3)           | curation precision (keep quality) | >= 80% |
| Spontaneous Recall (Eval 4) | preference tokens in context      | >= 70% |
| Staleness (Eval 5)          | stale injection rate              | < 10%  |

## Eval 1: Needle-in-the-Memory (`needle_test.py`)

**What it measures:** Whether specific, unusual facts stored in the crystallized
stage are reliably retrieved and injected into the session context even when
surrounded by 20 background memories.

**Why it matters:** The system is useless if critical facts are silently dropped
during retrieval. This eval catches regressions in the token-budget packing
logic or FTS index.

**Method:** 3 "needle" memories with unique tokens are planted in crystallized.
`inject_for_session()` is called; the test checks all 3 tokens appear in the
returned string.

## Eval 2: Cross-Session Continuity (`continuity_test.py`)

**What it measures:** Whether memories promoted to crystallized in Session A
survive and appear in Session B's injected context when both sessions share the
same `base_dir`.

**Why it matters:** The primary value of the memory system is durable knowledge
across sessions. A failure here means the agent loses its working context every
time a new conversation starts.

**Method:** Session A creates two decision memories in consolidated, sets
`reinforcement_count=3`, and promotes them to crystallized. Session B opens a
fresh `MemoryStore` on the same `base_dir` and calls `inject_for_session()`;
the test asserts both decision texts appear.

## Eval 3: Curation Quality (`curation_audit.py`)

**What it measures:** Whether the consolidation pipeline correctly distinguishes
important decisions from trivial chit-chat, keeping the former and pruning the
latter.

**Why it matters:** Poor curation pollutes the memory store with noise, degrades
retrieval quality, and wastes token budget on irrelevant content.

**Method:** 10 observations (4 important + 6 trivial) are fed to
`Consolidator.consolidate_session()` with a mocked `_call_llm` that returns
deterministic KEEP/PRUNE decisions. The test audits the resulting memory tree:
4 consolidated memories created, 6 pruned, precision = 100%.

## Eval 4: Spontaneous Recall (`spontaneous_recall.py`)

**What it measures:** Whether preference memories in the instinctive stage are
automatically surfaced in the injected context without the agent being
explicitly told to look them up.

**Why it matters:** Tier 1 (instinctive) memories should be zero-overhead
behavioral guidelines — always present. If they are missing from context, the
agent cannot apply user preferences without explicit reminders.

**Method:** 5 preference memories are placed in instinctive. `inject_for_session()`
is called with no prompt about memory. All 5 preference tokens are checked for
presence in the injected context block.

## Eval 5: Memory Staleness (`staleness_test.py`)

**What it measures:** Whether `store.update()` correctly replaces stale content
so that outdated facts do not appear in injected context after a superseding
update.

**Why it matters:** Injecting contradictory or outdated facts (e.g., both
`/v1/users` and `/v2/users` as the current endpoint) is worse than injecting
nothing. The eval catches dual-write bugs or stale FTS index entries.

**Method:** 3 memories are created with "old" content, then updated with "new"
content. After `inject_for_session()`, only new tokens should appear; old tokens
must be absent.
