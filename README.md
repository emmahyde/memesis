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
| `/memesis:retrieval` | Retrieval analytics: injection counts, hit rates, most/least retrieved memories.    |
| `/memesis:dashboard` | Combined memory system overview pulling from stats, health, and retrieval data.     |
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

## Transcript Sweep (Cron)

The transcript sweep ingests new content from Claude Code session transcripts
and extracts durable observations into the ephemeral buffer for downstream
consolidation. It runs as a standalone process — no hook involvement.

Entry point: `core/transcript_ingest.py` (orchestrator) — thin wrapper:
`scripts/transcript_cron.py`.

Run without arguments to sweep **all projects** under
`~/.claude/projects/*/*.jsonl`:

```bash
# Default: all projects, every 15 min via crontab
*/15 * * * * uv run --directory /path/to/memesis python scripts/transcript_cron.py
```

Use `--project` to sweep transcripts from **one specific folder** — useful
for an hourly sweep of a single project:

```bash
# Single project — hourly
0 * * * * uv run --directory /path/to/memesis python scripts/transcript_cron.py \
    --project ~/.claude/projects/-Users-emmahyde-projects-sector
```

### Flags

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--project PATH` | *(all projects)* | Only scan transcripts from this folder (`{PATH}/*.jsonl`). |
| `--max-sessions N` | *(unlimited)* | Cap the number of sessions processed per tick. Newest sessions first. |
| `--dry-run` | *(off)* | Print observations without writing anything (cursor, ephemeral, DB). |

### Technical Architecture

#### Pipeline (single tick)

Each invocation of `tick()` (defined at `core/transcript_ingest.py:1220`):

1. **Discovery** — `discover_transcripts()` globs JSONL files modified
   within the last 25 hours. When `--project` is set, scopes the glob to
   `{project_path}/*.jsonl` instead of `~/.claude/projects/*/*.jsonl`.
   Sorted by mtime descending so `--max-sessions N` processes the N
   most-recently-active sessions.

2. **Cursor check** — Each session has a byte-offset cursor in SQLite.
   If no cursor exists (new session): seed it at EOF and skip
   (extraction begins next tick). If the on-disk path changed (rotation):
   reset cursor to EOF. See [CursorStore DB](#cursorstore-db) below.

3. **Delta read** — `read_transcript_from()` opens the JSONL at the cursor
   offset, reads all new entries, and returns `(entries, new_offset, cwd)`.
   The cwd is detected from `attachment` entries carrying the working
   directory field.

4. **Session type detection** — `detect_session_type()` at
   `core/session_detector.py:182` classifies the session as `code`,
   `writing`, `research`, or `unknown` using two heuristics:
   - **Path-based**: substrings in cwd (e.g. `/manuscript/` → `writing`,
     `/.claude-mem` → `research`, `/projects/sector` → `code`).
   - **Tool-mix**: ratio of Edit/Write/Bash calls to WebFetch/Read calls,
     combined with file extension patterns. Research requires web tools
     + `.md` reads; writing requires prose extensions without code files.

5. **LLM extraction** — `extract_observations()` (line 216) calls the LLM
   with `format_extract_prompt()` (system prompt: `extraction`). The LLM
   returns a JSON array of observations. Each observation carries
   `content`, `importance` [0, 1], `kind` (decision, finding, preference,
   constraint, correction, open_question), and `knowledge_type`.
   Observations below `importance < 0.3` are dropped. The LLM may also
   return `{"skipped": true, "reason": "..."}` to intentionally skip
   low-signal windows.

6. **Append to ephemeral** — `append_to_ephemeral()` formats each
   observation as a tagged line (`[observation_type] content`) and
   appends to `<global>/ephemeral/<project_slug>/<date>.md`. The write is
   protected by `fcntl.flock` on a `.lock` file in the target directory.

7. **Self-reflection** — After extraction, `reflect_on_extraction()`
   evaluates heuristic rules over run stats (parse errors, productive
   windows, knowledge-type variety). Confirmed rules update
   `self_model.md` and feed back into the next tick's `ParameterOverrides`
   via the rule registry (`core/rule_registry.py`).

#### CursorStore DB

File: `~/.claude/memesis/cursors.db`

```sql
CREATE TABLE IF NOT EXISTS transcript_cursors (
  session_id       TEXT PRIMARY KEY,    -- UUID stem of the JSONL file
  transcript_path  TEXT NOT NULL,        -- absolute path to the JSONL
  last_byte_offset INTEGER NOT NULL DEFAULT 0,  -- resume point
  first_seen_at    INTEGER NOT NULL,    -- Unix timestamp of first contact
  last_run_at      INTEGER NOT NULL,    -- Unix timestamp of last tick
  cwd              TEXT DEFAULT NULL     -- last known working directory
);
CREATE INDEX IF NOT EXISTS idx_cursors_last_run
  ON transcript_cursors(last_run_at);
