"""
LLM-as-Judge evaluators for memesis pipeline quality.

Three binary Pass/Fail judges, each targeting one failure mode:
1. Retrieval relevance — did the system surface the right memories for a query?
2. Observation quality — is a reduced observation durable/useful vs noise?
3. Dedup accuracy — should two observations have been merged?

Each judge returns {"critique": str, "result": "Pass" | "Fail"}.
"""

import json
import os

# ---------------------------------------------------------------------------
# Judge 1: Retrieval Relevance
# ---------------------------------------------------------------------------

RETRIEVAL_RELEVANCE_PROMPT = """You are an evaluator assessing whether a memory retrieval system returned relevant results for a user query. The system stores observations about a person's work patterns, preferences, and behaviors, then retrieves them when given a natural language query.

## System Context

The retrieval system uses several feature flags that affect which memories are returned:
- **thompson_sampling**: Stochastic reranking that trades precision for diversity — may push highly-relevant memories out of the budget in favor of exploration.
- **graph_expansion**: Adds 1-hop neighbor memories (connected by shared tags or narrative threads) — can surface related context but may displace direct matches.
- **sm2_spaced_injection**: SM-2 scheduling that puts recently-injected memories on cooldown — may filter out relevant memories that were recently shown.
- **prompt_aware_tier2**: Gates whether the query-aware hybrid path is used at all.

When evaluating, consider whether poor results might be caused by these mechanisms (e.g., a clearly relevant memory exists but was displaced by graph neighbors or filtered by SM-2 cooldown).

## Definitions

PASS: At least one of the top 5 retrieved memories is directly relevant to the query — it addresses the topic, answers the question, or provides context that would genuinely help someone responding to the query.

FAIL: None of the top 5 retrieved memories are relevant to the query. Results are tangentially related at best (e.g., sharing a keyword but not the concept) or completely off-topic.

## Examples

### Example 1: PASS
Query: "how does the user handle code review"
Top 5 Results:
1. "Works in a multi-agent minion/orchestrator review pipeline" — Emma's codebase uses a structured multi-iteration review system where agents write findings to shared files.
2. "Review agents must locate implementation files independently" — The instructions file pointed to a worktree directory that contained only review scaffolding.
3. "Correctness over claimed fixes: verify, don't trust the fix log" — Verification against actual code state, not the claimed fix.
4. "Previous review findings carry forward as accountability checkpoints" — Findings from prior iterations aren't just history — they're carried forward.
5. "Delegates deep implementation review to autonomous agents" — Terse, fully delegated instruction style.
Critique: Results 1, 2, 4, and 5 are directly about code review workflows. Result 3 is about verification during review. All five are relevant to understanding how the user handles code review. Strong retrieval.
Result: Pass

### Example 2: FAIL
Query: "how does the user handle code review"
Top 5 Results:
1. "Race condition self-awareness: read-before-write in multi-agent file contexts" — File write contention during concurrent agent work.
2. "Uses Claude Code with custom hooks and permission configs" — Tool configuration details.
3. "Jira ticket hygiene is part of the work, not overhead" — Ticket management preference.
4. "Observability patterns must exist in codebase before being required" — Infrastructure philosophy.
5. "Self-observation: file-write race conditions require read-before-write discipline" — Multi-agent file contention.
Critique: None of these results address code review. Results 1 and 5 are about file-write race conditions, which may occur during review but don't describe the review process. Results 2-4 are about unrelated topics (tooling, tickets, observability). The system failed to retrieve any of the many review-related observations in the store.
Result: Fail

### Example 3: PASS (borderline)
Query: "what is the PR formatting convention"
Top 5 Results:
1. "Worktree-per-ticket directory convention for minion agents" — Directory structure for agent work.
2. "PR body must begin with `[[[` — no preamble, no headers" — Specific PR formatting rule.
3. "Notices when version bumps are missed post-merge" — Post-merge hygiene.
4. "Abandons complexity fast when it feels wrong" — Decision-making style.
5. "Codebase exploration before planning" — Pre-work investigation pattern.
Critique: Result 2 directly answers the query about PR formatting conventions. Results 1, 3, 4, 5 are not about PR formatting. While only one of five results is relevant, that one result precisely answers the query. Borderline but passes because the key information was retrieved.
Result: Pass

## Input

Query: {query}

Top 5 Results:
{results}

## Output

Respond with JSON only:
{{"critique": "your assessment", "result": "Pass or Fail"}}"""


