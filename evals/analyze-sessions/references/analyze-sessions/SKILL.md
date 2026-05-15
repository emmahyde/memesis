---
name: analyze-sessions
description: >
  Forensic audit of the memesis memory pipeline for one or more Claude Code session IDs.
  Use when: "what did the pipeline miss", "audit pipeline output", "why wasn't X remembered",
  "what signal was lost in session", "check what observations were extracted",
  "analyze session(s)", /memesis:analyze-sessions. Extracts conversational signal via rg+jq,
  forms independent expected-memory judgment, retrieves actual pipeline output, runs a
  structured gap audit keyed to known failure modes, then uses panel-of-experts reasoning
  to steelman ≥5 solutions and proposes the best fix with ranked alternatives.
---

# Analyze Sessions — Pipeline Signal Audit

Forensic comparison of what a session *should* have produced versus what the pipeline
*actually* captured. Grounded in the known failure modes documented in `.context/`.

## Usage

```
/memesis:analyze-sessions ses_abc123 [ses_def456 ...]
/memesis:analyze-sessions ses_abc123 --stage stage1    # Stage 1 only
/memesis:analyze-sessions ses_abc123 --live            # force fresh LLM extraction
```

Bare session IDs (`ses_` stem of any JSONL in `~/.claude/transcripts/`). If none given,
use the 3 most-recent sessions from the cursor DB.

Run from `/Users/emmahyde/projects/memesis` (project root). Use `uv run python3` for
all Python invocations (not bare `python3`).

---

## Background: known pipeline failure modes

These are the documented gaps from `.context/`. Check each one during the audit.

### Stage 0 — Discovery / cursor

