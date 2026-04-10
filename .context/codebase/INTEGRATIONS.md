# External Integrations

## APIs & Services

| Service | Purpose | Config / Credentials | Client Code |
| ------- | ------- | -------------------- | ----------- |
| Anthropic Messages API (direct) | LLM calls: consolidation, crystallization, reconsolidation, self-reflection, narrative synthesis, coherence checks | `ANTHROPIC_API_KEY` env var (SDK default; never referenced explicitly) | `core/llm.py` `call_llm()` — centralized transport for all LLM calls |
| AWS Bedrock (Anthropic via Bedrock) | Alternate LLM routing for cron context (no interactive session) | `AWS_PROFILE=bedrock-users`, `AWS_REGION=us-west-2`, `CLAUDE_CODE_USE_BEDROCK=true` — defaulted in `hooks/consolidate_cron.py` | `core/llm.py` `_make_client()` switches between `anthropic.Anthropic()` and `anthropic.AnthropicBedrock()` |
| AWS Bedrock Titan Text Embeddings v2 | Float32 vector embeddings (512 dimensions) for KNN search | Same AWS credentials as above; model ID `amazon.titan-embed-text-v2:0` | `core/embeddings.py` `embed_text()` via `boto3` `bedrock-runtime` client |

### Centralized LLM Transport

All LLM calls route through `core/llm.py` `call_llm()` (enforced by `CLAUDE.md` rule 3). This function:

1. Selects client based on `CLAUDE_CODE_USE_BEDROCK` env var
2. Uses model constants: `DEFAULT_MODEL = "claude-sonnet-4-6"` (direct) or `BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6"` (Bedrock)
3. Strips markdown fences from response text
4. Does NOT parse JSON or retry — callers own response handling

### LLM Call Parameters

All production LLM calls use:
- `temperature=0` — deterministic output for JSON-structured decisions
- `max_tokens` varies by caller: 1024 default, 2048 for consolidation/self-reflection, 16384 for backfill consolidation
- No explicit `timeout` or `max_retries` configured — relies on SDK defaults (10 min timeout, 2 retries)
- No token usage logging (see `.context/research/ecosystem-pitfalls.md` section 4)

### Embedding Service

`core/embeddings.py` manages the Bedrock Titan embedding client:

- **Lazy initialization**: `_get_bedrock_client()` creates the boto3 client on first call
- **Graceful degradation**: Returns `None` if boto3 is not installed or Bedrock client creation fails
- **Parameters**: 512 dimensions, L2-normalized (`normalize=True`), text truncated to 4000 chars
- **Serialization**: `struct.pack(f"{n}f", *embedding)` produces raw float32 bytes for sqlite-vec
- **AWS config**: `AWS_REGION` defaults to `us-west-2`, `AWS_PROFILE` defaults to `bedrock-users`

## Databases

- **SQLite** (via Peewee ORM + apsw): Single-file database at `{base_dir}/index.db`. WAL mode enabled. Dual-connection architecture (peewee for relational, apsw for vector). See `STACK.md` for schema details.
- **No external database server**: All storage is local SQLite files.

### Connection Patterns

| Subsystem | Library | Connection Lifecycle | Config |
| --------- | ------- | -------------------- | ------ |
| Relational (memories, logs, threads, edges) | peewee `SqliteDatabase` | Persistent singleton, deferred init | `core/database.py` `init_db()` — pragmas: WAL, synchronous=normal, busy_timeout=5000 |
| Vector (vec_memories) | apsw `Connection` | New connection per operation | `core/vec.py` `VecStore._connect()` — loads sqlite-vec extension each time |
| FTS5 (memories_fts) | peewee (raw SQL) | Shares peewee connection | Created in `core/database.py` `_create_fts_table()` |

### File Locking

`fcntl.flock` is used for multi-process safety in:
- `hooks/pre_compact.py`: Locks ephemeral buffer during snapshot-and-clear
- `hooks/consolidate_cron.py`: Same lock pattern to prevent double-processing if cron and hook overlap

## Claude Code Hook System

Integration point: `hooks/hooks.json` registers three hooks with the Claude Code runtime.