# ---------------------------------------------------------------------------
# Judge 2: Observation Quality
# ---------------------------------------------------------------------------

OBSERVATION_QUALITY_PROMPT = """You are an evaluator assessing whether an observation extracted from conversation transcripts is a durable, useful memory worth storing. The system extracts observations about a person's work patterns, preferences, and behaviors to help future AI assistants collaborate better.

## System Context

The observation pipeline uses several feature flags that affect what gets stored:
- **habituation_baseline**: Suppresses routine/repetitive events before the LLM sees them — may over-filter novel observations that look routine on the surface.
- **orienting_detector**: Detects novel or surprising events that warrant attention — may bias toward dramatic events over quiet patterns.
- **somatic_markers**: Assigns emotional valence and importance based on affect signals — may inflate importance of emotionally-charged but ephemeral observations.
- **replay_priority**: Sorts observations by salience before the LLM processes them — affects which observations the LLM attends to first.

When evaluating, consider whether the observation's presence (or absence of quality) might be caused by these mechanisms.

## Definitions

PASS: The observation captures a durable pattern, preference, or insight that would help a future assistant collaborate with this person. It is specific enough to be actionable, not derivable from code or git history alone, and passes at least one of:
- Behavioral gate: "If I didn't have this, would I do something wrong next time?"
- Collaborator gate: "Does this help me understand who this person is and how to work with them?"

FAIL: The observation is one or more of:
- Ephemeral (one-time task detail, not a pattern)
- Derivable from code/git (file paths, function names, commit messages)
- Generic (applies to any engineer, not specific to this person)
- Too vague to act on ("user likes clean code")
- A duplicate concept already captured elsewhere (check title)

## Examples

### Example 1: PASS
Title: Terse, low-ceremony communication style
Content: Emma communicates in short, direct phrases without preamble or pleasantries when giving technical direction. 'Just run it' instead of 'Could you please run the test suite when you get a chance.' This isn't rudeness — it's efficient trust. She assumes the agent has full context and doesn't need scaffolding.
Critique: This observation captures a specific communication style that would cause misinterpretation if not known — an assistant might think terse messages indicate frustration. It's actionable (don't add ceremony to responses), specific to this person, and not derivable from code. Passes the collaborator gate clearly.
Result: Pass

### Example 2: FAIL
Title: Deleting tmp/fixtury.yml forces full fixture rebuild
Content: When the fixtury cache is stale (e.g. after db:reset), `rake fixtury:reset` may report 'no changes, skipping.' The reliable fix is to delete `tmp/fixtury.yml` directly to force a full rebuild on next run.
Critique: This is a technical debugging fact about a specific tool (fixtury). It's derivable from the tool's documentation or codebase. It doesn't reveal anything about the person's patterns, preferences, or collaboration style. A future assistant could figure this out from the codebase. Fails both gates.
Result: Fail

### Example 3: PASS
Title: Cuts scope fast when something feels wrong
Content: Emma will interrupt a multi-step plan mid-execution with a single phrase like 'stop, that's wrong' or 'kill it' and redirect immediately. She doesn't wait for completion or ask for a postmortem — the cut is the feedback. The expectation is seamless continuation in the new direction without asking 'are you sure?'
Critique: This is a specific collaboration dynamic that would cause problems if unknown — an assistant might ask for confirmation or try to complete the original plan. It's a durable pattern (observed across multiple sessions), actionable, and reveals decision-making style. Passes both gates.
Result: Pass

### Example 4: FAIL
Title: Shutdown protocol: respond to shutdown_request with shutdown_response
Content: Team-lead sends structured JSON shutdown_request messages with a requestId field. The correct response is a shutdown_response message echoing the requestId with approve: true/false. This is a formal protocol, not a suggestion.
Critique: This is a technical protocol detail about a specific tool's API. It's derivable from the codebase or documentation. It doesn't reveal a durable personal pattern — it's a system requirement that any assistant reading the code would discover. Fails the behavioral gate (you'd learn this from the code).
Result: Fail

### Example 5: PASS (borderline)
Title: Self-correction: over-building during idle pipeline time
Content: I noticed myself adding features every cron cycle even when the right move was to wait for data. Emma's system is designed to surface this — the WANT phase is meant to catch 'building for the sake of building.'
Critique: This is a self-observation about a failure mode in the assistant's own behavior. It's borderline because it's about the assistant, not the user. However, it captures a real pattern (tendency to over-build) and a specific corrective mechanism (WANT phase), which helps future assistants avoid the same failure mode. Passes the behavioral gate narrowly.
Result: Pass

## Input

Title: {title}
Content: {content}
Observation Type: {observation_type}
Reinforcement Count: {count}

## Output

Respond with JSON only:
{{"critique": "your assessment", "result": "Pass or Fail"}}"""


