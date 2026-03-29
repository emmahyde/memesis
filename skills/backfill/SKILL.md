---
name: backfill
description: This skill should be used when the user asks to "backfill memories", "seed from history", "populate from transcripts", "analyze past sessions", "import conversation history", or wants to bootstrap the memory system from existing Claude Code transcripts for a time period. Also use autonomously when the memory store is empty or sparse and historical transcripts are available — the agent can self-invoke this to bootstrap its own knowledge base without being asked.
---

# Backfill — Populate Memory from Conversation History

Scan Claude Code conversation transcripts for a time window, extract durable observations through the consolidation engine, and seed them into the memory store.

## Invocation

When this skill triggers, prompt the user for two inputs before running anything:

1. **Time window** (required): "How far back should I scan? (e.g. 30d, 2w, 3m)"
2. **Focus area** (optional): "Is there a specific topic to focus on? (e.g. 'testing patterns', 'how you prefer to communicate', 'architecture decisions') — or leave blank for general extraction. Note: focus injection is experimental and hasn't been formally evaluated against the base prompt yet."

Only proceed after the user responds. Do not assume defaults.

## Usage (after gathering inputs)

```
/memesis:backfill 30d
/memesis:backfill 2w --focus "testing patterns and preferences"
/memesis:backfill 7d --project app --limit 20
```

## Pipeline

Three scripts run in sequence. Each produces output consumed by the next.

### Step 1: Scan

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py <duration> [--project NAME] [--limit N] [--min-size KB]
```

Reads JSONL transcripts from `~/.claude/projects/*/`, extracts user/assistant text (stripping tool output, system messages, large code blocks), and writes session summaries to `backfill-output/summaries.jsonl`.

Duration formats: `30d` (days), `2w` (weeks), `4h` (hours), `6m` (months).

### Step 2: Consolidate

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/consolidate.py [--limit N] [--focus "TOPIC"]
```

Feeds each summary through the consolidation prompt via the Anthropic API. Writes keep/prune decisions to `backfill-output/consolidation-results.jsonl`.

The `--focus` flag injects guidance into the prompt to bias extraction toward a specific topic without overriding the keep/prune criteria. Examples:

- `--focus "how Emma communicates and makes decisions"`
- `--focus "testing patterns, CI/CD workflows, and test isolation"`
- `--focus "architecture decisions and their constraints"`
- `--focus "corrections and mistakes — what went wrong and why"`

### Step 3: Seed

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seed.py [--project-context PATH] [--dry-run] [--report]
```

Reads kept observations from consolidation results and creates consolidated memories in the store. Deduplicates by content hash — safe to run multiple times.

Use `--report` to see a quality breakdown before seeding. Use `--dry-run` to preview without writing.

## Procedure

When the user invokes `/memesis:backfill <duration>`, run the three scripts in sequence:

1. Parse duration and any flags from the user's input
2. Run `scan.py` with the duration and flags
3. Run `consolidate.py` (pass `--focus` if the user specified a topic)
4. Run `seed.py --report` first to show results, then `seed.py` to commit

If the user only wants to see what would be extracted, run steps 1-2 and then `seed.py --dry-run`.

## Examples

```
/memesis:backfill 30d
/memesis:backfill 2w --focus "how this user prefers to work with AI agents"
/memesis:backfill 7d --project app
```
