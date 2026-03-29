# Ideas

Captured during the 2026-03-29 session. Not prioritized, not committed to — just sparks.

## Eval Pipeline

- **Proportional extraction**: observation count should scale with session length. 484 messages should not produce the same output as 8 messages. (scan budget scaling landed in b6d1b21, but reduce itself may need per-session token budgets too)
- **Causal link detection**: system captures WHAT happened but not WHY. "Stale memory → nuclear purge" is two observations when it should be one with a causal chain
- **Frustration signal detection**: message compression, cold factual corrections, redirect→abandon→nuke escalation, absence of explanation AS the signal. These are the highest-value observations for collaboration quality
- **Observation tone audit**: titles like "cut-and-abandon" read judgmental. Should be descriptive and warm — "decisive about what's not worth finishing"
- **Dedup eval**: automated check for near-duplicate observations that should be merged (#7/#13, #9/#12, #1/#18)
- **Classification eval**: automated check for misclassified observation types (15 self_observations that are really workflow_patterns)
- **Run evals against real DB via flag**: `--live` flag on capture_baseline to point at real memory store instead of synthetic fixtures

## Reduce Prompt

- **Two gates are good but the collaborator gate under-fires**: most extractions are still technical workflow patterns. The human signal categories (personality, aesthetic, collaboration_dynamic) are underrepresented
- **Thinking blocks as a signal source**: included in scan now, but reduce doesn't distinguish thinking from text. Could weight thinking-block observations differently (self-corrections, uncertainty signals live there)
- **Frustration detection vocabulary**: teach reduce to recognize message shortening, factual-only corrections, interrupts, "forget this", "delete all" as frustration escalation — not just as individual events

## Retrieval

- **Consolidated memories are invisible**: 76/87 live memories are consolidated and never surface in injection. Tier 2 FTS wiring (FOUND-02) is critical
- **FTS can't handle natural language queries**: "How does the user want PRs broken down?" returns nothing because FTS5 MATCH needs term overlap. Hybrid RRF (FOUND-01) fixes this
- **Injection context is small**: 6K chars for 87 memories. Could be denser — summaries instead of full content for lower tiers

## Scan

- **Thinking blocks are included but not tagged**: reduce sees `[thinking] ...` prefix but doesn't know to treat these differently from conversational text. Could use structured markers
- **Tool output is fully stripped**: sometimes tool output contains important signal (error messages, test failures). Currently replaced with `[ran: cmd]` one-liner. Could keep stderr/failure output
- **Compact markers could segment the summary**: instead of one flat summary, could produce segments separated by compacts. Each segment is a "phase" of the session

## Memory Lifecycle

- **Cross-session observation threading**: the same pattern appearing across 5 repos in one day is a stronger signal than appearing in 5 sessions of the same repo. Cross-project reinforcement should weight higher
- **Observation decay for automated sessions**: RETIRE worktree sessions are highly repetitive. Reinforcements from automated sessions should decay faster than from interactive ones
- **Mode detection**: one-shot sessions (8 messages, single question) vs building sessions (484 messages, multi-hour) vs creative sessions (Sector game design). Different modes should produce different observation types

## Architecture (from research synthesis)

- **Retrieval is metabolic, not read-only**: every research dimension (algorithms, neuroscience, arxiv, sense memory, sci-fi) independently converged on this — retrieval should change system state. Thompson sampling updates arm distributions, reconsolidation updates memories, priming lowers gate thresholds. "Living memory"
- **Five Properties of Living Memory**: metabolic decay, association propagation, narrative coherence, identity grounding, trajectory preservation. These are the design principles, not just features
- **Encoding specificity**: memories should store multi-dimensional context at encoding time (what project, what mood, what was happening). Retrieval quality depends on matching the encoding context, not just content similarity. Deferred but high value
- **`was_used` is a heuristic, not ground truth**: FeedbackLoop's keyword matching for usage detection is a proxy. Thompson sampling and RL estimates should be weighted conservatively until we have better signal
- **3s timeout budget**: UserPromptSubmit hook has 3s total. All per-prompt retrieval must complete in ~500ms. This hard-constrains what we can do at injection time (no LLM re-ranker, no multi-hop graph traversal synchronously)

## Pipeline Economics

- **Scan compresses 287:1**: 260M raw transcript tokens → 906K summary tokens. Scan is pure Python (regex, truncation), zero LLM cost
- **Full reduce is ~$10 on Sonnet**: 724 sessions × ~2.9K input tokens + ~300 output tokens per call
- **10% sample is ~$1 and takes 3 minutes**: good enough for iteration. Full corpus for milestone validation
- **Thinking blocks add 55% to scan output**: 906K → 1.4M tokens. Still cheap. Worth it for self-correction and decision reasoning signal
- **Claude Code transcript rolling window is ~2-4 weeks**: transcripts disappear. Copy them early. We got 777 files (Feb 27 – Mar 29) representing everything available on 2026-03-29

## Observation Store Quality

- **High-frequency observations are dominated by automated sessions**: x68 "Autonomous investigation" and x56 "Unattended execution" are inflated by RETIRE worktree runs. Need frequency normalization or session-type weighting
- **Reduce prompt creates near-duplicates instead of merging**: #7/#13 and #9/#12 are the same observation with different wording. Reduce's dedup is semantic-distance based but the threshold may be too tight
- **Observation titles should be warm, not clinical**: the observations describe a person, not a system. "Cut-and-abandon" should be "decisive about what's not worth finishing"

## Long-term

- **Gold set expansion**: 6 sessions is a start. Need 20+ for statistical confidence, covering all project types and session lengths
- **LLM judge for observation quality**: after gold set establishes ground truth, could train an LLM judge to score new observations against the patterns established in the gold set
- **Observation store browser**: not a UI (out of scope) but a CLI skill that lets you browse, merge, reclassify, and prune observations interactively
- **Eval as CI gate**: run `python3 eval/report.py` in verify_phase.py after each roadmap phase. Track LongMemEval accuracy, injection rate, FTS precision over time
- **Planted-fact eval for reduce**: write synthetic transcripts with known signals, run scan → reduce, check if reduce extracts them. Fully automated, good for CI alongside the gold set