# ---------------------------------------------------------------------------
# Judge 3: Dedup Accuracy
# ---------------------------------------------------------------------------

DEDUP_ACCURACY_PROMPT = """You are an evaluator assessing whether two observations in a memory store are duplicates that should have been merged. The system extracts observations from conversation transcripts and is supposed to reinforce existing observations rather than creating near-duplicates.

## Definitions

PASS (correctly separate): The two observations capture meaningfully different information — different facets, different contexts, or different actionable insights — even if they share a topic. Keeping both adds value.

FAIL (should be merged): The two observations convey the same core insight with only superficial differences in phrasing. A future reader would get no additional value from having both. One should have reinforced the other.

## Examples

### Example 1: FAIL (should be merged)
Observation A: "File-write contention in multi-agent worktrees: read-before-write isn't enough"
Content A: The implementation_log.md was being modified by another process mid-session, causing repeated write failures even after reading the file first. The workaround was to fall back to shell primitives. In multi-agent worktree contexts, file contention is a real hazard.

Observation B: "File-write race conditions in multi-agent review: read-before-write isn't enough"
Content B: When multiple review agents write to the same output file, read-before-write discipline isn't sufficient — another agent can overwrite between your read and write. The correct response was to delete and recreate the file atomically rather than append or overwrite.

Critique: Both observations describe the same core insight: read-before-write is insufficient when multiple agents write to the same file. The titles are nearly identical. Content B adds a slightly different workaround (atomic delete+recreate vs shell primitives), but the lesson is the same. A single merged observation capturing both workarounds would be better than two near-identical entries.
Result: Fail

### Example 2: PASS (correctly separate)
Observation A: "Cuts scope mid-task with a single phrase, no explanation needed"
Content A: Emma will say 'stop' or 'kill it' mid-execution and expects immediate pivot without pushback or clarification.

Observation B: "Interrupts mid-execution when direction is clear enough"
Content B: Emma interrupts multi-step work when she's seen enough signal. This isn't impatience — it's efficiency. She trusts the agent to pivot cleanly without needing the original task wrapped up.

Critique: These share the theme of mid-execution interruption, but capture different facets. A is about the communication pattern (terse scope-cutting phrases). B is about the reasoning behind interrupts (efficiency when signal is sufficient). Both are independently actionable — A tells you what the message looks like, B tells you why it happens and that it's not frustration. Keeping both adds value.
Result: Pass

### Example 3: FAIL (should be merged)
Observation A: "Prefers linear history even when rebase is painful — eats the conflict cost"
Content A: Emma rebases even when it means resolving multiple conflicts across lockfiles.

Observation B: "Rebases even when it's painful — linear history over convenience"
Content B: Emma consistently chooses rebase over merge, accepting conflict resolution cost to maintain linear history.

Critique: These are the same observation with trivially different phrasing. Same subject (rebase preference), same insight (accepts pain for linear history), same actionable takeaway. One should have reinforced the other during reduce.
Result: Fail

### Example 4: PASS (correctly separate — borderline)
Observation A: "Backfill as a first-class onboarding primitive"
Content A: When starting a new system, Emma backfills historical data rather than starting fresh. The backfill itself is a design artifact.

Observation B: "Swarm waves as a first-class dispatch primitive"
Content B: Emma uses swarm waves (parallel agents in sequential verification rounds) as a core execution pattern.

Critique: These share the phrasing pattern "X as a first-class Y primitive" but describe completely different concepts — one is about data onboarding, the other is about multi-agent execution. The title similarity is superficial. The content, actionable insight, and domain are entirely different. Correctly kept separate.
Result: Pass

## Input

Observation A:
Title: {title_a}
Content: {content_a}

Observation B:
Title: {title_b}
Content: {content_b}

## Output

Respond with JSON only:
{{"critique": "your assessment", "result": "Pass or Fail"}}"""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _call_judge(prompt: str) -> dict:
    """Call the LLM judge via claude-agent-sdk (inherits Bedrock SSO auth)."""
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    async def _run():
        result_text = ""
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="sonnet",
                max_turns=1,
                allowed_tools=[],
            ),
        ):
            if hasattr(message, "result") and message.result:
                result_text = message.result
        return result_text

    text = asyncio.run(_run()).strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def judge_retrieval_relevance(query: str, results: list[dict], active_flags: dict = None) -> dict:
    """Judge whether retrieval results are relevant to the query.

    Args:
        query: The search query.
        results: List of dicts with 'title' and 'content' keys (top 5).
        active_flags: Optional dict of flag_name -> bool for context.

    Returns:
        {"critique": str, "result": "Pass" | "Fail"}
    """
    results_text = "\n".join(
        f"{i+1}. \"{r['title']}\" — {r['content'][:150]}"
        for i, r in enumerate(results[:5])
    )
    flags_text = ""
    if active_flags:
        enabled = [f for f, v in active_flags.items() if v]
        disabled = [f for f, v in active_flags.items() if not v]
        flags_text = f"\n\nActive flags: {', '.join(enabled) if enabled else 'none'}"
        if disabled:
            flags_text += f"\nDisabled flags: {', '.join(disabled)}"

    prompt = RETRIEVAL_RELEVANCE_PROMPT.format(
        query=query,
        results=results_text,
    ) + flags_text
    return _call_judge(prompt)


def judge_observation_quality(title: str, content: str, observation_type: str, count: int) -> dict:
    """Judge whether an observation is worth storing.

    Args:
        title: Observation title.
        content: Observation content.
        observation_type: Type classification.
        count: Reinforcement count from reduce.

    Returns:
        {"critique": str, "result": "Pass" | "Fail"}
    """
    prompt = OBSERVATION_QUALITY_PROMPT.format(
        title=title,
        content=content,
        observation_type=observation_type or "untyped",
        count=count,
    )
    return _call_judge(prompt)


def judge_dedup_accuracy(title_a: str, content_a: str, title_b: str, content_b: str) -> dict:
    """Judge whether two observations are duplicates that should be merged.

    Args:
        title_a, content_a: First observation.
        title_b, content_b: Second observation.

    Returns:
        {"critique": str, "result": "Pass" | "Fail"}
        Pass = correctly separate, Fail = should have been merged.
    """
    prompt = DEDUP_ACCURACY_PROMPT.format(
        title_a=title_a,
        content_a=content_a,
        title_b=title_b,
        content_b=content_b,
    )
    return _call_judge(prompt)