- **cwd extraction bug (#5 — BLOCKER)**: `read_transcript_from()` filters out attachment
  entries, so `cwd` never reaches `detect_session_type()`. All sessions may classify as
  `session_type=unknown`, defeating session-type-aware prompts/chunking entirely.
  Check: `cursors.db` `cwd` column is NULL for the session.
- **New session seeded at EOF**: first-contact sessions get cursor=EOF and extract nothing.
  This is by design, but means *the prior session* is the first real extraction window.

### Stage 1 — Extraction (transcript_ingest.py)

- **100% skip rate on research sessions** (confirmed: session 418d1c86, 11 windows × 0
  productive). `session_type=unknown` disables research-specific guidance; neutral-affect
  prefilter (`PREFILTER_RESEARCH_NEUTRAL`) drops them silently.
- **max_tokens cap**: 16K-char windows can produce 5+ obs but return `[]` silently due
  to truncation — `parse_error_repaired` trace event is the signal.
- **Skip-reason not persisted**: when Stage 1 skips, `failed_gate` and `reason` aren't
  surfaced in the report; only `outcome` is retained. Diagnosis requires raw trace JSONL.
- **Dead-key lookup** (`chunking_suboptimal` → was `chunking_mismatch_user_anchored_low_turns`):
  confirmed-rule branch in `select_chunking()` never fires (Tier 0 bug, line 647).
- **Importance gate at 0.3** silently drops observations. `low_importance_dropped` count
  is logged to `drop_stats` but only if the caller passes the dict in.
- **Dedup cross-window**: without Reframe A (`REFRAME_A_ENABLED`), each window extracts
  independently and Stage 1.5 must reconcile paraphrase duplicates later.

### Stage 1.5 — Synthesis (issue_cards.py)

- **synthesis_overgreedy** (3 confirmed fires): observations forced into cards that don't
  cohere. Rule 0 (entity-overlap orphan gate) was added in Tier 1 to address this.
  Check `synthesis_overgreedy` in self-reflection rules.
- **evidence_obs_indices hallucination**: card evidence indices never validated against
  input observation count. Index `999` on 10 obs silently passes (Tier 2 item 15).
- **Mixed valence collapse**: `mixed` enum exists in schema but prompt doesn't instruct
  its use; evolving-reaction cards collapse to a single valence.
- **Orphan rendering gap**: `audit_pipeline_dimensions.py` renders cards but not orphan
  text — 6 orphans invisible in audit output (Tier 0 defect 3).

### Stage 2 — Consolidation / PreCompact

- **RISK-01 — PreCompact timeout (P0 Critical)**: 30s wall-clock budget covers ≥4 serial
  LLM calls (curate → reconsolidate → crystallize → reflect). At 2–10s per call, budget
  exhausts mid-pipeline. Ephemeral buffer consumed; memories half-promoted with no
  rollback. Failure is silent and cumulative.
- **Stage 2 session classification**: same cwd bug affects consolidation's session-type
  context; consolidation prompt may use wrong genre framing.

### Cross-cutting

- **affect_signal_no_extraction**: windows where `affect.max_boost > 0` but Stage 1.5
  excluded them. `windows_with_affect_signal_but_no_card` stat tracks this (Tier 2 item 14).
- **monotone_knowledge_lens**: fires when `unique_knowledge_types_emitted == 1` AND
  `final_observations >= 5` — single-axis extraction missing breadth.
- **confirmed_rule_no_action**: any rule with `fire_count >= 5` and a proposed action
  but no corresponding parameter override means known issues are going unfixed.
- **RISK-04 — Secret persistence**: API keys / tokens observed in sessions can be
  embedded and injected into future prompts. No redaction pass exists.

---

## Step 1 — Extract conversational signal from transcript

For each session ID, locate the transcript:

```bash
TRANSCRIPT=~/.claude/transcripts/{session_id}.jsonl
wc -l "$TRANSCRIPT"
```

Run these in parallel (use `ctx_batch_execute`):

```bash
# All user messages
jq -r 'select(.type=="user") | .message.content |
  if type=="string" then . else (.[].text? // "") end' "$TRANSCRIPT"

# Git commits Claude authored (Bash tool calls)
jq -r 'select(.type=="assistant") | .message.content[]? |
  select(.type=="tool_use" and .name=="Bash") | .input.command' "$TRANSCRIPT" \
  | rg -i 'git commit' | head -20

# Files edited/written
jq -r 'select(.type=="assistant") | .message.content[]? |
  select(.type=="tool_use" and (.name=="Edit" or .name=="Write")) |
  "\(.name): \(.input.file_path)"' "$TRANSCRIPT" | sort -u

# Explicit corrections and pushback
jq -r 'select(.type=="user") | .message.content |
  if type=="string" then . else (.[].text? // "") end' "$TRANSCRIPT" \
  | rg -i '^\s*(no[,.]|don.t|stop|wrong|actually|not that|incorrect|revert|undo|wait)' | head -20

# Decisions, plans, key choices
jq -r 'select(.type=="assistant") | .message.content |
  if type=="string" then . else (.[].text? // "") end' "$TRANSCRIPT" \
  | rg -i '(decided|decision|approach|plan|going to|will use|chose|picked|instead)' | head -20

# cwd (session working directory — critical for session_type detection)
jq -r '.cwd // .message.cwd // empty' "$TRANSCRIPT" | head -3

# Tool mix (for session_type heuristic)
jq -r 'select(.type=="tool_use" or (.message.type=="tool_use")) |
  .tool_name // .message.name // empty' "$TRANSCRIPT" | sort | uniq -c | sort -rn | head -10
```

Summarize as:

```
SESSION SIGNAL SUMMARY
======================
session_id:    <id>
cwd:           <path or MISSING — critical>
lines:         N
git_commits:   [...]
files_touched: [...]
corrections:   [quotes]
decisions:     [key moments]
user_goals:    [inferred]
session_type:  <code|research|writing|unknown — your inference>
```

---

## Step 2 — Establish expected observations (independent judgment)

Read the signal summary. Before checking what the pipeline captured, form your own list.

Design ethos anchors:
- **Signal Over Storage**: aim for the ~15% that matters. Corrections > preferences >
  decisions > domain knowledge > workflow patterns.
- **Cognitive Realism**: affect signal (friction, surprise, emphasis) elevates importance.
  A correction during a frustrated exchange is worth more than a neutral preference.
- **Progressive Durability**: ephemeral is fine; the question is whether it should have
  been captured at all at Stage 1.

```
EXPECTED OBSERVATIONS
=====================
1. [kind]  <content>
   importance: 0.0–1.0  |  signal_strength: HIGH/MEDIUM/LOW
   source: "<exact quote>"

2. [kind]  <content>
   ...
```

Valid `kind` values (W5 schema):
`correction | preference_signal | decision_context | domain_knowledge |
 workflow_pattern | self_observation | shared_insight`

---

## Step 3 — Retrieve actual pipeline output

### 3a. Cursor state (was the session processed, and with what cwd?)

```bash
sqlite3 ~/.claude/memesis/cursors.db \
  "SELECT session_id, transcript_path, last_byte_offset, cwd, last_run_at
   FROM transcript_cursors WHERE session_id='<id>';"
```

Key checks:
- `last_byte_offset = 0` or row missing → **never processed** (Stage 0 miss)
- `cwd IS NULL` → **cwd extraction bug active** — session_type will be `unknown`
- `last_run_at` timestamp → how long since last ingest tick

### 3b. Ephemeral buffer (Stage 1 output)

```bash
# Find the project-scoped memory dir for this session's cwd
# For memesis project:
EPHEM=~/.claude/projects/-Users-emmahyde-projects-memesis/memory/ephemeral
ls -lt "$EPHEM"/*.md 2>/dev/null | head -10

# Search all ephemeral files for content that could be from this session
rg -l '' "$EPHEM" 2>/dev/null | xargs rg -l 'correction\|preference\|decision' 2>/dev/null
```

Read the most recent 2–3 ephemeral files. Observations are formatted as:
```
## [observation_type | YYYY-MM-DD]
<content>
```

### 3c. Trace JSONL (Stage 1 diagnostic events)

```bash
TRACE_DIR=~/.claude/memesis/traces
ls -lt "$TRACE_DIR"/*.jsonl 2>/dev/null | head -5

# Filter for this session's events (exact match + replay variants)
jq -r --arg SID "<id>" \
  'select(.session_id == $SID or (.session_id | startswith("replay-" + $SID))) |
   "\(.ts // "") \(.event_type) \(.data | tojson | .[:100])"' \
  "$TRACE_DIR"/*.jsonl 2>/dev/null | head -60
```

Key events to look for:

| Event | Meaning |
|-------|---------|
| `stage1_extract_start` | Stage 1 ran for this session |
| `stage1_extract_end` | `n_obs`, `outcome` — were observations found? |
| `stage1_append_ephemeral` | `n_appended` — how many reached the buffer |
| `stage1_skip` | `failed_gate`, `reason` — why Stage 1 bailed |
| `parse_error_repaired` | max_tokens truncation hit; JSON was repaired |
| `pre_filtered_low_affect` | prefilter dropped this window before LLM call |
| `precompact_start/end` | Stage 2 ran; was there a timeout? |
| `cross_window_dedup_hits` | Reframe A active and deduping |

### 3d. Self-reflection rules that fired

```bash
uv run python3 -c "
import sys; sys.path.insert(0, '.')
from core.database import init_db, close_db
from core.models import ConsolidationLog
init_db()
rows = list(ConsolidationLog.select().order_by(ConsolidationLog.created_at.desc()).limit(20))
for r in rows:
    print(r.created_at, r.session_id or 'N/A', str(r.action)[:80])
close_db()
"
```

Also check if known rules fired for the session's date range:

```bash
uv run python3 -c "
import sys; sys.path.insert(0, '.')
from core.database import init_db, close_db
from core.rule_registry import ParameterOverrides
init_db()
reg = ParameterOverrides()
print(reg.list_rules())
close_db()
" 2>/dev/null
```

Look for: `synthesis_overgreedy`, `low_obs_yield_per_call`, `repeated_facts_high`,
`affect_signal_no_extraction`, `monotone_knowledge_lens`, `confirmed_rule_no_action`.

### 3e. Production DB observations

```bash
uv run python3 -c "
import sys; sys.path.insert(0, '.')
from core.database import init_db, close_db
from core.models import Observation, Memory
import json
init_db()

obs = list(Observation.select().where(Observation.session_id == '<id>'))
print(f'Observations in DB for session: {len(obs)}')
for o in obs:
    print(f'  [{o.kind}] imp={getattr(o, \"importance\", \"?\")} {str(o.observation_text)[:120]}')

mems = list(Memory.select().where(Memory.evidence_session_ids.contains('<id>')))
print(f'Memories citing this session: {len(mems)}')
for m in mems:
    print(f'  [{m.stage}] {str(m.content)[:120]}')

close_db()
"
```

Collect into:

```
ACTUAL PIPELINE OUTPUT
======================
cursor_present:     yes/no  (cwd present: yes/no)
stage1_ran:         yes/no  (n_obs_extracted: N, n_appended: N)
prefilter_dropped:  N windows
parse_errors:       N
stage2_ran:         yes/no  (timeout: yes/no)
observations_in_db: N
memories_promoted:  N
rules_fired:        [list]
```

---

## Step 4 — Structured gap audit

Build the gap table:

```
AUDIT TABLE
===========
Signal                       | Importance | Actual        | Stage lost   | Failure mode
-----------------------------|------------|---------------|--------------|--------------------
<correction about X>         | 0.75 HIGH  | MISSING       | Stage 1      | cwd bug → unknown type
<decision to use Y>          | 0.60 MED   | CAPTURED      | —            | OK
<preference for Z>           | 0.70 HIGH  | PARTIAL wrong | Stage 1      | max_tokens truncation
<workflow pattern W>         | 0.35 LOW   | MISSING       | acceptable   | below importance floor
<domain knowledge D>         | 0.55 MED   | MISSING       | Stage 2      | RISK-01 timeout
```

Then summarize:

```
AUDIT SUMMARY
=============
Expected observations:    N
  Captured (exact):       N  (N%)
  Captured (degraded):    N  (N%)
  Missing — recoverable:  N  (N%)
  Missing — acceptable:   N  (N%)

Stage breakdown for losses:
  Stage 0 (not ingested):        N  — cursor miss or new-session seeding
  Stage 1 (session_type=unknown): N  — cwd extraction bug
  Stage 1 (prefilter):           N  — neutral-affect gate
  Stage 1 (importance < 0.3):    N  — importance gate drop
  Stage 1 (max_tokens truncated): N  — parse_error_repaired events
  Stage 1.5 (overgreedy synth):  N  — synthesis_overgreedy rule
  Stage 2 (timeout/no-rollback): N  — RISK-01

Decision identification: N/M correctly surfaced
Affect signal captured:  N windows with affect → N extractions (expected N)
```

---

## Step 5 — Panel-of-experts reasoning (do this privately)

Convene five experts, each steelmanning a distinct solution. Do not collapse early.

1. **Signal Fidelity Engineer** — hates false negatives; wants every correction captured.
   Will push for prompt rewrites, kind taxonomy expansion, or lower importance floor.

2. **Precision Skeptic** — hates noise; `synthesis_overgreedy` is their nightmare.
   Will push for tighter orphan gating, higher importance floors, Rule 0 enforcement.

3. **Pipeline Architect** — asks "where in the pipeline is the fix cheapest?"
   Will evaluate: cursor schema fix (#5), max_tokens bump (Tier 0 Bug 4), session-type
   prompt injection (Tier 1 Item 7), Reframe A stateful dedup (Tier 2 Item 18).

4. **Cognitive Realism Advocate** — wants affect to drive salience correctly.
   Will push for `affect_signal_no_extraction` rule enforcement, Kensinger bump
   write-site correctness (D1=C), mixed-valence convention.

5. **Product Pragmatist** — ranks by (impact × confidence) / implementation cost.
   Will evaluate: cwd bug fix (1-function change, high impact), max_tokens bump
   (1-line change), session-type guidance (prompt-only, no schema change), vs
   Reframe A (larger refactor, opt-in flag exists).

Known context to draw on:
- Tier 0 fixes: dead-key lookup, max_tokens cap, skip-reason persistence, orphan rendering
- Tier 1 fixes: session-type guidance, skip friction sub-rule, low-affect prefilter
- Tier 2 fixes: ExtractionRunStats fields, synthesis enhancements, Reframe A
- Tier 3 fixes: #29 evidence_obs_indices, #32 affect→importance reconciliation,
  #33 SESSION_TYPE_GUIDANCE in OBSERVATION_EXTRACT_PROMPT, #34 SKIP_PROTOCOL sub-rule
- RISK-01: PreCompact two-phase architecture
- RISK-04: secret redaction pass

Rank solutions by: (expected_recall_improvement × confidence) / implementation_cost.

---

## Step 6 — Proposed solution

```
PROPOSED SOLUTION
=================
Title:        <name>
Targets:      <pipeline stage(s) and file(s)>
Root cause:   <1–2 sentences>

Change:
  File: <path:line>
  What: <specific modification>
  Why:  <why this addresses the root cause>

Expected improvement: <what signal should now be captured>
Risk:   <what might get worse>
Test:   <verification — /memesis:evolve replay, specific pytest, audit script>
Tier:   <Tier 0 bug / Tier 1 tactical / Tier 2 enhancement / RISK fix>
```

---

## Step 7 — Alternatives summary

```
ALTERNATIVE SOLUTIONS
=====================
#  | Approach                              | Key tradeoff                | Why not top
---|---------------------------------------|----------------------------|------------------
2  | ...                                   | ...                        | ...
3  | ...                                   | ...                        | ...
4  | ...                                   | ...                        | ...
5  | ...                                   | ...                        | ...
```

---

## Reference: key files

| Purpose | Path |
|---------|------|
| Stage 1 extraction | `core/transcript_ingest.py` |
| Transcript parsing | `core/transcript.py` — `read_transcript_from()` |
| Stage 1 prompt | `core/prompts.py` — `OBSERVATION_EXTRACT_PROMPT`, `SESSION_TYPE_GUIDANCE` |
| Session type detection | `core/session_detector.py` — `detect_session_type()` |
| Cursor store | `core/cursors.py` — `CursorStore` (`~/.claude/memesis/cursors.db`) |
| Stage 1.5 synthesis | `core/issue_cards.py` — `synthesize_issue_cards()` |
| Stage 2 consolidation | `core/consolidator.py` |
| Self-reflection rules | `core/self_reflection_extraction.py` — `ExtractionRunStats` |
| Rule registry | `core/rule_registry.py` — `ParameterOverrides` |
| Trace events | `~/.claude/memesis/traces/*.jsonl` |
| Ephemeral buffer | `~/.claude/projects/.../memory/ephemeral/session-YYYY-MM-DD.md` |
| Production DB | `~/.claude/memory/index.db` (Peewee — never `sqlite3` for writes) |
| Planning docs | `.context/CONTEXT-*.md`, `.context/RISK-REGISTER.md` |

## Reference: audit tier map

| Tier | Items | Status |
|------|-------|--------|
| Tier 0 | Dead-key lookup; max_tokens cap; skip-reason persistence; orphan rendering | Bug fixes |
| Tier 1 | Session-type guidance; skip friction; low-affect prefilter; schema field promotion; new self-reflection rules | 1-week tactical |
| Tier 2 | ExtractionRunStats fields; synthesis enhancements; Reframe A stateful dedup | Enhancement |
| Tier 3 | #29 evidence validation; #32 affect→importance; #33 SESSION_TYPE_GUIDANCE; #34 SKIP_PROTOCOL | In-flight |
| RISK-01 | PreCompact two-phase + rollback | P0 Critical |
| RISK-04 | Secret redaction pass | P0 Critical |
