---
name: reflect
description: This skill should be used when the user says "reflect on patterns", "update self-model", "what have you learned about me", "self-reflection", or any request to review behavioral tendencies, update known patterns, or explicitly trigger a self-model review. Runs on-demand and complements the automatic every-5-consolidations trigger.
---

# Reflect — On-Demand Self-Reflection

Review consolidation history, surface behavioral patterns, and propose updates to the self-model — with a preview step before anything is applied. You choose what gets written.

## Usage

```
/memesis:reflect
/memesis:reflect --sessions 20
```

`--sessions N` controls how many recent sessions to analyze (default: 10).

## Procedure

Self-reflection runs in two phases. Nothing is written until you approve it.

### Phase 1 — Preview

1. Call `SelfReflector.reflect()` to analyze the consolidation log against the current self-model. This reads history and calls the LLM but **does not write anything**.
2. Render the findings as a numbered preview:
   - **New patterns found** — tendencies identified in consolidation history not yet in the self-model
   - **Existing patterns to deprecate** — tendencies that consolidation history suggests are no longer accurate
   - **What stays** — brief note that nothing is applied until approved
3. If there are no findings (empty `observations` and `deprecated`), report that and stop. No approval step needed.

### Phase 2 — Approval

4. After rendering the preview, ask:

   > "Apply these changes? Reply `all`, `none`, or a space-separated list of numbers (e.g. `1 3`) to apply only specific findings."

5. Parse the user's response:
   - `all` — build a reflection dict with all findings and call `apply_reflection()`
   - `none` — discard, confirm nothing was applied
   - Numbers (e.g. `1 3`) — build a reflection dict containing only the selected items and call `apply_reflection()`

6. After applying, confirm what changed: how many new observations were added, how many tendencies were marked deprecated, and the memory ID of the updated self-model.

### Rendering the preview

Format each finding as a numbered item so users can reference them by number in their approval response.

New patterns use this format:

```
[N] NEW PATTERN — <tendency name>
    Trigger: <trigger description>
    Correction: <suggested correction>
    Confidence: <0.0–1.0>
    Evidence: <supporting evidence from consolidation history>
```

Deprecations use this format:

```
[N] DEPRECATE — <tendency name>
    Reason: no longer observed in recent consolidation history
```

## Implementation

### Phase 1 — call reflect(), render preview

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.self_reflection import SelfReflector
init_db(project_context=os.getcwd())

session_count = 10  # or parse from --sessions flag

reflector = SelfReflector()
findings = reflector.reflect(session_count=session_count)

observations = findings.get("observations", [])
deprecated = findings.get("deprecated", [])

if not observations and not deprecated:
    print("No new patterns found in recent consolidation history. Self-model is up to date.")
else:
    # Build a numbered list for preview rendering
    # findings_index maps display number -> ("observation", obs_dict) or ("deprecated", name)
    findings_index = {}
    n = 1

    print(f"## Self-Reflection Preview\n")
    print(f"Analyzed last {session_count} sessions.\n")

    if observations:
        print("### New Patterns Found\n")
        for obs in observations:
            tendency = obs.get("tendency", "Unknown tendency")
            trigger = obs.get("trigger", "")
            correction = obs.get("correction", "")
            confidence = obs.get("confidence", 0.5)
            evidence = obs.get("evidence", "")

            print(f"[{n}] NEW PATTERN — {tendency}")
            if trigger:
                print(f"    Trigger: {trigger}")
            if correction:
                print(f"    Correction: {correction}")
            print(f"    Confidence: {confidence}")
            if evidence:
                print(f"    Evidence: {evidence}")
            print()

            findings_index[n] = ("observation", obs)
            n += 1

    if deprecated:
        print("### Patterns to Deprecate\n")
        for name in deprecated:
            print(f"[{n}] DEPRECATE — {name}")
            print(f"    Reason: no longer observed in recent consolidation history")
            print()
            findings_index[n] = ("deprecated", name)
            n += 1

    print("Nothing has been applied yet.")
    print("Reply `all`, `none`, or space-separated numbers (e.g. `1 3`) to apply specific findings.")
```

### Phase 2 — apply approved findings

```python
# approval is the user's response string: "all", "none", or "1 3 5"
# findings_index and findings are available from Phase 1

approved_observations = []
approved_deprecated = []

approval = approval.strip().lower()

if approval == "none":
    print("No changes applied. Self-model unchanged.")
elif approval == "all":
    approved_observations = observations
    approved_deprecated = deprecated
else:
    selected_numbers = [int(x) for x in approval.split() if x.isdigit()]
    for num in selected_numbers:
        if num in findings_index:
            kind, payload = findings_index[num]
            if kind == "observation":
                approved_observations.append(payload)
            elif kind == "deprecated":
                approved_deprecated.append(payload)

if approved_observations or approved_deprecated:
    approved_findings = {
        "observations": approved_observations,
        "deprecated": approved_deprecated,
    }
    memory_id = reflector.apply_reflection(approved_findings)

    applied_new = len(approved_observations)
    applied_dep = len(approved_deprecated)
    print(f"Self-model updated (id: {memory_id}).")
    if applied_new:
        print(f"  + {applied_new} new pattern(s) added")
    if applied_dep:
        print(f"  - {applied_dep} pattern(s) marked deprecated")
```

## Notes

- This skill runs `reflect()` and `apply_reflection()` as separate steps, giving you control over what gets written.
- Automatic self-reflection also runs every 5 consolidations (triggered by `hooks/pre_compact.py` and `hooks/consolidate_cron.py`). Running `/memesis:reflect` on demand is independent — it won't skip or reset the automatic counter.
- If the self-model doesn't exist yet, `reflect()` seeds it before analyzing. The first run will usually show no findings since there's no history yet.
- The `--sessions N` flag controls the window of consolidation history reviewed. Wider windows surface slower-moving patterns; narrower windows are more responsive to recent changes.

## Examples

**Standard on-demand reflection**

```
/memesis:reflect
```

Analyzes the last 10 sessions. Preview shows 2 new patterns and 1 deprecation candidate:

```
## Self-Reflection Preview

Analyzed last 10 sessions.

### New Patterns Found

[1] NEW PATTERN — Premature abstraction
    Trigger: Designing shared utilities before a second use case exists
    Correction: Wait for the second caller. Extract when duplication is felt, not anticipated.
    Confidence: 0.7
    Evidence: Three consolidation entries noted me proposing shared modules before any other module needed them.

### Patterns to Deprecate

[2] DEPRECATE — Explaining before acting
    Reason: no longer observed in recent consolidation history

Nothing has been applied yet.
Reply `all`, `none`, or space-separated numbers (e.g. `1 3`) to apply specific findings.
```

User replies `1` — only the new pattern is applied, the deprecation is skipped:

```
Self-model updated (id: a7f3c2...).
  + 1 new pattern(s) added
```

**Wider window**

```
/memesis:reflect --sessions 30
```

Analyzes 30 sessions for slower-moving patterns that might not show in a 10-session window.
