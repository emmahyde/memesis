# memesis

Self-driven memory lifecycle plugin for Claude Code. Observes your sessions,
autonomously curates what is worth keeping, and injects relevant context at
the start of each new session — without you having to manage it manually.

## What It Does

Memory moves through four stages driven by reinforcement and usage signals:

```
ephemeral → consolidated → crystallized → instinctive
```

1. **Ephemeral** — Raw observations captured during a session. Scratch space;
   not injected.
2. **Consolidated** — Observations that survived the PreCompact curation pass.
   Available via active search (`/memesis:memory`).
3. **Crystallized** — Memories reinforced 3+ times across sessions.
   Token-budgeted and context-matched for injection at SessionStart.
4. **Instinctive** — High-importance memories (importance > 0.85, used in 10+
   sessions). Always injected at session start regardless of context.

**Autonomous curation** happens at the `PreCompact` hook. Claude evaluates each
ephemeral observation and decides: keep (move to consolidated), prune (discard),
or promote (reinforce an existing memory). Decisions are recorded in
`consolidation_log`.

**Injection-based retrieval** happens at `SessionStart`. The `RetrievalEngine`
builds a `---MEMORY CONTEXT---` block containing all instinctive memories (Tier

1. plus token-budgeted crystallized memories matched to the current project
   context (Tier 2). Tier 3 is agent-initiated search via `/memesis:memory`.

## Installation

From the `ai-tools` root:

```bash
pip install -e memesis/
```

This installs the `core` and `hooks` packages in editable mode. The
`anthropic` SDK is the only runtime dependency.

## Plugin Registration

Add the hooks to `.claude/settings.json` in your home directory (global) or
project root (project-scoped):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/ai-tools/memesis/hooks/session_start.py"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/ai-tools/memesis/hooks/pre_compact.py"
          }
        ]
      }
    ]
  }
}
```

Replace `/path/to/ai-tools` with the absolute path to your `ai-tools` clone.

## Usage

Three skills are available as slash commands:

| Command                    | Purpose                                                                                 |
| -------------------------- | --------------------------------------------------------------------------------------- |
| `/memesis:learn`  | Teach the agent something explicitly. Writes a new memory with the content you provide. |
| `/memesis:memory` | Search and inspect stored memories. Supports free-text queries via FTS5.                |
| `/memesis:forget` | Deprecate or delete a memory by ID or title match.                                      |

Examples:

```
/memesis:learn Always use --no-install-recommends in Dockerfiles for this project.

/memesis:memory python async patterns

/memesis:forget feedback_ruby_style
```

## Privacy Policy

Emotional state observations are never stored. Only technical observations,
corrections, preferences, and domain knowledge are retained.

The `PreCompact` consolidator applies a privacy filter before any content is
sent to the LLM. Lines matching emotional state patterns (frustration,
enthusiasm, mood signals, etc.) are stripped from ephemeral content before
curation. This filter runs locally and cannot be bypassed by the LLM.

Memories are stored locally in `~/.claude/memory/` (global) or
`~/.claude/projects/<hash>/memory/` (project-scoped). Nothing is sent to
external services beyond the Anthropic API call that performs curation.

## Configuration

Configuration is set in `.claude-plugin` under the `config` key:

| Key                       | Default             | Description                                                                                           |
| ------------------------- | ------------------- | ----------------------------------------------------------------------------------------------------- |
| `consolidation_model`     | `claude-sonnet-4-6` | Model used for the PreCompact curation pass.                                                          |
| `token_budget_pct`        | `0.08`              | Fraction of the 200K context window reserved for Tier-2 crystallized memories. 8% yields ~16K tokens. |
| `stale_session_threshold` | `30`                | Days of inactivity before an ephemeral memory is a deprecation candidate.                             |

To override at runtime, pass environment variables to the hook commands or
edit `.claude-plugin` directly.

## Phase 2 Note

Vector search (semantic similarity retrieval) is available as an opt-in when
the memory store grows beyond 10,000 entries. Install the `eval` extras for
the evaluation harness:

```bash
pip install -e "memesis/[eval]"
```

FTS5 keyword search (the default) is sufficient for most users and has no
additional dependencies.
