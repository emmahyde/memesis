# Observation Collector Prompt — Dedup Draft

Scope: `core/prompts.py` only. No caller impact. Two changes.

Issue 2 (OBSERVATION_TYPES "stale") **retracted** — it is live: `transcript_ingest.py:1178`
`_KIND_MAP` maps extraction `kind` → OBSERVATION_TYPES header vocab; `append_observation.py:68`
uses `OBSERVATION_TYPES.keys()` for `/learn`. RETIRED VOCABULARY applies to extraction `kind`
output, a separate axis. No conflict. Left untouched.

---

## Change 1 — collapse duplicate session-type guidance

Today two systems disagree:

- `SESSION_TYPE_GUIDANCE` dict (prompts.py:29-57) — keys `research/writing/code/agent_driven/unknown`,
  injected into the template at `{session_type_guidance}` (template line 441).
- Hardcoded body block "SESSION_TYPE GUIDANCE" (prompts.py:490-512) — keys `code/research/writing/general`.

`general` is not a real `session_type` (`format_extract_prompt` docstring: real values are
`code/research/writing/agent_driven/unknown`). The body block's `general` entry is unreachable;
`agent_driven` sessions get no body-block guidance at all.

**Fix:** the dict becomes the single source. Move the body block's richer per-type detail INTO
the dict values, then delete the body block. The `{session_type_guidance}` injection then carries
full detail; `agent_driven` and `unknown` are covered.

### 1a. Replace SESSION_TYPE_GUIDANCE dict (prompts.py:29-57)

```python
SESSION_TYPE_GUIDANCE = {
    # Per-session-type filter injected into _OBSERVATION_EXTRACT_PROMPT_TEMPLATE at
    # the {session_type_guidance} placeholder. Single source of truth — there is no
    # longer a parallel hardcoded block in the prompt body.
    # Use format_extract_prompt(); it keys this dict by session_type automatically.
    "research": (
        "Durable: conceptual outcomes (\"X library uses Y mechanism because Z\"), decisions to "
        "adopt/reject an approach, comparisons with explicit trade-offs, prior-art findings that "
        "change future direction. Skip: raw search results, tool calls, page summaries without a "
        "synthesis, \"looked at X\" with no takeaway. A research session can have many durable "
        "observations even with no code change — bias toward extracting conceptual findings over "
        "skipping. Force work_event=null."
    ),
    "writing": (
        "Durable: authoring decisions (structure, voice, scene order), aesthetic choices with "
        "rationale, rejected options with reason, style commitments, named characters/locations "
        "with established traits. Skip: aesthetic preferences without rationale, one-off word "
        "choices, summaries of what was written."
    ),
    "code": (
        "Durable: bugfixes with diagnosed root cause, refactor decisions with rationale, "
        "performance findings, API/contract corrections, build/test gotchas, configuration "
        "constraints, tooling wins (commands that save time, flags that simplify workflow), "
        "workflow discoveries (shortcuts, defaults, implicit behaviors). Skip: green test runs "
        "with no finding, file navigation, tool-call traces without a conclusion."
    ),
    "agent_driven": (
        "Target task structure, decisions, and surprising agent failures. Skip per-tool-call "
        "narration and routine progress updates."
    ),
    "unknown": (
        "Apply the QUALITY GATE directly with no session-type-specific bias."
    ),
}
```

### 1b. Delete the body block (prompts.py:490-514)

Remove this entire span from `_OBSERVATION_EXTRACT_PROMPT_TEMPLATE`:

```
---

SESSION_TYPE GUIDANCE — what counts as durable depends on session_type:

  code      — durable: bugfixes with diagnosed root cause, ...
  research  — durable: conceptual outcomes ...
  writing   — durable: authoring decisions ...
  general   — apply the QUALITY GATE directly without session-type bias.

---
```

The `Session-type guidance: {session_type_guidance}` line near the template top (line 441)
already injects the same content, now in full detail.

---

## Change 2 — compress triple-stated skip protocol

Today the "name a rejected candidate before skipping" rule appears 3×:
"SKIP DISCIPLINE" (615-617), "SKIP PROTOCOL" (619-644), bullet (646-650). ~36 lines.

**Fix:** one section. Replace prompts.py:615-650 with:

```
SKIP PROTOCOL:

A skip is a real cost — the LLM call to read this window already happened. Before
skipping, sweep the slice once more for ANY durable signal: a passing aside, a
constraint mentioned in passing, a rejected option, a configuration value used.
Bias toward extracting one low-importance observation over skipping outright.

If — after that sweep — the slice still has no qualifying observation, return a
structured skip. The `considered` list is REQUIRED and must be non-empty: name
every candidate fact you swept and rejected, each with the gate it failed.
A skip whose `considered` is empty or absent is treated as a downgraded skip —
the affect signal is preserved and logged as a warning, not silently discarded.

  {{"skipped": true,
    "failed_gate": "<falsifiable|durable|novel|load_bearing>",
    "reason": "<one sentence naming what the slice contained instead>",
    "considered": ["<candidate> — failed <gate_name>", ...]}}

`failed_gate` MUST be the FIRST quality-gate criterion the slice failed.

Affect signals (pushback / repetition / non-neutral valence) override skip: if the
AFFECT HINT shows any of those, you MUST extract at least one observation. User
interruptions (ctrl-c, "stop", "cancel", request cancelled mid-execution) also
override skip — treat them as behavioral friction regardless of the affect score.
```

Net: 36 lines → ~22. Same rules, every clause preserved (sweep, required non-empty
`considered`, first-failed-gate, downgraded-skip behavior, affect/interruption override).

---

## Guard

After applying: `python3 -m pytest tests/test_prompts.py tests/test_transcript_ingest.py`.
`tests/test_prompts.py:131` smoke-tests `format_extract_prompt` — confirm the longer dict
values do not break any assertion on prompt content.
