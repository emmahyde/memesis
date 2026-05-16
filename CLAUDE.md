## Rules

1. **All persistence through `MemoryStore` or `database.py`.** Never write memory markdown files or SQLite rows directly. Atomic writes use `tempfile.mkstemp` + `shutil.move`. **CRITICAL: Do not open separate `sqlite3.connect()` connections to `index.db`.** The Peewee `db` singleton manages WAL mode and `busy_timeout=5000`; bypassing it creates concurrent-writer races. Always use `init_db()`, Peewee models, or `db.execute_sql()` for any DB access.

2. **All LLM calls through `core.llm.call_llm()`.** Do not create `anthropic.Anthropic()` clients in service modules.

3. **Tests never touch `~/.claude/memory`.** Use the `conftest.py` temporary directory fixtures.

4. **Skill invocations use full form.** `/memesis:learn`, `/memesis:recall`, `/memesis:forget` — not shorthand.

5. **Behavioral framing for friction signals.** When observations describe user friction (giving up, retries, scope reductions), phrase as workflow patterns rather than feelings. Both forms are allowed; behavioral framing transfers better across sessions.

## Context

Self-driven memory lifecycle plugin for Claude Code. Memories progress through stages: `ephemeral` → `consolidated` → `crystallized` → `instinctive`. The system ingests observations, curates them via LLM consolidation, and reinjects relevant memories at session start.

- **Language:** Python 3.10+, runtime deps in `pyproject.toml`
- **Test suite:** `python3 -m pytest tests/` from project root
- **Eval harness:** `python3 -m pytest eval/` (requires `pip install -e ".[eval]"`)

@AGENTS.md

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **memesis** (34056 symbols, 59142 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/memesis/context` | Codebase overview, check index freshness |
| `gitnexus://repo/memesis/clusters` | All functional areas |
| `gitnexus://repo/memesis/processes` | All execution flows |
| `gitnexus://repo/memesis/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
