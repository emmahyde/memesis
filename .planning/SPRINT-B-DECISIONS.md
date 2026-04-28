# Sprint B Decisions

## OD-C: Validator implementation — stdlib dataclass

**Decision date:** 2026-04-27
**Owner:** WS-G

Stdlib `dataclasses` + manual `__post_init__`-style validation in
`_validate_stage1_core` (already implemented in `core/validators.py`).

**Rationale:** Pydantic is absent from `requirements*.txt` and `pyproject.toml`.
Adding it for one validator is unjustified dep weight. The existing stdlib
`dataclasses` approach with `_validate_stage1_core` covers all needs: enum
validation, range checks, pronoun-prefix rejection, and soft/hard modes.

**Trade-offs:**
- No automatic field coercion (intentional — panel §5 philosophy: no coercion,
  no silent passthrough).
- Validation logic is explicit rather than declarative; adding new fields
  requires manually extending `_validate_stage1_core`.
- No schema introspection / JSON schema export (not needed for current scope).

**Migration path if Pydantic is later adopted:**
- `Stage1Observation` and `Stage2Observation` dataclass fields map 1:1 to
  Pydantic `BaseModel` fields.
- `_validate_stage1_core` validators become `@validator` / `@field_validator`
  decorators.
- Soft mode (`validate_stage1_soft`) becomes a `model_validate` call with
  `strict=False` and a custom error collector.
- Migration is a clean rewrite of `core/validators.py` only; no other files change.

---

## OD-D: Cosine threshold for linked_observation_ids — 0.90

**Decision date:** 2026-04-27 (threshold set in Sprint A Wave 2 WS-F)
**Owner:** WS-G (audit plan)

**Default:** `0.90` in `core/linking.py:LINK_COSINE_THRESHOLD`
**Override:** `MEMESIS_LINK_THRESHOLD` env var (float, e.g. `MEMESIS_LINK_THRESHOLD=0.85`)

### Audit plan

**Trigger:** Run after 100 consolidations or 4 weeks, whichever comes first.

**Data source:** `backfill-output/observability/linking-trace.jsonl`
Each entry has: `memory_id`, `candidate_count`, `above_threshold_count`,
`selected` (with `topic_drift` flag), `threshold`.

**Step 1 — collect signal (after 100 consolidation runs):**
```bash
# Count total link decisions
jq 'select(.selected | length > 0)' linking-trace.jsonl | wc -l

# topic_drift rate across all selected links
jq '[.selected[].topic_drift] | flatten | (map(select(.)) | length) / length' \
  linking-trace.jsonl | python3 -c "import sys; vals=[float(l) for l in sys.stdin]; print(sum(vals)/len(vals))"
```

**Step 2 — sample 30 link pairs uniformly:**
```bash
jq -c 'select(.selected | length > 0) | {memory_id, selected}' linking-trace.jsonl \
  | shuf | head -30 > link-sample-30.jsonl
```
For each sampled pair, manually retrieve both memory contents from the DB and
score relevance 1–5 (1=spurious, 3=topically related, 5=tightly coupled).

**Step 3 — compute precision@3 by relevance band:**
- Band A (score ≥ 4): target ≥ 70% of links
- Band B (score 3–4): acceptable
- Band C (score < 3): false-positive; target < 15%

**Alarm thresholds:**
- `topic_drift_rate > 15%` across 100 links → threshold too low; raise to 0.95
- `above_threshold_count` median < 0.5 per consolidation → threshold too high; lower to 0.85
- Precision@3 Band C > 20% → raise threshold

**Tuning protocol (if alarm fires):**
Binary search across {0.85, 0.90, 0.95} on the 30-pair sample:
1. Re-run `find_links_for_observation` on the sample pairs at each threshold.
2. Recompute Band C false-positive rate.
3. Pick the lowest threshold with Band C < 15%.
4. Update `LINK_COSINE_THRESHOLD` default in `core/linking.py` and document here.

**Re-evaluate after:** 100 consolidations OR 4 weeks, whichever comes first.

---

## LLME-F9 / OD-B: session_type field — wired in Sprint B WS-G

**Decision date:** 2026-04-27
**Owner:** WS-G

### What shipped

- `Memory.session_type` column added in Sprint A WS-E (schema only).
- Sprint B WS-G wires detection, ingest propagation, and prompt priming.

### Detection (`core/session_detector.py`)

Heuristic only — no LLM calls. Priority order:

1. **cwd path hint** — `detect_session_type_from_cwd(cwd)`. Writing/research
   hints checked first (specific); code hints checked second (broad).
   - `code`: `/projects/`, `/repos/`, `/sector`, `/memesis`, `/code`, `/src`, `/dev`
   - `writing`: `/manuscript`, `/chapter`, `/prose`, `/draft`, `/novel`, `/writing`
   - `research`: `/research`, `/papers`, `/external_references`, `/notes`
2. **Tool-mix heuristic** — `detect_session_type_from_tools(tool_uses)`. Checks
   Edit/Write/Bash on code extensions, WebFetch/WebSearch + .md reads, Edit/Write
   on prose extensions.
3. **Default fallback** — `"code"` (memesis is software-first).

### Ingest propagation (`core/transcript_ingest.py`)

`tick()` extracts `cwd` from transcript entries, collects tool use entries,
calls `detect_session_type(cwd, tool_uses)`, and:
- Passes `session_type` to `extract_observations(rendered, session_type=...)`.
- Sets `obs["session_type"]` on each extracted observation for downstream validators.

`extract_observations()` accepts `session_type: str = "code"` and forwards it
into `OBSERVATION_EXTRACT_PROMPT.format(transcript=..., session_type=...)`.

### Prompt priming

- **Stage 1 (`OBSERVATION_EXTRACT_PROMPT`):** context line added near top:
  `"Session type: {session_type}"` — filled at format-time.
- **Stage 2 (`CONSOLIDATION_PROMPT`):** explicit null-default clause added to
  `work_event` section: `"Set work_event=null when session_type != 'code'
  (writing and research sessions have no code actions)."`

### Migration

`scripts/migrate_w5_schema.py` extended to back-derive `session_type` for
existing rows using `detect_session_type(row["cwd"])`. Run with `--commit`
after verifying dry-run output.

### Deferral note (from LLME-F9)

Full mode system (`plugin/modes/*.json`) remains deferred. The minimal viable
`session_type: code | writing | research` field on `Memory` covers the actual
multi-session-type usage pattern identified by LLME-F9 without the plugin
machinery overhead.
