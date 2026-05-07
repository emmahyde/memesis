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

Ten skills are available as slash commands:

| Command                    | Purpose                                                                                 |
| -------------------------- | --------------------------------------------------------------------------------------- |
| `/memesis:learn`  | Teach the agent something explicitly. Stores a new memory with the content you provide. |
| `/memesis:recall` | Surface a specific memory by query. |
| `/memesis:forget` | Deprecate or delete a memory by ID or title match.                                      |
| `/memesis:teach`  | Multi-part structured knowledge capture (processes, architectures, workflows).         |
| `/memesis:reflect` | Trigger a self-model review — analyze behavioral patterns across sessions.             |
| `/memesis:connect` | Manually group related memories into a named narrative thread.                         |
| `/memesis:threads` | View narrative thread visualization with member memories and evolution arcs.           |
| `/memesis:stats`  | High-level memory statistics: counts by stage, importance distribution.                |
| `/memesis:health` | Memory health diagnostics: archival candidates, relevance decay, stale memories.       |
| `/memesis:usage`  | Usage analytics: injection counts, retrieval rates, most/least used memories.          |
| `/memesis:dashboard` | Combined memory system overview pulling from stats, health, and usage data.         |
| `/memesis:backfill` | Seed memory from historical Claude Code transcripts.                                  |
| `/memesis:ideate` | Autonomous ideation loop — self-driven development for memesis.                        |
| `/memesis:run-eval` | Run the memesis eval suite (live or synthetic modes).                              |

Examples:

```
/memesis:learn Always use --no-install-recommends in Dockerfiles for this project.

/memesis:recall python async patterns

/memesis:forget feedback_ruby_style

/memesis:teach Our deployment pipeline works like this: ...

/memesis:reflect

/memesis:stats
```

Memory retrieval uses three-tier progressive disclosure:
1. **Tier 1 (Instinctive)** — High-importance memories injected automatically at session start.
2. **Tier 2 (Crystallized)** — Token-budgeted context-matched memories injected via prompt-aware retrieval.
3. **Tier 3 (Agent-initiated)** — On-demand search via `/memesis:recall` with hybrid RRF (FTS5 + sqlite-vec KNN).

### Advanced Features

- **OrientingDetector** — Rule-based high-signal moment detection (corrections, emphasis, error spikes, pacing breaks)
- **Habituation Baseline** — Suppresses routine events via per-project frequency model
- **Somatic Markers** — Emotional valence classification (neutral/friction/surprise/delight) with importance bump
- **Replay Priority** — Salience-ordered observation batching for consolidation LLM
- **SM-2 Spaced Injection** — Spaced repetition scheduling prevents memory over-injection
- **Reconsolidation** — Session evidence updates injected memories (confirmations, contradictions, refinements)
- **Saturation Decay + Integration Factor** — Prevents stale isolated memories from crowding out fresh ones
- **1-Hop Graph Expansion** — Retrieval expands to thread neighbors via MemoryEdge table
- **Ghost Coherence Check** — Periodic LLM check comparing self-model claims against memory evidence

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

## MCP Server

Memesis exposes a stdio MCP server that lets Claude Code query the memory store
directly via tool calls, enabling progressive disclosure: `search_memory` for
ranked summaries, `get_memory` for full hydration, and `recent_observations` for
recency-ordered session context. The server runs locally, talks to the same
SQLite store as the hooks, and requires no authentication.

Register it in `~/.claude.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "memesis": {
      "command": "/abs/path/to/memesis/.venv/bin/memesis-mcp"
    }
  }
}
```

Replace `/abs/path/to/memesis` with the absolute path to this repository.
If using `uv` instead of a virtualenv:

```json
{
  "mcpServers": {
    "memesis": {
      "command": "/abs/path/to/uv",
      "args": ["--directory", "/abs/path/to/memesis", "run", "python", "core/mcp_server.py"]
    }
  }
}
```

Use `which uv` and `which python` to confirm binary paths — Claude Code does
not inherit your shell PATH.
