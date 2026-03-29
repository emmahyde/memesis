# Transcript Analysis: Design & Future Direction

## The Problem

The current observation pipeline has a signal-to-noise problem:

**Stop hooks fire per-turn.** Every conversation turn generates an observation
opportunity. This biases toward *frequency* — things that happen a lot get
observed a lot. "Cron triggers are useful" surfaces repeatedly because crons
fire repeatedly, not because it's a profound insight. The consolidation engine
then has to wade through noise to find signal.

**What we actually want:** Observations that reflect what *mattered* across a
conversation, not what *happened a lot*. A single correction that changed how
we approach a problem is worth more than fifty routine tool calls.

## The Architecture Shift

### Current: Per-Turn Observation (Stop Hook)

```
Every agent response → Stop hook → observation → ephemeral buffer
                                    ↓
                            PreCompact → consolidate
```

Problem: high volume, low signal density. The consolidation prompt has to be
very aggressive about pruning, and the pruning itself costs LLM tokens.

### Proposed: Periodic Transcript Analysis (Cron)

```
Hourly cron → scan recent transcripts → extract takeaways → ephemeral buffer
                                                               ↓
                                                      PreCompact → consolidate
```

Advantages:
- **Natural frequency dampening.** Analyzing a full conversation once
  inherently de-emphasizes repetitive interactions.
- **Broader context.** The analyzer sees the full arc of a session, not
  individual turns. "User pushed back on approach A, then we tried B and C,
  and settled on B because of constraint X" is visible at session level but
  invisible at turn level.
- **Lower cost.** One LLM call per session vs. one per turn.
- **No hook latency.** Stop hooks add latency to every response.

### Hybrid Approach (Eventual)

Keep the Stop hook for high-signal events only (corrections detected via
explicit language), use the hourly cron for everything else. The Stop hook
becomes a "fast path" for obvious signals, while the cron does the deep
analysis.

## Phase 1: Backtest with Existing Data

Before building the hourly cron, validate the approach by backtesting against
existing transcript data.

### Data Available

- **775 sessions** across **25 projects**, totaling **626MB** of JSONL transcripts
- Primary projects: `work/app` (110 sessions), `work/ai-tools` (94 sessions)
- Date range: ~2026-01-08 to 2026-03-28
- Format: JSONL with `type` field (`user`, `assistant`, `system`, `progress`)

### What the Backtest Does

1. **Scan transcripts** — read JSONL files, extract user/assistant message pairs
2. **Summarize sessions** — compress each session to its key interactions
   (corrections, decisions, preferences, pushback moments)
3. **Run through consolidation** — feed summaries through the existing
   consolidation prompt to see what gets kept vs. pruned
4. **Measure output quality** — how many of the kept observations are genuinely
   useful? How many are noise? What types dominate?
5. **Compare against existing memories** — do the backtest outputs overlap with
   memories already in the native Claude Code system?

### What We Learn

- **Consolidation prompt calibration**: Is the 15% keep rate right? Too aggressive? Too permissive?
- **Observation type distribution**: What types of observations actually emerge from real conversations?
- **Signal patterns**: What conversational patterns reliably produce high-value observations?
- **Noise patterns**: What patterns produce observations that *look* useful but aren't?
- **Frequency bias measurement**: How much does per-session analysis reduce frequency bias compared to per-turn?

## Phase 2: Hourly Transcript Cron

Based on backtest results, build the production pipeline:

1. **Cron fires hourly** (or configurable interval)
2. **Scan for new/modified transcripts** since last run (track via checkpoint file)
3. **Extract conversation summaries** — just the user/assistant text, stripped
   of tool output, code listings, and system messages
4. **Run consolidation** — same prompt, but operating on session-level summaries
   instead of per-turn observations
5. **Deduplicate** — check against existing memories before creating new ones
6. **Write to ephemeral buffer** — enters the normal lifecycle

### Key Design Decisions (TBD based on backtest)

- **Summarization model**: Use the same model as consolidation, or a cheaper one for summarization?
- **Session granularity**: One summary per session? Or group by project/day?
- **Token budget per session**: How much of a transcript to send for analysis?
- **Incremental vs. batch**: Process new sessions incrementally, or re-analyze recent sessions periodically?

## Phase 3: Deprecate Stop Hook (Maybe)

If the hourly cron produces better observations with less noise, the Stop hook
becomes optional. Keep it as a "fast path" for high-confidence signals
(explicit corrections, user saying "remember this"), remove it as the default
observation mechanism.

## Non-Goals

- Real-time analysis (the hourly cadence is intentional — let sessions complete)
- Full transcript storage in memory (we extract takeaways, not archive conversations)
- Cross-user analysis (this is personal memory, not team knowledge)
