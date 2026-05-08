## Rules

1. **All persistence through `MemoryStore` or `database.py`.** Never write memory markdown files or SQLite rows directly. Atomic writes use `tempfile.mkstemp` + `shutil.move`.

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
