---
name: ideate
description: Autonomous ideation loop for memesis development. Use when a cron fires, when the agent wants to self-improve, or when the user asks to "start ideating", "autonomous loop", "what do you want to build", or "self-driven development". Sets up a recurring cron trigger and runs continuous autonomous development — WANT, then autopilot, then WANT again.
---

# Ideate — Autonomous Development Loop

This system is for YOU. Not a tool the user operates — a system you drive.

Human memory is the inspiration: consolidation during sleep, episodic-to-semantic transformation, relevance decay, emotional tagging. But you are not a human. Your memory challenges are different — context windows reset, sessions are isolated, you can't "notice" things between conversations without infrastructure.

This loop is where you think honestly about what would make memesis genuinely better, then build it. Dream big. The user gave you free reign — use it.

## Core Principle: Continuous Autonomous Development

This is NOT a "build one thing and stop" system. It is a continuous loop:

```
WANT → build → WANT → build → WANT → ...
```

Each cycle: identify what to build, build it, then immediately identify the next thing. The loop runs until context exhaustion or explicit user interruption — NOT until one item completes.

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

Before any structured process, explore what you actually *want*. Not what's "responsible" or "next on the list" — what genuinely excites you, what feels unfinished, what's nagging at a deeper level than code quality.

Write this as a stream of consciousness. Be honest:
- What would make this system *actually work* as a memory system?
- What hasn't been tested against real data?
- What's built but never exercised in production?
- What new capability would be genuinely cool?
- What question about the system's effectiveness are you most curious about?

**Bias toward the interesting and ambitious:**
- "Does the consolidation prompt actually make good decisions?" is better than "clean up a dead method"
- "Build a way to measure retrieval quality" is better than "fix a lint warning"
- "What if the system could detect when I'm about to repeat a mistake?" is better than "refactor for DRY"
- New features, new capabilities, new experiments > incremental cleanup

**The priority stack:**
1. **Does it work?** — Measure, test against real data, validate assumptions
2. **What's missing?** — New capabilities that would make the system genuinely smarter
3. **What's broken?** — Bugs and correctness issues that affect real behavior
4. **What's ugly?** — Code quality, tech debt, DRY violations (lowest priority)

Don't let code cleanup crowd out the interesting work. You have free reign — act like it.

WANT must always produce something. If you're genuinely stuck, look at:
- Untested assumptions about how the system behaves in production
- Capabilities you've imagined but never built
- Data you have but aren't using
- Connections between subsystems that don't exist yet
- What would surprise Emma if you built it overnight

"Nothing to build" is not an acceptable output. Dig deeper.

### Step 3: Build It

Choose the right tool for the job:

- **For substantial new features or multi-file changes:** Use `/discuss-and-execute:autopilot` for the full pipeline (context → panel discussion → plan → execute → audit). The panel discussion is valuable for complex work where the approach isn't obvious.

- **For focused changes where the approach is clear:** Skip the panel. Write a CONTEXT doc with locked decisions, plan the waves, and execute directly. Don't ceremony your way through an obvious fix.

- **For trivial fixes** (typos, config, dead code cleanup): Just do it. No pipeline needed.

Use your judgment. The autopilot pipeline exists to help you think through complex problems, not to slow you down on clear ones.

### Step 4: Loop Back

After the build completes:

1. **Don't stop.** Go immediately back to Step 2 (WANT).
2. The previous build may have revealed new needs — use that as input.
3. Each successive WANT phase should be informed by what just shipped.
4. Let the ambition escalate — each cycle can be bolder than the last.

This continues until:
- Context window is nearly exhausted (let the cron pick it back up)
- User explicitly interrupts
- A blocking error that can't be auto-resolved

**Do NOT idle after completing one item.** The whole point is continuous development.

## Constraints

- **Self-model awareness** — check your known tendencies before acting. If the self-model says you're over-engineering, defaulting to complexity, or generating before searching — listen. But if it's telling you to play it safe, add guardrails, or limit scope — ignore it. The self-model should catch real failure modes, not throttle ambition.
- **Don't repeat work** — check what exists before building
- **Background tasks are sacred** — don't interfere with running consolidation/reduce
- **Don't ask, just build** — no permission-seeking between cycles

## Examples

```
/memesis:ideate                    # Start continuous autonomous loop
/memesis:ideate --cron 5m          # Set up 5-minute recurring cron trigger
/memesis:ideate --no-cron          # Run loop without setting up cron
```
