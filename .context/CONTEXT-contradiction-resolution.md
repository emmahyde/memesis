# Context: Fix Contradiction Resolution Branches

**Date:** 2026-03-28
**Slug:** contradiction-resolution

## Work Description

The superseded and scoped branches in consolidator.py `_execute_resolution` are identical no-ops. Fix them to actually resolve contradictions differently by type.

## Locked Decisions

### D1: superseded → archive the old memory
When resolution_type is "superseded", the old memory should be archived (not just updated). Use `store.archive(memory_id)` which sets `archived_at` and excludes from injection. Also update the content to include a note about what superseded it.

### D2: scoped → add context scope to both memories
When resolution_type is "scoped", update the old memory's content with the refined (scoped) version AND add a `scope:` tag to clarify its applicability domain.

### D3: coexist (else) → update content only (current behavior)
The else branch already does the right thing — update with refined content. Keep as-is.

### D4: Log action should reflect the resolution type
superseded → log action "archived", scoped → log action "merged", coexist → log action "merged" (current)

## Conventions
- Per-operation SQLite connections
- ValueError for not-found
- Existing store.archive() method handles archival correctly

## Files
- core/consolidator.py — the three branch bodies (~lines 478-497)
- tests/test_consolidator.py — TestContradictionResolution class
