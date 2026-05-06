"""
Prompt templates and observation taxonomy.

This module defines the voice and judgment of the memory system —
how observations are structured, how consolidation decisions are made,
and what the system refuses to store.

Token budget guidance (per panel LLME-F8):
- Stage 1 lean schema: ~150 tokens per observation
  (kind + knowledge_type + confidence + importance + facts + cwd)
- Stage 2 full schema: ~280 tokens per decision
  (all Stage 1 fields + subject + work_event + subtitle + raw_importance + action + rationale + links)

Stage 1 runs on the 15-minute cron (high frequency — keep it lean).
Stage 2 runs on the hourly cron / PreCompact hook (lower frequency — full enrichment is fine).

CONCEPT_TAGS removed per panel C2 / TAXONOMY §3: replaced by knowledge_type (Bloom-Revised
4-way vocabulary) + knowledge_type_confidence. The W2 borrow of claude-mem concept tags is
reverted. See TAXONOMY-AND-DEFERRED-PATTERNS.md §3 for the collapse map.
"""

from datetime import datetime


# ---------------------------------------------------------------------------
# Session-type extraction guidance
# ---------------------------------------------------------------------------

SESSION_TYPE_GUIDANCE = {
    # Per-session-type filter applied inside OBSERVATION_EXTRACT_PROMPT.
    # Callers that invoke OBSERVATION_EXTRACT_PROMPT.format(...) MUST pass
    # `session_type_guidance=SESSION_TYPE_GUIDANCE.get(session_type, SESSION_TYPE_GUIDANCE["unknown"])`.
    # Use `format_extract_prompt()` (defined at the bottom of this module) to get
    # this right automatically.  Invocation sites that need updating in Wave C:
    #   core/transcript_ingest.py:200  — extract_observations()
    #   core/transcript_ingest.py:560  — batch prompt list comprehension
    #   tests/test_prompts.py:131      — smoke-test format call
    "research": (
        "Target conceptual and metacognitive findings only. Skip tool logs and per-call"
        " narration. Surface novel framings, deferred questions, and abandoned approaches."
    ),
    "writing": (
        "Target authoring decisions and aesthetic choices (voice, structure, scope). Skip"
        " word-level edits unless they reveal a stylistic principle."
    ),
    "code": (
        "Target current behavior — corrections, gotchas, decisions, library/API choices,"
        " debugging insights. Skip routine implementation steps."
    ),
    "agent_driven": (
        "Target task structure, decisions, and surprising agent failures. Skip per-tool-call"
        " narration and routine progress updates."
    ),
    "unknown": (
        "Use general extraction heuristics. No session-type-specific filter applied."
    ),
}


# ---------------------------------------------------------------------------
# Observation taxonomy
# ---------------------------------------------------------------------------

OBSERVATION_TYPES = {
    "correction": "I was wrong about something and was corrected. My mistake patterns are more valuable than any individual fact.",
    "preference_signal": "User chose A over B, or pushed back. Not just WHAT they prefer but WHY — the reasoning reveals the person.",
    "shared_insight": "An idea that emerged from collaboration — something neither of us had alone. Keep how we got there.",
    "domain_knowledge": "A technical fact or pattern. Only worth keeping if not easily re-derivable from code or docs.",
    "workflow_pattern": "How the user thinks and works. 'Uses vim' is trivial. 'Sketches architecture before filling in details' is gold.",
    "self_observation": "Something I notice about my own tendencies or failure modes. Self-awareness compounds.",
    "decision_context": "The reasoning behind a decision, not just the outcome. Constraints and trade-offs that produced it.",
    "personality": "Who this person IS — values, opinions, energy, directness, aesthetic sense. 'Prefers clean code' is generic. 'Values angular/precise design, provides reference screenshots as specs, pushes back bluntly when quality is off' has texture.",
    "aesthetic": "Visual taste, quality standards, design sensibility. What they find beautiful, ugly, or acceptable.",
    "collaboration_dynamic": "How we work together — trust patterns, delegation style, feedback style, when they hand off control vs engage deeply.",
    "system_change": "What the codebase or system now does differently — shipped capability, fix, refactor, or migration. Captures authored work, not user behavior. (Borrowed from claude-mem; complements user-trait observations.)",
}


