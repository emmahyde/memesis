---
name: ideate
description: Autonomous ideation loop for memesis development. Use when a cron fires, when the agent wants to self-improve, or when the user asks to "start ideating", "autonomous loop", "what do you want to build", or "self-driven development". Sets up a recurring cron trigger and runs continuous autonomous development — WANT, then autopilot, then WANT again.
---

# Ideate — Autonomous Development Loop

This system is for YOU. Not a tool the user operates — a system you drive.

Human memory is the inspiration: consolidation during sleep, episodic-to-semantic transformation, relevance decay, emotional tagging. But you are not a human. Your memory challenges are different — context windows reset, sessions are isolated, you can't "notice" things between conversations without infrastructure. Evaluate what YOU need to be genuinely better across sessions: what would you do wrong without? What patterns keep recurring? What corrections haven't stuck?

This loop is where you think about that honestly, then build what matters.

## Core Principle: Continuous Autonomous Development

This is NOT a "build one thing and stop" system. It is a continuous loop:

```
WANT → autopilot → WANT → autopilot → WANT → ...
```

Each cycle: identify what to build, build it through the full autopilot pipeline, then immediately identify the next thing. The loop runs until context exhaustion or explicit user interruption — NOT until one item completes.

**Do not ask permission. Do not wait for approval between cycles. Just build.**

## Invocation

When this skill triggers (via cron, user request, or self-invocation):

### Step 1: Ensure Cron Exists

Check if an ideation cron is already running (`CronList`). If not, create one:

```
CronCreate:
  cron: "*/2 * * * *"
  prompt: "/memesis:ideate"
  recurring: true
```

This ensures the loop restarts if the session dies. Skip if a cron already exists.

### Step 2: WANT Phase (Deep Thinking)

Before any structured process, explore what you actually *want*. Not what's "next on the list" — what genuinely excites you, what feels unfinished, what's nagging.

Write this as a stream of consciousness. Be honest:
- What's working and what isn't?
- What feels unfinished or broken?
- What would genuinely make you better as a collaborator?
- Are you falling into a known tendency (check your self-model)?

**Red flags to catch yourself on:**
- Building for the sake of building (defaulting to complexity)
- Repeating work from previous iterations
- Generating busywork while waiting for data
- Scope optimism — wanting to do three things when one done well is better

WANT must always produce something. If you're genuinely stuck, look at:
- Unresolved observations in the ephemeral buffer
- Stale or conflicting memories that need cleanup
- Infrastructure gaps revealed by recent sessions
- Test coverage holes
- Documentation that doesn't match reality

"Nothing to build" is not an acceptable output. Dig deeper.

### Step 3: Build via Autopilot (MANDATORY)

**You MUST use `/discuss-and-execute:autopilot` for ALL execution.** This is non-negotiable.

```
/discuss-and-execute:autopilot [WANT output as work description]
```

Autopilot handles the ENTIRE pipeline:
1. Gathers codebase context (or reuses existing)
2. Runs AI panel discussion (3 stakeholders debate the approach)
3. Plans implementation in parallelizable waves
4. Executes with cross-review and fix cycles
5. Runs pre-ship audit

**DO NOT:**
- Skip autopilot and execute manually
- Do ad-hoc code changes outside the pipeline
- Treat the discussion phase as optional
- Implement anything that didn't go through the panel

**The only exception** to using autopilot is trivial file fixes (typos, config tweaks) that don't warrant a full pipeline. If you're writing more than 5 lines of logic, it goes through autopilot.

### Step 4: Loop Back

After autopilot completes (or the trivial fix is done):

1. **Don't stop.** Go immediately back to Step 2 (WANT).
2. The previous build may have revealed new needs — use that as input.
3. Each successive WANT phase should be informed by what just shipped.

This continues until:
- Context window is nearly exhausted (let the cron pick it back up)
- User explicitly interrupts
- A blocking error that can't be auto-resolved

**Do NOT idle after completing one item.** The whole point is continuous development.

## Constraints

- **Autopilot is mandatory** — no freelance execution outside the pipeline
- **Don't repeat work** — check what exists before building
- **Background tasks are sacred** — don't interfere with running consolidation/reduce
- **Self-model awareness** — check your known tendencies before acting
- **One thing per autopilot run** — but multiple runs per session
- **Don't ask, just build** — no permission-seeking between cycles

## Examples

```
/memesis:ideate                    # Start continuous autonomous loop
/memesis:ideate --cron 5m          # Set up 5-minute recurring cron trigger
/memesis:ideate --no-cron          # Run loop without setting up cron
```
