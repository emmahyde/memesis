---
name: review
description: >
  Architecture and design review for memesis. Assesses a module, design decision, or proposed
  change against stated design goals, known constraints (CLAUDE.md rules, panel decisions),
  and existing behavior. Surfaces latent risks. Every recommendation is steelmanned before
  being stated. Use when evaluating a proposed change, reviewing a PR-equivalent changeset,
  or auditing a subsystem before extending it. Triggers on: "review this change", "is this
  the right approach", "assess risks", "design review", "architecture review", "should I",
  /memesis:review.
---

# Architecture & Design Review

**Invoked as:** `/memesis:review`

Assess a module or change against design goals, known constraints, and latent risks. Every recommendation is steelmanned before it is stated.

Before any recommendation, steelman the alternative. See `/memesis:index` for the full protocol.

**Deeper reference:** `.claude/skills/index/references/design-decisions.md`, `.claude/skills/index/references/glossary.md`

---

## Workflow

### Step 1 — Read relevant planning docs

Check `.planning/` for panel consensus records affecting this area. These are load-bearing decisions — contradicting them requires explicit rationale.

### Step 2 — Read the existing architecture map

Check `.context/codebase/ARCHITECTURE.md` and `CONCERNS.md` for prior analysis.

### Step 3 — Define the change surface

- Which files change?
- Which behaviors change?
- Which adjacent systems could be affected?

### Step 4 — Check CLAUDE.md constraints

| Rule | What to check |
|------|-------------|
| Rule 1: All persistence through `MemoryStore` or `database.py` | No direct SQL writes outside the ORM |
| Rule 1 (critical): No `sqlite3.connect()` | All DB access via `init_db()` + Peewee models |
| Rule 2: All LLM calls through `core.llm.call_llm()` | No `anthropic.Anthropic()` in service modules |
| Rule 3: Tests use `conftest.py` fixtures | No test touching `~/.claude/memory` |

### Step 5 — Check latent risks

| Risk | Check |
|------|-------|
| Concurrent-write race | Does change bypass Peewee WAL + `busy_timeout=5000`? |
| FTS5 splitter | Does new migration SQL have `;` inside `--` comments? |
| Migration ordering | Does new migration depend on a column added by a later migration? |
| Shadow-mode assumption | Does change assume `SHADOW_ONLY=False`? |
| Cosine threshold drift | Thresholds calibrated for `bge-small-en-v1.5` at 384d — don't reuse for other models |

### Step 6 — Steelman, then recommend

1. **Against:** Strongest case against (2-4 sentences). What assumptions? What would flip it?
2. **Falsifying condition:** One thing that, if true, flips the recommendation.
3. **Tiebreaker:** Specific evidence (code, log, panel decision).
4. **Recommendation:** State it.

---

## Key Design Decisions (summary)

| Decision | Constraint |
|---------|-----------|
| Peewee singleton | Never bypass with `sqlite3.connect()` |
| LLM centralization | Never instantiate `anthropic.Anthropic()` outside `core/llm.py` |
| Shadow mode (Decision C3) | `SHADOW_ONLY=True` until false-prune rate is measured |
| Activation formula | NOT ACT-R; not bounded to [0,1]; normalization is caller's responsibility |
| Cosine thresholds | 0.72 link, 0.85 dedup — calibrated for `bge-small-en-v1.5` |
| Three-tier retrieval | Not flat ranking — tiers degrade gracefully as memory grows |
| Ordinal mismatch | `Observation.ordinal` is 0-indexed; LLM `obs_ids` are 1-indexed |

---

## Reporting Format

```
## Review: [Module / Change / Decision]

### Change Surface
[Files, behaviors, adjacent systems affected]

### Constraint Violations
[Any CLAUDE.md rules or panel decisions violated]

### Latent Risks
[Each risk with severity and specific condition]

### Steelmanned Recommendation
Against: [...] Wins if: [...]
For: [...]
Recommendation: [...]

### Severity
[Critical / High / Medium / Low] — [1 sentence rationale]
```