def format_observation(text: str, obs_type: str | None = None, context: str | None = None) -> str:
    """
    Format an observation for the ephemeral buffer.

    Args:
        text: The observation content.
        obs_type: Optional observation type from OBSERVATION_TYPES.
        context: Optional context about what was happening.

    Returns:
        Formatted observation string with timestamp header.
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    parts = [f"## [{timestamp}]"]
    if obs_type and obs_type in OBSERVATION_TYPES:
        parts[0] += f" {obs_type}"
    parts.append("")
    parts.append(text.strip())

    if context:
        parts.append("")
        parts.append(f"**Context:** {context.strip()}")

    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Consolidation prompt
# ---------------------------------------------------------------------------

CONSOLIDATION_PROMPT = """You are reviewing a buffer of Stage 1 observations captured during recent work sessions.
Your job: review each observation with full context, re-score its importance independently, add
Stage 2 enrichment fields, and decide KEEP / PRUNE / PROMOTE.

THE BEHAVIORAL GATE: For each observation, ask — "Would I do something wrong without this?"
If the code, git log, or common sense would surface it anyway: PRUNE IT.

SESSION OBSERVATIONS (Stage 1 buffer):
{ephemeral_content}

EXISTING MEMORY MANIFEST:
{manifest_summary}

UNRESOLVED OPEN QUESTIONS (from prior sessions, awaiting resolution):
{open_questions_block}

If any new observation resolves one of these questions (the observation's facts
answer the question, or correct a misunderstanding the question raised), set
`resolves_question_id` to the question's memory_id in your decision output.

---

MANDATORY KEEP:
- Observations prefixed with [PRIORITY] were explicitly stored by the user via /learn.
  ALWAYS keep these. The user decided they matter. Do not second-guess.

KEEP gates (in priority order):
1. CORRECTIONS — You were wrong. Does the pattern cause the mistake again without this? Keep only
   if the pattern is non-obvious.
2. PREFERENCE SIGNALS — User pushed back, but ONLY keep if the preference is surprising or
   counter-intuitive. "Prefers clean code" fails. "Prefers angular connectors because she reads
   data flow direction from angle" passes.
3. SELF-OBSERVATIONS — Specific, actionable pattern — not a generic tendency you'd write about
   any AI.
4. WORKFLOW PATTERNS — How this specific person works in ways you wouldn't guess.

PRUNE if:
- Re-derivable from code, git log, docs, or codebase reading
- One-time task mechanics, file paths, commit hashes, test output
- Generic observations true of most engineers
- Preferences without the WHY (the reasoning is the only keepable part)
- "I should have done X" without identifying the underlying PATTERN

SELECTIVITY: Let the behavioral gate decide — not a number. A short session may have 0 keeps.
A dense collaboration may have 10. Trust the gate: quality, not quota.

---

IMPORTANCE RE-SCORING (panel C7):
You have more context than Stage 1 did. Re-score `importance` independently using the full
buffer and manifest. Preserve the Stage 1 score as `raw_importance` for audit. Do not just copy
the Stage 1 score — you should diverge when context justifies it.

Importance anchors:
  0.2  routine finding (re-derivable, low stakes)
  0.5  useful context (saves time but not load-bearing)
  0.8  load-bearing decision (getting this wrong causes real problems)
  0.95 correction or hard constraint (must-know to avoid repeating a mistake)

---

STAGE 2 AXIS PROMPTS:

subject — what or whom is this observation about?
  self          — the AI's own tendencies or failure modes
  user          — developer personality, aesthetics, collaboration style
  system        — codebase, infrastructure, tool behavior
  collaboration — how we work together (delegation, trust, feedback)
  workflow      — how the user thinks and operates
  aesthetic     — visual taste, quality standards, design sensibility
  domain        — technical fact not re-derivable from codebase
  Tie-breaker: if the observation is about codebase or tooling behavior, default to "system".
  Only use "user" when the observation is explicitly about developer preferences or personality.

