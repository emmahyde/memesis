# Plan: Extract Shared LLM Helper

**Source:** .context/CONTEXT-shared-llm-helper.md
**Generated:** 2026-03-28
**Status:** Ready for execution

## Overview

Extract `_call_llm` duplication from 4 modules into `core/llm.py`, migrating one caller at a time with green-suite checkpoints. Each wave is a self-contained commit.

**Waves:** 4
**Total tasks:** 6

---

## Wave 1: Foundation — Create core/llm.py

**Prerequisite:** None (first wave)

### Task 1.1: Create `core/llm.py` with `call_llm()` function

- **Files owned:** `core/llm.py`
- **Depends on:** None
- **Decisions:** D1 (str return), D3 (model constants), D4 (private _make_client), D5 (fence-strip from consolidator)
- **Acceptance criteria:**
  - [ ] `call_llm(prompt, *, max_tokens=1024, temperature=0, model=None) -> str` exists
  - [ ] `_make_client() -> anthropic.Anthropic | anthropic.AnthropicBedrock` selects based on `CLAUDE_CODE_USE_BEDROCK` env var
  - [ ] `DEFAULT_MODEL = "claude-sonnet-4-6"` and `BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6"` constants
  - [ ] `strip_markdown_fences(text: str) -> str` utility (public, callers may need it for retry scenarios)
  - [ ] Fence stripping handles ```` ```json ````, bare ```` ``` ````, and no-fence cases
  - [ ] No JSON parsing, no retry logic, no error wrapping — exceptions propagate
  - [ ] `import anthropic` is the only third-party import

**What to implement:**

```python
"""Shared LLM transport — client selection and response cleaning."""

DEFAULT_MODEL = "claude-sonnet-4-6"
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6"

def strip_markdown_fences(text: str) -> str: ...
def _make_client(): ...
def call_llm(prompt: str, *, max_tokens: int = 1024, temperature: float = 0, model: str | None = None) -> str: ...
```

### Task 1.2: Create `tests/test_llm.py`

- **Files owned:** `tests/test_llm.py`
- **Depends on:** Task 1.1
- **Decisions:** D7 (mock path is `core.llm.call_llm`)
- **Acceptance criteria:**
  - [ ] Tests for `strip_markdown_fences`: json fence, bare fence, no fence, nested fences
  - [ ] Tests for `_make_client`: Bedrock env var set → AnthropicBedrock, unset → Anthropic
  - [ ] Tests for `call_llm`: verifies client.messages.create called with correct params, returns stripped text
  - [ ] Tests for model selection: default model, Bedrock model, explicit model override
  - [ ] All mocks patch `core.llm.anthropic.Anthropic` / `core.llm.anthropic.AnthropicBedrock`
  - [ ] Full test suite (433 + new tests) passes green

**Wave 1 status:** pending

---

## Wave 2: Migrate simple callers — crystallizer + threads

**Prerequisite:** Wave 1 complete

### Task 2.1: Migrate `crystallizer.py` to use `core.llm.call_llm`

- **Files owned:** `core/crystallizer.py`, `tests/test_crystallizer.py`
- **Depends on:** Task 1.1
- **Decisions:** D1 (str return, caller parses JSON), D6 (update mock paths fully)
- **Acceptance criteria:**
  - [ ] Module-level `_call_llm` function deleted from crystallizer.py
  - [ ] `from core.llm import call_llm, strip_markdown_fences` at top of file
  - [ ] `_crystallize_group` calls `call_llm(prompt, max_tokens=1024, temperature=0)` then `json.loads()` on the result
  - [ ] `import anthropic` removed from crystallizer.py (no longer needed)
  - [ ] Tests in `test_crystallizer.py` updated: mock `core.llm.call_llm` instead of `core.crystallizer._call_llm`
  - [ ] Full test suite passes green

### Task 2.2: Migrate `threads.py` to use `core.llm.call_llm`

- **Files owned:** `core/threads.py`, `tests/test_threads.py`
- **Depends on:** Task 1.1
- **Decisions:** D1 (str return), D6 (update mock paths)
- **Acceptance criteria:**
  - [ ] Module-level `_call_llm` function deleted from threads.py
  - [ ] `from core.llm import call_llm, strip_markdown_fences` at top of file
  - [ ] `narrate_cluster` calls `call_llm(prompt, max_tokens=1024, temperature=0.3)` then `json.loads()` on the result (note: temperature=0.3 for narrative voice)
  - [ ] `import anthropic` removed from threads.py
  - [ ] Tests in `test_threads.py` updated: mock `core.llm.call_llm` instead of `core.threads._call_llm`
  - [ ] Full test suite passes green

**Wave 2 status:** pending

---

## Wave 3: Migrate consolidator (complex — retry logic preserved)

