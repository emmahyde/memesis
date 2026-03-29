# Discussion Log: Extract Shared LLM Helper

**Date:** 2026-03-28
**Mode:** Panel discussion (--auto)
**Stakeholders:**

- Kenji, senior Python engineer / core package owner — bias: minimal public surface, explicit contracts
- Dara, QA/testing lead — bias: test-first refactoring, verifiable migration steps
- Mei, infrastructure engineer / hooks maintainer — bias: simplicity, operational debuggability

---

## Wave 1: Initial Positions

### Kenji (API Architect)

1. **Approach:** Create `core/llm.py` with two public functions: `call_llm_raw() -> str` (client + fence strip) and `call_llm() -> dict` (raw + json.loads). Optional `model` param with `DEFAULT_MODEL` constant.
2. **Tasks:** Write core/llm.py, refactor crystallizer, threads, self_reflection (thin shim), consolidator (keep retry), update 25+ test mock paths.
3. **Top concern:** Test mock path divergence — 25+ sites patching `core.consolidator.anthropic.Anthropic` will silently stop working. Highest-probability breakage.
4. **Convention:** Retry and fallback logic must stay in callers, not leak into shared module. Infrastructure, not policy.
5. **Reuse:** Fence-stripping from consolidator._parse_decisions. Model ID constants from any of the 4 files.

### Dara (Testing Lead)

1. **Approach:** Extract first with zero callers. Green baseline. Migrate one caller at a time with green checkpoint between each. Never multiple callers in one commit.
2. **Tasks:** Audit mock paths first, create core/llm.py + tests, then migrate consolidator → crystallizer → threads → self_reflection one at a time, remove dead imports last.
3. **Top concern:** `patch.object(instance, "_call_llm")` integration tests. If callers call module-level function directly instead of through instance method, patch.object silently stops intercepting.
4. **Convention:** Mock at point of use. Retired mock paths must be explicitly deleted. Keep instance shims so patch.object works.
5. **Reuse:** conftest.py env-pop for CLAUDE_CODE_USE_BEDROCK. Instance method shim pattern.

### Mei (Infrastructure/Simplicity)

1. **Approach:** ONE function: `call_llm(prompt, *, max_tokens, temperature, model) -> str`. Returns stripped text, NEVER parsed dict. Parse is always caller's job.
2. **Tasks:** Write core/llm.py (one function), update 4 callers to delegate transport only, add fence-strip test, verify existing tests.
3. **Top concern:** Abstraction that makes debugging harder. Generic errors from shared module obscure which service made the call. One job, one failure mode.
4. **Convention:** Shared function returns raw text only. No exceptions to this rule. Future callers must also own their parse step.
5. **Reuse:** Fence-stripping from consolidator._parse_decisions verbatim. Bedrock env var check verbatim.

---

## Wave 2: Debate Summary

Key tensions resolved:

**Return type (`-> str` vs `-> dict`):** Mei's position (str-only) won. Kenji's `call_llm() -> dict` adds a JSON parse step whose failure mode differs across callers (consolidator retries, self_reflection falls back to empty dict, crystallizer falls back to simple promotion). Centralizing the parse means centralizing the error handling, which contradicts Kenji's own principle that error policy belongs in callers.

**Mock migration strategy:** Dara's one-at-a-time discipline combined with Kenji's clean-break approach. Keep instance method shims where patch.object is used in integration tests, but fully update mock paths in unit tests. No ghost patches.

**Instance shims vs module-level calls:** Dara proposed keeping `_call_llm` as instance method shims. Mei agreed this preserves stack trace clarity (you see `Consolidator._call_llm → core.llm.call_llm` not just `core.llm.call_llm`). Kenji accepted as reasonable for the 2 callers that use instance methods (consolidator, self_reflection). The other 2 (crystallizer, threads) have module-level `_call_llm` which can be replaced directly.

---

## Consensus

1. Single `call_llm() -> str` function in `core/llm.py` (Mei's design)
2. One-at-a-time migration with green checkpoints (Dara's discipline)
3. Instance shims preserved for consolidator + self_reflection (Dara + Mei)
4. Direct replacement for crystallizer + threads module-level functions (Kenji)
5. All retry/fallback/parse logic stays in callers (unanimous)
6. Model constants centralized in core/llm.py (unanimous)

## Unresolved Disagreements

None — full consensus reached.