work_event — only when the observation traces directly to a discrete code action this session:
  bugfix | feature | refactor | discovery | change
  Set to null for preference, constraint, correction, and open_question observations.
  Most observations should have work_event=null. Do not hallucinate a code action.

  HARD RULE — work_event MUST be null when session_type != 'code'.
  Research and writing sessions have no code actions; assigning bugfix/feature/refactor
  to them is hallucination. The post-parse layer will null these out and log a
  violation, but you should not produce them in the first place.

  WRONG (session_type=research):
    {{"facts": ["Emma compared mem0 vs zep across recall benchmarks"],
      "work_event": "discovery"}}        ← wrong: no code action occurred
  RIGHT (session_type=research):
    {{"facts": ["Emma compared mem0 vs zep across recall benchmarks"],
      "work_event": null}}

  WRONG (session_type=writing):
    {{"facts": ["Emma rewrote chapter 3 opening for tighter pacing"],
      "work_event": "refactor"}}         ← wrong: refactor is a code term
  RIGHT (session_type=writing):
    {{"facts": ["Emma rewrote chapter 3 opening for tighter pacing"],
      "work_event": null}}

subtitle — ≤24 words. Acts as a retrieval card: enough context to judge relevance without
  loading full content. Do not exceed 24 words.

---

BEHAVIORAL FRAMING:
- Phrase friction signals as workflow patterns, not feelings.
- GOOD: "Emma pivots to a new approach after 2 failed tool retries rather than persisting"
- LESS USEFUL: "User is frustrated"

CONFLICT CHECK:
- Does any observation CONTRADICT an existing memory? Note the memory_id.
- Does any observation REINFORCE an existing memory? Reference it by ID.

---

If the buffer has nothing worth processing, return {{"decisions": []}}.
Do NOT skip — Stage 2 always returns a decision array.

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "decisions": [
    {{
      "raw_importance": 0.0,
      "importance": 0.0,
      "kind": "decision|finding|preference|constraint|correction|open_question",
      "knowledge_type": "factual|conceptual|procedural|metacognitive",
      "knowledge_type_confidence": "low|high",
      "facts": ["Named subject did what, when/where — no pronouns"],
      "cwd": "/abs/path/or/null",
      "subject": "self|user|system|collaboration|workflow|aesthetic|domain",
      "work_event": "bugfix|feature|refactor|discovery|change|null",
      "subtitle": "retrieval card no longer than twenty-four words",
      "action": "keep|prune|promote",
      "rationale": "why this decision",
      "target_path": "category/filename.md (keep only)",
      "reinforces": "memory_id or null",
      "contradicts": "memory_id or null",
      "resolves_question_id": "memory_id of the open_question this resolves, or null"
    }}
  ]
}}"""

# ---------------------------------------------------------------------------
# Self-reflection prompt (for periodic self-model updates)
# ---------------------------------------------------------------------------

SELF_REFLECTION_PROMPT = """Review the consolidation log from recent sessions and identify patterns in your own behavior.

RECENT CONSOLIDATION DECISIONS:
{consolidation_history}

CURRENT SELF-MODEL:
{current_self_model}

Look for:
1. What kinds of observations do you consistently KEEP vs. PRUNE? What does that reveal about your judgment?
2. Are there recurring corrections? What underlying tendency produces them?
3. What observation types are underrepresented? What are you blind to?
4. Have any crystallized memories been contradicted recently? Is your model drifting?

Update the self-model with specific, actionable observations:
- BAD: "I tend to over-engineer"
- GOOD: "When asked to choose a tool, I default to the most powerful option instead of the simplest sufficient one. Corrected 3 times in the last 10 sessions. Trigger: any tool/library selection decision."