```

- **New session**: cursor is inserted at `last_byte_offset = file_size`
  (EOF) on first tick. Nothing extracted. Extraction begins on the second
  tick after the session accumulates content beyond the cursor.
- **Path rotation**: if `transcript_path` changes (Claude Code rotated the
  file), cursor resets to the new file's EOF.
- **Idempotency**: replaying a tick without new bytes is a no-op. The
  cursor advances only after a successful extract-and-append cycle.
- **Crash safety**: if extraction or ephemeral append fails mid-tick, the
  cursor is not advanced. The next tick re-reads the same delta.

#### Discovery behavior by mode

| Mode | Source | Glob pattern | Max age |
| ---- | ------ | ------------ | ------- |
| Default (`--project` unset) | `discover_transcripts()` | `~/.claude/projects/*/*.jsonl` | 25 h |
| Single-folder (`--project PATH`) | Inline `Path.glob` | `{PATH}/*.jsonl` | 25 h |

Both modes sort results by modification time descending and apply the same
max-age cutoff (`time.time() - 25 * 3600`).

#### Design properties

- **No long-running state** — Each tick is a standalone Python process.
  Persistence: SQLite (cursors) + filesystem (ephemeral buffer).
- **Crash-safe** — Cursor not advanced on failure. `fcntl.flock` prevents
  interleaved writes to the ephemeral buffer.
- **Quota-free** — Every eligible window triggers an LLM call. Quality
  gating is post-hoc (importance filter, validator traces).
- **Stage 1 of two** — This pipeline captures episodic observations
  (Tulving 1972). Stage 2 (`core/consolidator.py`) elaborates these toward
  semantic memory via the PreCompact hook. See [Architecture](#what-it-does)
  for the full lifecycle.

#### Module index

| Module | File | Role |
| ------ | ---- | ---- |
| `transcript_ingest` | `core/transcript_ingest.py` | Orchestrator: `tick()`, `discover_transcripts()`, `extract_observations()`, `append_to_ephemeral()` |
| `cursors` | `core/cursors.py` | SQLite cursor store: `CursorStore.get()`, `CursorStore.upsert()` |
| `transcript` | `core/transcript.py` | JSONL parsing: `read_transcript_from()`, `summarize()`, `iter_windows()` |
| `session_detector` | `core/session_detector.py` | `detect_session_type()` — path + tool-mix heuristics |
| `extraction_affect` | `core/extraction_affect.py` | Somatic pre-pass over transcript windows |
| `issue_cards` | `core/issue_cards.py` | Stage 1.5 card synthesis over flat observations |
| `self_reflection_extraction` | `core/self_reflection_extraction.py` | Post-extraction heuristic rules and self-model updates |

Links: [Architecture overview](#what-it-does) · [CursorStore DB](#cursorstore-db) · [Flags](#flags) · [Consolidation pipeline](#what-it-does)
