---
name: autoresearch
description: Vendored autoresearch skill — iterates a Modify→Verify→Keep/Discard loop over the defined mutation surface (D-14), gated by the full guard suite (D-15) and a token budget (D-16). Invoked by `evolve --autoresearch` to automatically improve pipeline fidelity against a compiled eval delta. Memesis ships its own copy; upstream updates are pulled in deliberately, not auto-tracked.
---

# Autoresearch — Autonomous Pipeline Mutation Loop

Iterates a Modify→Verify→Keep/Discard loop over the memesis pipeline mutation surface.
Driven by a compiled eval delta (from `/memesis:evolve`) and a YAML config file.

## Vendored-copy policy (D-13)

This skill is a **vendored sibling** under `skills/autoresearch/`.
Memesis ships its own copy — no external skill dependency.
Upstream updates are pulled in deliberately, not auto-tracked.

## Usage

Invoked by `evolve --autoresearch` (Task 4.2 wiring). Not called directly.

```python
from core.autoresearch import Autoresearcher

researcher = Autoresearcher(session_path=Path("~/.claude/memesis/evolve/<session>"), eval_slug="<slug>")
researcher.run()
```

## Config file

Reads `<session_path>/autoresearch.yaml` at construction. Example:

```yaml
max_iterations: 10
token_budget: 50000
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_iterations` | int | `10` | Hard cap on loop iterations (D-16) |
| `token_budget` | int | (required) | Cumulative LLM token spend ceiling (D-16) |
| `iteration_count` | int | `0` | Written back after each kept mutation |
| `token_spend` | int | `0` | Written back after each kept mutation |

## Mutation surface (D-14)

Autoresearch may only propose mutations to these files:

| File | What changes |
|------|-------------|
| `core/prompts.py` | `OBSERVATION_EXTRACT_PROMPT`, `SESSION_TYPE_GUIDANCE` |
| `core/issue_cards.py` | `ISSUE_SYNTHESIS_PROMPT` |
| `core/rule_registry.py` | `ParameterOverrides` numeric thresholds |
| `core/consolidator.py` | `_execute_keep()` logic |
| `core/crystallizer.py` | Crystallization gates |

Any attempt to mutate a file outside this surface raises `ValueError`.

## Guard set (D-15)

After every mutation, ALL of the following must pass before `keep`:

1. `python3 -m pytest tests/` — full unit suite
2. Tier-3 invariant tests (explicit):
   - `TestCardImportance`
   - `TestAllIndicesInvalidDemotion`
   - `TestRule3KensingerRemoved`
   - `TestEvidenceIndicesValidation`
3. `eval/recall/` — regression suite (prevents fixing session A by breaking session B)
4. Manifest JSON round-trip check

If any guard fails → `discard` (git checkout -- <file>).
If all guards pass → `keep` (atomic write, update YAML).

## Loop behavior

```
for iteration in range(max_iterations):
    if token_spend >= token_budget:
        halt()  # D-16: mid-iteration, no grace period
    target_file = select_mutation_target()
    new_content = _propose_mutation(target_file)
    apply_mutation(target_file, new_content)
    if guard_suite_passes():
        keep(target_file, new_content)
        update_yaml(iteration_count, token_spend)
    else:
        discard(target_file)
```

## Token tracking

Token spend is accumulated from `llm_envelope` events in the active `TraceWriter`.
The `Autoresearcher` accepts an optional `token_counter` injection for tests.

## Halt conditions (D-16)

- `iteration_count >= max_iterations` — hard cap
- `token_spend >= token_budget` — checked **before** each iteration starts (mid-iteration halt, no grace period)

## Keep / Discard mechanics

**Keep:** atomic write via `tempfile.mkstemp` + `shutil.move`, then update `autoresearch.yaml` with new `iteration_count` and `token_spend`.

**Discard:** `git checkout -- <file>` to revert the working-tree file.

## Implementation

`core/autoresearch.py` — `Autoresearcher` class.

## Notes

- `_propose_mutation(target_file) -> str` is a stub method returning new file content. The orchestrator or LLM provides content; the engine focuses on apply/verify/keep/discard plumbing.
- The guard suite is run via `subprocess.run(["python3", "-m", "pytest", ...], check=False)`.
- Token budget exhaustion halts mid-iteration with no grace period — re-run with a higher budget if needed.