| Hook Event | Script | Timeout | I/O | Trigger |
| ---------- | ------ | ------- | --- | ------- |
| `SessionStart` | `hooks/session_start.py` | 5s | stdout: memory context block injected into session | Session opens |
| `PreCompact` | `hooks/pre_compact.py` | 30s | stdin: conversation text; stdout: empty; stderr: summary | Before context compaction |
| `UserPromptSubmit` | `hooks/user_prompt_inject.py` | 3s | stdin: user prompt JSON; stdout: just-in-time memory injection | Every user message |

All hook commands:
- Run from a plugin-managed venv: `${CLAUDE_PLUGIN_DATA}/venv/bin/python3`
- Set `NLTK_DATA=${CLAUDE_PLUGIN_DATA}/nltk_data` for corpus access
- Use `${CLAUDE_PLUGIN_ROOT}` as the plugin source root (set by Claude Code)

The `SessionStart` hook also runs `scripts/install-deps.sh` first to ensure the venv and dependencies are current (compares `requirements.txt` against a stamp file).

## Native Claude Code Memory Ingestion

`core/ingest.py` reads native Claude Code `MEMORY.md` and linked `.md` files from:
- `~/.claude/projects/{path_hash}/memory/` (project-scoped, checked first)
- `~/.claude/memory/` (global fallback)

Files must have YAML frontmatter with a `name` field to be ingested. Native memory types (`user`, `feedback`, `project`, `reference`) map to memesis observation types via `NATIVE_TYPE_MAP`.

## Session Environment Variables

| Variable | Source | Used by |
| -------- | ------ | ------- |
| `CLAUDE_SESSION_ID` | Set by Claude Code at hook invocation | `hooks/session_start.py`, `hooks/pre_compact.py`, `hooks/user_prompt_inject.py` — defaults to `"unknown"` if absent |
| `CLAUDE_CODE_USE_BEDROCK` | Set by operator / cron; removed in tests | `core/llm.py` `_make_client()` and `core/embeddings.py` |
| `CLAUDE_PLUGIN_ROOT` | Set by Claude Code | `hooks/hooks.json` command templates |
| `CLAUDE_PLUGIN_DATA` | Set by Claude Code | `hooks/hooks.json` — venv and NLTK data paths |
| `AWS_PROFILE` | Defaulted to `"bedrock-users"` in cron and embeddings | `hooks/consolidate_cron.py`, `core/embeddings.py` |
| `AWS_REGION` | Defaulted to `"us-west-2"` in cron and embeddings | `hooks/consolidate_cron.py`, `core/embeddings.py` |
| `ANTHROPIC_API_KEY` | User environment (never set in code) | `anthropic.Anthropic()` SDK auto-reads |

## Auth Providers

None. Authentication is entirely ambient:
- **Anthropic API**: `ANTHROPIC_API_KEY` env var read by the SDK
- **AWS Bedrock**: Ambient AWS credentials from the profile `bedrock-users` (set via `AWS_PROFILE`)

No OAuth, no session tokens, no user accounts.

## Webhooks / Events

The system is consumer-only — it does not expose any HTTP endpoints or receive webhooks. All integration is via:

1. **Claude Code hook events** (inbound): The runtime invokes hook scripts as subprocesses and reads their stdout. See `hooks/hooks.json`.
2. **Cron trigger** (inbound): `hooks/consolidate_cron.py` is invoked on a cron schedule (`7 * * * *`). It scans all project memory directories under `~/.claude/projects/` for unprocessed ephemeral buffers.

## Feature Flags

`core/flags.py` provides a runtime toggle system:
- Reads from `{base_dir}/flags.json` (created manually; not auto-generated)
- 17 flags defined in `DEFAULTS` dict, all defaulting to `True`
- Covers: `thompson_sampling`, `reconsolidation`, `graph_expansion`, `ghost_coherence`, `affect_awareness`, `causal_edges`, `contradiction_tensors`, and more
- Cache invalidation via `flags.reload()`

## Backfill Pipeline (One-Time Scripts)

`scripts/` contains a standalone pipeline for seeding memories from historical conversation transcripts. These scripts share the same Anthropic client pattern.

| Script | Input | Output |
| ------ | ----- | ------ |
| `scripts/scan.py` | Transcript files | `backfill-output/observations.db` (SQLite) |
| `scripts/reduce.py` | `observations.db` | Deduplicated observation store (TF-IDF cosine dedup) |
| `scripts/consolidate.py` | `observations.db` | `backfill-output/consolidation-results.jsonl` + `reinforcements.json` |
| `scripts/seed.py` | `consolidation-results.jsonl` | Entries written to live memory database |
