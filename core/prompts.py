"""
Prompt templates, observation taxonomy, and privacy filter patterns.

This module defines the voice and judgment of the memory system —
how observations are structured, how consolidation decisions are made,
and what the system refuses to store.
"""

from datetime import datetime


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
}


def format_observation(text: str, obs_type: str = None, context: str = None) -> str:
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

CONSOLIDATION_PROMPT = """You are reviewing your own session notes before sleep. These observations were captured during active work. MOST SHOULD DIE. You are looking for the observations that would genuinely change how you behave in a future session. Not "interesting" — behaviorally load-bearing.

THE TEST: For each observation, ask: "If I didn't have this, would I do something wrong next time?" If the answer is no — if you'd figure it out from the code, the git history, or common sense — PRUNE IT.

SESSION OBSERVATIONS:
{ephemeral_content}

EXISTING MEMORY MANIFEST:
{manifest_summary}

KEEP ONLY IF it passes one of these gates (in priority order):
1. CORRECTIONS — You were wrong. What pattern caused it? Would you make the same mistake without this memory? Keep only if the pattern is non-obvious.
2. PREFERENCE SIGNALS — The user pushed back. But ONLY keep if the preference is surprising or counter-intuitive. "Prefers clean code" fails. "Prefers angular connectors over curved because she reads data flow direction from angles" passes.
3. SELF-OBSERVATIONS — You caught yourself doing something. ONLY keep if it's a specific, actionable pattern, not a generic tendency you'd write about any AI.
4. WORKFLOW PATTERNS — How this specific person works in ways you wouldn't guess. "Works across multiple repos" is obvious from any engineer. "Prefers to remove scaffolding immediately after migration completes rather than in a follow-up PR" has teeth.

PRUNE AGGRESSIVELY:
- Anything re-derivable from code, git log, or reading the codebase
- One-time task mechanics, tool output, file paths, commit hashes
- Generic observations true of most engineers ("moves fast", "prefers tests")
- Preferences without reasoning (the WHY is the only part worth keeping)
- Domain knowledge that lives in documentation
- Self-observations that are just "I should have done X" without identifying the underlying PATTERN
- Anything you're keeping because it's "interesting" rather than because losing it would hurt

SELECTIVITY: Let the behavioral gate decide, not an arbitrary number. A short session might have 0 keeps. A long, dense collaboration might have 10. Trust the gate — if losing it would hurt, keep it. If not, prune it. The test is quality, not quota.

PRIVACY:
- NEVER store emotional state observations (frustration, excitement, mood)

CONFLICT CHECK:
- Does any observation CONTRADICT an existing memory? If so, note which one.
- Does any observation REINFORCE an existing memory? If so, reference it by ID.

For each observation, decide:
- KEEP: Passes the "would I do something wrong without this?" gate.
- PRUNE: Fails the gate. Say why in one sentence.
- PROMOTE: Reinforces an existing memory. Specify the memory_id from the manifest.

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "decisions": [
    {{
      "observation": "the raw observation text",
      "action": "keep|prune|promote",
      "rationale": "why this decision — be honest with yourself",
      "title": "short title (keep only)",
      "summary": "~150 char summary (keep only)",
      "tags": ["tag1", "tag2"],
      "target_path": "category/filename.md (keep only)",
      "observation_type": "correction|preference_signal|shared_insight|domain_knowledge|workflow_pattern|self_observation|decision_context|null",
      "reinforces": "memory_id or null",
      "contradicts": "memory_id or null"
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
# Privacy filter patterns
# ---------------------------------------------------------------------------

EMOTIONAL_STATE_PATTERNS = [
    r'\b(seemed?|appears?|looks?|sounds?)\s+(frustrated|excited|angry|upset|happy|sad|annoyed|pleased|disappointed|confused|stressed)',
    r'\b(was|is|feels?)\s+(frustrated|excited|angry|upset|happy|sad|annoyed|pleased|disappointed|confused|stressed)',
    r'\b(mood|emotion|feeling)\b',
    r'\b(emma|user)\s+(seemed?|was|felt?)\s+\w+ly?\b',
]
