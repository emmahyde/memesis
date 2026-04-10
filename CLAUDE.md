## Rules

1. **All persistence through `MemoryStore` or `database.py`.** Never write memory markdown files or SQLite rows directly. Atomic writes use `tempfile.mkstemp` + `shutil.move`.

2. **Privacy filter before every LLM call.** The consolidator's privacy filter strips emotional state patterns before content reaches the API. Never bypass it, even in tests.

3. **All LLM calls through `core.llm.call_llm()`.** Do not create `anthropic.Anthropic()` clients in service modules.

4. **Tests never touch `~/.claude/memory`.** Use the `conftest.py` temporary directory fixtures.

5. **Skill invocations use full form.** `/memesis:learn`, `/memesis:recall`, `/memesis:forget` — not shorthand.

## Context

Self-driven memory lifecycle plugin for Claude Code. Memories progress through stages: `ephemeral` → `consolidated` → `crystallized` → `instinctive`. The system ingests observations, curates them via LLM consolidation, and reinjects relevant memories at session start.

- **Language:** Python 3.10+, runtime deps in `pyproject.toml`
- **Test suite:** `python3 -m pytest tests/` from project root
- **Eval harness:** `python3 -m pytest eval/` (requires `pip install -e ".[eval]"`)

@AGENTS.md