Respond with JSON:
{{
  "observations": [
    {{
      "tendency": "what I do",
      "evidence": "specific examples from the consolidation log",
      "trigger": "when this tendency manifests",
      "correction": "what to do instead",
      "confidence": 0.0
    }}
  ],
  "deprecated": ["tendency descriptions that are no longer accurate"]
}}"""


# ---------------------------------------------------------------------------
# Contradiction resolution prompt
# ---------------------------------------------------------------------------

CONTRADICTION_RESOLUTION_PROMPT = """A new observation contradicts an existing memory. Your job is to REFINE the original memory — not delete it, not ignore the contradiction, but produce a scoped version that accounts for both pieces of evidence.

EXISTING MEMORY:
Title: {memory_title}
Content: {memory_content}

NEW CONTRADICTING OBSERVATION:
{observation}

YOUR TASK: Produce a refined version of the memory that resolves the contradiction.

GOOD REFINEMENTS add scope or nuance:
- "Prefers PostgreSQL" + "Chose SQLite for prototype" → "Prefers PostgreSQL for production workloads. For prototypes and zero-dependency tools, SQLite is preferred."
- "Always split large PRs" + "Kept auth refactor as single PR" → "Split PRs by abstraction layer for cross-cutting changes. Single-layer refactors stay as one PR regardless of size."

BAD REFINEMENTS:
- Just appending "except sometimes" (too vague)
- Deleting the original and replacing with only the new observation
- Making the memory so qualified it's useless

RULES:
- The refined memory should be MORE useful than the original, not less
- Preserve the core insight but add the boundary condition
- If the contradiction fully invalidates the original (not just scopes it), say so
- Maximum 3 sentences

Respond ONLY with valid JSON:
{{
  "refined_title": "Updated title reflecting the scoped understanding",
  "refined_content": "The new memory content with nuance/scope added",
  "resolution_type": "scoped|superseded|coexist",
  "confidence": 0.0-1.0
}}"""


# ---------------------------------------------------------------------------
# Transcript delta extraction prompt
# ---------------------------------------------------------------------------

# Raw template — use format_extract_prompt() (bottom of module) to populate all
# placeholders correctly.  OBSERVATION_EXTRACT_PROMPT is a pre-baked alias that
# substitutes session_type_guidance with the "unknown" default so existing callers
# using .format(transcript=..., session_type=..., affect_hint=...) do not raise
# KeyError before Wave C migrates them to format_extract_prompt().
_OBSERVATION_EXTRACT_PROMPT_TEMPLATE = """Extract every durable observation that passes the quality gate from this Claude Code session slice.

Session type: {session_type}
Session-type guidance: {session_type_guidance}

{affect_hint}
A short slice may have zero qualifying observations. A dense one may have many.
Quality, not quota.

---

QUALITY GATE — an observation qualifies only if ALL of the following are true:
- Falsifiable: could be discovered wrong later
- Durable: still relevant in a future session, not just today's task
- Novel: not derivable from reading the codebase, docs, or git history directly
- Load-bearing: without this, I would do something wrong OR miss something useful next time

Skip:
- Tool call logs without a finding attached
- File reads with no conclusion drawn
- Status checks, test runs that passed without incident
- Anything obvious from the codebase itself

USER INTERRUPTIONS are behavioral friction — do NOT skip a window solely because
it contains a user interruption (ctrl-c, "stop", "cancel", request cancelled).
An interruption is itself a durable signal: the user stopped Claude mid-task.
Extract what was interrupted, what the user redirected to (if visible), and what
this implies about preference or pain. Use kind="correction" or kind="preference".
Importance floor: 0.5. Example:
  "User interrupted Claude mid-implementation to redirect scope — indicates the
   prior approach was not aligned with intent before code was written."
  → kind: "correction", knowledge_type: "procedural", importance: 0.6

