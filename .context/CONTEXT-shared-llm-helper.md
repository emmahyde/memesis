# Context: Extract Shared LLM Helper

**Date:** 2026-03-28
**Mode:** Panel discussion (--auto)
**Slug:** shared-llm-helper

## Work Description

Extract the duplicated `_call_llm` pattern from consolidator.py, crystallizer.py, threads.py, and self_reflection.py into a shared `core/llm.py` module.

## Locked Decisions

### D1: Single function returning str, not dict
`call_llm(prompt, *, max_tokens=1024, temperature=0, model=None) -> str` — returns fence-stripped text only. No JSON parsing in the shared layer. Each caller owns its own parse step because the expected shapes differ (list of decisions, `{observations, deprecated}`, free dict). This keeps one job, one failure mode.

### D2: No retry logic in shared module
The consolidator's retry-on-JSON-failure is domain-specific policy (appends prompt instructions on retry). Retry, fallback-to-empty, and error wrapping stay in callers. `call_llm` lets exceptions propagate.

### D3: Module-level constants for model IDs
`DEFAULT_MODEL = "claude-sonnet-4-6"` and `BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6"` in `core/llm.py`. One place to update when the model changes.

### D4: Private `_make_client()` helper
Internal helper creates `anthropic.AnthropicBedrock()` or `anthropic.Anthropic()` based on `CLAUDE_CODE_USE_BEDROCK` env var. Not part of public API.

### D5: Fence-stripping from consolidator is the canonical implementation
Use the fence-stripping block from `consolidator._parse_decisions` verbatim — it handles both `` ```json `` and bare `` ``` `` fences.

### D6: Migrate one caller at a time, green suite between each
Never migrate multiple callers in one commit. Update mock paths fully — no ghost patches left behind. Callers that had `_call_llm` as instance methods keep thin shims that delegate to `core.llm.call_llm` so `patch.object` integration tests need minimal changes.

### D7: Test mock path is `core.llm.call_llm`
After migration, tests that need to mock the LLM should patch `core.llm.call_llm` (the function) rather than `core.llm.anthropic.Anthropic` (the class). This is simpler and more stable. For integration tests using `patch.object` on instance methods, the thin shim preserves that pattern.

## Conventions to Enforce
- Per-operation SQLite connections (unchanged)
- ValueError for domain errors (unchanged)
- Private helpers use single underscore prefix
- No third-party deps at runtime except `anthropic`
- `temperature=0` for determinism (default), callers override as needed (threads uses 0.3)

## Concerns to Watch
- Ghost mock patches: any test still patching `core.consolidator.anthropic.Anthropic` after migration will silently do nothing — the import no longer exists in that namespace. Every retired mock path must be deleted.
- Consolidator retry logic: must NOT be pulled into shared module. It's caller policy.
- Error traceability: when an LLM call fails in pre_compact.py, the stack trace must show which service (consolidation vs crystallization vs thread-building vs self-reflection) made the call. The thin shim pattern preserves this.

## Reusable Code
- Fence-stripping: `consolidator.py` lines ~284-293
- Bedrock check: `os.environ.get("CLAUDE_CODE_USE_BEDROCK")` — identical in all 4 files
- Model constants: `"us.anthropic.claude-sonnet-4-6"` / `"claude-sonnet-4-6"` — identical in all 4 files

## Canonical References
- `core/llm.py` — new shared module (to be created)
- `core/consolidator.py` — `_call_llm`, `_call_resolution_llm` (both use retry + parse)
- `core/crystallizer.py` — module-level `_call_llm` (simple: call + parse JSON)
- `core/threads.py` — module-level `_call_llm` (simple: call + parse JSON, temperature=0.3)
- `core/self_reflection.py` — `SelfReflector._call_llm` (call + custom parse into {observations, deprecated})
- `tests/test_consolidator.py` — patches `core.consolidator.anthropic.Anthropic` (~25 sites)
- `tests/test_crystallizer.py` — patches `core.crystallizer._call_llm`
- `tests/test_threads.py` — patches `core.threads._call_llm`
- `tests/test_self_reflection.py` — patches `anthropic.Anthropic` or instance `_call_llm`
