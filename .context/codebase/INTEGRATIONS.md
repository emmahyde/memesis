# External Integrations

## APIs & Services

| Service | Purpose | Config / Credentials | Client Code |
| ------- | ------- | -------------------- | ----------- |
| Anthropic Messages API (direct) | LLM calls for consolidation, crystallization, contradiction resolution, self-reflection, narrative synthesis, backfill pipeline | `ANTHROPIC_API_KEY` env var (SDK default; never referenced explicitly in code) | `core/consolidator.py`, `core/crystallizer.py`, `core/self_reflection.py`, `core/threads.py`, `scripts/consolidate.py`, `scripts/reduce.py` |
| AWS Bedrock (Anthropic via Bedrock) | Alternate LLM routing for cron context (no interactive session) | `AWS_PROFILE=bedrock-users`, `AWS_REGION=us-west-2`, `CLAUDE_CODE_USE_BEDROCK=true` — all defaulted in `hooks/consolidate_cron.py` | Same files as above; every LLM call checks `os.environ.get("CLAUDE_CODE_USE_BEDROCK")` and switches between `anthropic.Anthropic()` and `anthropic.AnthropicBedrock()` |

### Anthropic Client Selection Pattern

Every LLM call site follows the same pattern (example from `core/consolidator.py:225`):

```python
if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
    client = anthropic.AnthropicBedrock()
    model = "us.anthropic.claude-sonnet-4-6"   # Bedrock model ID format
else:
    client = anthropic.Anthropic()
    model = self.model  # defaults to "claude-sonnet-4-6"
```

The Bedrock model ID uses the `us.anthropic.` prefix and cross-region inference format. The direct API uses the standard short model ID.

`ANTHROPIC_API_KEY` is never set or referenced in code — the SDK reads it from the environment automatically. `tests/conftest.py` pops `CLAUDE_CODE_USE_BEDROCK` to prevent accidental Bedrock calls during test runs.

### LLM Call Parameters

All production LLM calls use:
- `temperature=0` — deterministic output for JSON-structured decisions
- `max_tokens` varies by call: 2048 for consolidation/self-reflection, 1024 for crystallization/contradiction resolution, 16384 for backfill consolidation gate (`scripts/consolidate.py`)
- No explicit `timeout` or `max_retries` configured — relies on SDK defaults (10 min timeout, 2 retries)

## Databases

- **SQLite** (stdlib `sqlite3`): Single-file database at `{base_dir}/index.db`. WAL mode. No external DB server. Connection is opened per-operation with `sqlite3.connect(self.db_path)` — no persistent connection pool. File locking via `fcntl.flock` for multi-process safety in `hooks/pre_compact.py` and `hooks/consolidate_cron.py`.

## Filesystem Integrations

### Claude Code Hook System

Integration point: `hooks/hooks.json` registers three hooks with the Claude Code runtime:

| Hook Event | Script | Timeout | I/O |
| ---------- | ------ | ------- | --- |
| `SessionStart` | `hooks/session_start.py` | 5s | stdout: memory context block injected into session |
| `PreCompact` | `hooks/pre_compact.py` | 30s | stdin: conversation text piped in; stdout: empty (required); stderr: summary |
| `UserPromptSubmit` | `hooks/user_prompt_inject.py` | 3s | stdin: user prompt text; stdout: just-in-time memory injection or empty |

The hook command uses `${CLAUDE_PLUGIN_ROOT}` as the plugin root variable, set by Claude Code at runtime.

### Native Claude Code Memory Files

`core/ingest.py` reads native Claude Code MEMORY.md and linked `.md` files from:
- `~/.claude/projects/{path_hash}/memory/` (project-scoped, checked first)
- `~/.claude/memory/` (global fallback)

Files must have YAML frontmatter with a `name` field to be ingested. Native memory types (`user`, `feedback`, `project`, `reference`) map to memesis observation types via `NATIVE_TYPE_MAP`.

### Session Environment Variables

| Variable | Source | Used by |
| -------- | ------ | ------- |
| `CLAUDE_SESSION_ID` | Set by Claude Code at hook invocation | `hooks/session_start.py`, `hooks/pre_compact.py`, `hooks/user_prompt_inject.py` — defaults to `"unknown"` if absent |
| `CLAUDE_CODE_USE_BEDROCK` | Set by operator / cron; removed in tests | Every LLM call site |
| `CLAUDE_PLUGIN_ROOT` | Set by Claude Code; used in `hooks.json` command template | `hooks/hooks.json` |
| `AWS_PROFILE` | Defaulted to `"bedrock-users"` in cron | `hooks/consolidate_cron.py` |
| `AWS_REGION` | Defaulted to `"us-west-2"` in cron | `hooks/consolidate_cron.py` |

## Webhooks / Events

The system is consumer-only — it does not expose any HTTP endpoints or receive webhooks. All integration is via:

1. **Claude Code hook events** (inbound): The runtime invokes hook scripts as subprocesses and reads their stdout. See `hooks/hooks.json`.
2. **Cron trigger** (inbound): `hooks/consolidate_cron.py` is invoked on a cron schedule (documented as `7 * * * *` in the file header). It scans all project memory directories under `~/.claude/projects/` for unprocessed ephemeral buffers.

## Auth Providers

None. Authentication is entirely ambient:
- Anthropic API key: read from `ANTHROPIC_API_KEY` env var by the SDK
- AWS Bedrock: uses ambient AWS credentials from the profile `bedrock-users` (set via `AWS_PROFILE` in cron)

No OAuth, no session tokens, no user accounts.

## Backfill Pipeline (One-Time Scripts)

`scripts/` contains a standalone pipeline for seeding memories from historical conversation transcripts. These scripts are not part of the live hook system but share the same Anthropic client pattern.

| Script | Input | Output |
| ------ | ----- | ------ |
| `scripts/scan.py` | Transcript files | `backfill-output/observations.db` (SQLite) |
| `scripts/reduce.py` | `observations.db` | Deduplicated observation store |
| `scripts/consolidate.py` | `observations.db` | `backfill-output/consolidation-results.jsonl` + `reinforcements.json` |
| `scripts/seed.py` | `consolidation-results.jsonl` | Entries written to live `MemoryStore` |

`backfill-output/*.jsonl` files committed to the repo are historical run artifacts.