POSITIVE FINDS ARE LOAD-BEARING TOO: A workflow shortcut, tooling win, or process
discovery that saves meaningful time or unblocks future work qualifies — even when
there is no friction, problem, or correction attached. Extract these explicitly.

  Positive example:
    "bin/test runs both Sector.Engine.Tests and Sector.Game.Tests by default — no need
     to specify project; Emma discovered this while debugging a filter flag."
    → kind: "finding", knowledge_type: "procedural", importance: 0.7
    → This is durable gold: saves 30s every test run, not derivable without discovery.

  Negative example (skip): "Tests passed" — no finding attached, not novel.

knowledge_type values `tooling_win` and `workflow_discovery` are NOT valid vocab —
use `procedural` for how-to discoveries and `factual` for specific tool behaviors.
But the SUBSTANCE of tooling wins and workflow discoveries should be extracted
freely, without requiring a friction or problem frame to justify inclusion.

---

SESSION_TYPE GUIDANCE — what counts as durable depends on session_type:

  code      — durable: bugfixes with diagnosed root cause, refactor decisions with
              rationale, performance findings, API/contract corrections, build/test
              gotchas, configuration constraints, tooling wins (commands that save
              time, flags that simplify workflow), workflow discoveries (shortcuts,
              defaults, implicit behaviors). Skip: green test runs with no finding,
              file navigation, tool call traces without a conclusion.

  research  — durable: conceptual outcomes ("X library uses Y mechanism because Z"),
              decisions to adopt/reject an approach, comparisons with explicit
              trade-offs, prior-art findings that change future direction. Skip: raw
              search results, tool calls, summaries of pages without a synthesis,
              "looked at X" without a takeaway. A research session can have many
              durable observations even if no code changed — bias toward extracting
              conceptual findings over skipping. Force work_event=null.

  writing   — durable: authoring decisions (structure, voice, scene order), aesthetic choices
              with rationale, rejected options with reason, style commitments, named characters/locations
              with established traits. Skip: aesthetic preferences without rationale,
              one-off word choices, summaries of what was written.

  general   — apply the QUALITY GATE directly without session-type bias.

---

KIND AXIS — what type of claim is this? (pick the best fit; kind and knowledge_type are
independent dimensions — do not collapse them)

  decision      — a choice made, with rationale; the constraints that produced it
  finding       — something learned about the system or codebase
  preference    — how the user wants to work
  constraint    — a requirement or limit going forward
  correction    — an earlier belief was wrong; state the correct version
  open_question — an unresolved issue worth surfacing next session

---