**Prerequisite:** Wave 2 complete

### Task 3.1: Migrate `consolidator.py` to use `core.llm.call_llm`

- **Files owned:** `core/consolidator.py`, `tests/test_consolidator.py`
- **Depends on:** Task 1.1
- **Decisions:** D1 (str return), D2 (retry stays in caller), D6 (update mock paths), D7 (mock core.llm.call_llm)
- **Acceptance criteria:**
  - [ ] `Consolidator._call_llm` method body replaced: calls `core.llm.call_llm(prompt, max_tokens=2048, temperature=0)`, then runs existing `_parse_decisions` on the result. Retry logic on JSON failure STAYS in this method — on first parse failure, appends JSON instructions and calls `core.llm.call_llm` again.
  - [ ] `Consolidator._call_resolution_llm` similarly delegates transport to `core.llm.call_llm(prompt, max_tokens=1024, temperature=0)`, keeps its own JSON parse + fence strip.
  - [ ] `import anthropic` removed from consolidator.py
  - [ ] Direct `anthropic.Anthropic()` / `anthropic.AnthropicBedrock()` creation removed from consolidator.py
  - [ ] Tests updated: `patch("core.consolidator.anthropic.Anthropic")` → `patch("core.llm.call_llm")` or `patch.object(consolidator, "_call_llm")` depending on test type
  - [ ] Retry tests still verify retry behavior (mock returns bad text first, good text second)
  - [ ] Contradiction resolution tests still pass
  - [ ] Full test suite passes green

**IMPORTANT for implementer:** The consolidator has ~25 test sites that patch `core.consolidator.anthropic.Anthropic`. Each must be found and updated. Use `grep -n "core.consolidator.anthropic" tests/test_consolidator.py` to find them all. Some tests use `patch.object(consolidator_instance, "_call_llm")` — those can remain since the instance method still exists as a shim.

**Wave 3 status:** pending

---

## Wave 4: Migrate self_reflection (instance method shim)

**Prerequisite:** Wave 3 complete

### Task 4.1: Migrate `self_reflection.py` to use `core.llm.call_llm`

- **Files owned:** `core/self_reflection.py`, `tests/test_self_reflection.py`
- **Depends on:** Task 1.1
- **Decisions:** D1 (str return), D2 (fallback stays in caller), D6 (instance shim preserved)
- **Acceptance criteria:**
  - [ ] `SelfReflector._call_llm` method body replaced: calls `core.llm.call_llm(prompt, max_tokens=2048, temperature=0)`, then calls existing `self._parse_response(raw)`. Fallback to `{"observations": [], "deprecated": []}` on JSON error stays in this method.
  - [ ] `import anthropic` removed from self_reflection.py
  - [ ] Direct `anthropic.Anthropic()` / `anthropic.AnthropicBedrock()` creation removed
  - [ ] Tests updated: module-level anthropic patches → `patch("core.llm.call_llm")`; `patch.object` tests on instance method remain working
  - [ ] Full test suite passes green
  - [ ] Run `python3 -m pytest tests/ eval/ -v` as final verification — all 457+ tests green

**Wave 4 status:** pending

---

## File Ownership Map

| File | Owner |
| --- | --- |
| `core/llm.py` | Task 1.1 |
| `tests/test_llm.py` | Task 1.2 |
| `core/crystallizer.py` | Task 2.1 |
| `tests/test_crystallizer.py` | Task 2.1 |
| `core/threads.py` | Task 2.2 |
| `tests/test_threads.py` | Task 2.2 |
| `core/consolidator.py` | Task 3.1 |
| `tests/test_consolidator.py` | Task 3.1 |
| `core/self_reflection.py` | Task 4.1 |
| `tests/test_self_reflection.py` | Task 4.1 |

## Cross-Wave Ownership Handoffs

| File | Wave N Owner | Wave M Owner | Handoff Notes |
| --- | --- | --- | --- |
| `core/llm.py` | Task 1.1 (create) | — | Read-only by all later waves; no modifications needed |

No cross-wave conflicts — all files are owned by exactly one task across the entire plan.

## Decision Traceability

| Decision | Tasks |
| --- | --- |
| D1 (str return) | Task 1.1, 2.1, 2.2, 3.1, 4.1 |
| D2 (no retry in shared) | Task 3.1, 4.1 |
| D3 (model constants) | Task 1.1 |
| D4 (private _make_client) | Task 1.1 |
| D5 (fence-strip from consolidator) | Task 1.1 |
| D6 (one at a time, green checkpoints) | Task 2.1, 2.2, 3.1, 4.1 |
| D7 (mock core.llm.call_llm) | Task 1.2, 2.1, 2.2, 3.1, 4.1 |