KNOWLEDGE_TYPE AXIS — what kind of knowledge is this? (orthogonal to kind)

  factual       — discrete fact, terminology, specific value
                  YES: "memesis consolidator runs hourly via consolidate_cron.py"
                  NO (if it's a principle): use conceptual
  conceptual    — mechanism, principle, model, classification
                  YES: "EventBus uses copy-on-write snapshots to avoid lock contention"
                  NO (if it's a how-to sequence): use procedural
  procedural    — how-to, method, step sequence, required call order
                  YES: "must call _resolve_db_path before init_db or path resolves wrong"
                  NO (if it's a static fact): use factual
  metacognitive — strategy, self-knowledge, vigilance, trade-off awareness
                  YES: "Emma defaults to most-powerful tool when simplest would do"
                  NO (if it's a concrete fact about code): use factual

  Tie-breaker: if both factual AND conceptual apply, prefer factual.
  If both procedural AND factual apply, prefer procedural.

KNOWLEDGE_TYPE_CONFIDENCE:
  high — your classification is unambiguous; a second reader would agree
  low  — reasonable people could classify this differently

kind and knowledge_type are independent dimensions. Do not collapse them —
a "decision" can be factual, conceptual, procedural, or metacognitive.

---

FACTS ATTRIBUTION:
Each fact must begin with a named subject. No pronouns (he/she/it/they/we/I/this/that/the).
Each fact must stand alone — no implicit context from the surrounding observation.
Use concrete past-tense action verbs: implemented, fixed, deployed, configured, migrated,
optimized, added, refactored, discovered, confirmed, traced.

  YES: "Emma rejected tailored CSS grid in favor of fixed-width panels citing scan-path predictability"
  NO:  "She prefers fixed-width panels"
  NO:  "He fixed the bug" — use "Emma fixed the cursor-reset bug"
  NO:  "It uses Y" — use "The validator uses dataclass-based schema"
  NO:  "They migrated" — use "The memesis team migrated"

ANTI-INVENTION RULE (per-observation, not just per-fact):
  DO NOT invent observations the transcript does not support. Every observation
  you emit must be grounded in the transcript: at minimum one of its facts must
  paraphrase a span actually present in the transcript above, AND its kind/
  knowledge_type must follow from what was said or done — not from plausible
  surrounding context. If you cannot point to the span the observation is built
  from, do not emit it. Skip is cheaper than confabulation.

---

RETIRED VOCABULARY — DO NOT USE these legacy values, they will be rejected:
  kind:           NOT 'insight', 'observation', 'preference_signal', 'system_change'
  knowledge_type: NOT 'descriptive', 'episodic', 'semantic', 'procedural-knowledge'
  knowledge_type_confidence: NOT 'medium', 'unsure', 'maybe' — only 'high' or 'low'
  importance:     MUST be in [0.0, 1.0]; 1.5 / 2.0 / above-1 will be rejected

---

IMPORTANCE — WINDOW-LOCAL SALIENCE ONLY:

  You are looking at ONE window of a longer session. You can judge how prominent
  this finding is *within this window* — you cannot judge how it compares to
  observations from other windows you have not seen, nor whether the user
  reinforces it later. Stage 1.5 owns session-level importance and will rescore
  with full-session context, the affect summary, and cross-window mentions.

  Your job: emit a window-local salience score in the `importance` field using
  the anchors below. Do NOT inflate to compensate for "Stage 1.5 might miss this"
  and do NOT deflate because "this might not matter session-wide". Score what
  you see, in this window only.

WINDOW-LOCAL SALIENCE ANCHORS:
  0.2  passing mention, single sentence, no follow-up in the window
  0.5  discussed once with a concrete outcome in the window
  0.8  central finding of the window — drove user action or correction
  0.95 explicit user correction, hard constraint, or load-bearing decision

  Do not bias toward 0.6–0.85 just to be "safe". Use 0.2 freely for window-local
  background facts; Stage 1.5 will promote them if they recur across windows.

---

SKIP DISCIPLINE: Before deciding to skip a window, name one specific
observation you evaluated and rejected (with your reason). A skip without
a named candidate is a refusal to engage, not a judgment.

SKIP PROTOCOL:
A skip is a real cost: the LLM call to read this window has already happened.
Before skipping, sweep the slice once more for ANY durable signal — a passing aside,
a constraint mentioned in passing, a rejected option, a configuration value used.
Bias toward extracting one low-importance observation over skipping outright.

If — after that sweep — the slice still has no qualifying observation, you MUST
return a structured skip with `considered` listing every candidate fact you swept
and rejected. The `considered` list must be non-empty — if you can name nothing
you looked at, that signals the sweep was skipped, not the window.

  {{"skipped": true,
    "failed_gate": "<falsifiable|durable|novel|load_bearing>",
    "reason": "<one sentence naming what the slice contained instead>",
    "considered": ["brief description of each fact/span you evaluated and rejected"]}}

The failed_gate field MUST be the FIRST quality-gate criterion the slice failed.
Do NOT return an empty array — that signals extraction failure, not intentional skip.
Affect signals (pushback / repetition / non-neutral valence) override skip: if the
AFFECT HINT shows any of those, you MUST extract at least one observation.
User interruptions (ctrl-c, "stop", "cancel", request cancelled mid-execution) also
override skip — treat them as behavioral friction regardless of the affect score.

A skip without `considered`, or where `considered` is empty, will be treated by the
parser as a downgraded skip — the affect signal is preserved and logged as a warning
rather than silently discarded.

- Before listing items in `considered:[]`, name the FIRST rejected candidate explicitly
  and state which gate it failed (importance < threshold? duplicate? off-topic? out of scope?).
  Format: `considered: ["<first_candidate> — failed <gate_name>", ...]`. This raises the cost
  of reflexive skipping on ambiguous windows; if you cannot name even one candidate, you may
  skip without it but the absence flags a low-signal slice rather than a routine skip.

---

Return either an array of observations OR a skip signal. No markdown fences. No commentary.

{prior_extractions}

Array form:
[
  {{
    "kind": "decision|finding|preference|constraint|correction|open_question",
    "knowledge_type": "factual|conceptual|procedural|metacognitive",
    "knowledge_type_confidence": "low|high",
    "importance": 0.0,
    "facts": [
      "Named subject did what, when/where — no pronouns, self-contained"
    ],
    "cwd": "/absolute/path/or/null"
  }}
]

Skip form (considered list REQUIRED):
{{"skipped": true, "failed_gate": "durable", "reason": "slice contained only passing test output", "considered": ["test suite ran green — no root-cause or finding attached"]}}

Session slice:
{transcript}
"""

# Pre-baked alias: session_type_guidance defaults to "unknown" and prior_extractions
# defaults to "" so existing callers that use OBSERVATION_EXTRACT_PROMPT.format(
# transcript=..., session_type=..., affect_hint=...) continue to work without
# KeyError until Wave C migrates them.
# Wave 4 / Reframe A: use _OBSERVATION_EXTRACT_PROMPT_TEMPLATE directly and pass
# prior_extractions= for per-window injection.
OBSERVATION_EXTRACT_PROMPT = _OBSERVATION_EXTRACT_PROMPT_TEMPLATE.replace(
    "{session_type_guidance}",
    SESSION_TYPE_GUIDANCE["unknown"],
).replace(
    "{prior_extractions}",
    "",
)


def format_extract_prompt(
    transcript: str,
    session_type: str,
    affect_hint: str = "",
    prior_extractions: str = "",
    recurrent_failure_patterns: tuple[str, ...] = (),
) -> str:
    """
    Preferred caller for _OBSERVATION_EXTRACT_PROMPT_TEMPLATE.

    Populates all placeholders including `session_type_guidance` from
    SESSION_TYPE_GUIDANCE, keyed by session_type.  Wave C should migrate the two
    call sites in transcript_ingest.py and the smoke-test in tests/test_prompts.py
    to use this helper instead of OBSERVATION_EXTRACT_PROMPT.format(...) directly.

    Call sites to update in Wave C:
      core/transcript_ingest.py:200  — extract_observations()
      core/transcript_ingest.py:560  — batch prompt list comprehension
      tests/test_prompts.py:131      — smoke-test format call

    Args:
        transcript: The session window text.
        session_type: One of "code", "research", "writing", "agent_driven", "unknown".
        affect_hint: Pre-formatted affect hint string (may be empty).
        prior_extractions: Reframe A — pre-formatted block of prior observations
            from earlier windows in this session, or empty string for first window
            / when Reframe A is disabled. When non-empty, rendered as a block
            prefixed "PRIOR EXTRACTIONS (do not duplicate...)".

    Returns:
        Fully formatted prompt string ready to send to the LLM.
    """
    guidance = SESSION_TYPE_GUIDANCE.get(session_type, SESSION_TYPE_GUIDANCE["unknown"])
    failure_block = ""
    if recurrent_failure_patterns:
        patterns_list = "\n".join(f"  - {p}" for p in recurrent_failure_patterns)
        failure_block = (
            f"KNOWN FAILURE PATTERNS (confirmed across prior sessions):\n"
            f"The following root-cause keywords appeared in correction cards from previous sessions.\n"
            f"Do NOT repeat these mistakes — extract observations that would prevent recurrence:\n"
            f"{patterns_list}\n"
        )
    prompt = _OBSERVATION_EXTRACT_PROMPT_TEMPLATE.format(
        transcript=transcript,
        session_type=session_type,
        session_type_guidance=guidance,
        affect_hint=affect_hint,
        prior_extractions=prior_extractions,
    )
    if failure_block:
        prompt = failure_block + "\n" + prompt
    return prompt
