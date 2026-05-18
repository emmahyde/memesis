# PASSOFF · memesis-memory-kind · 2026-05-17 17:50

## LEGEND
now active   done finished   next upcoming   blocked stuck   note decision   ref pointer
> causes / leads to   => implies   <- depends on   @path:line   [H/M/L] priority   {S|M|L} effort

## CONTEXT
335/375 `memories.memory_kind` rows are NULL > panel renders `_DEFAULT_EMOJI` 🔸 (not in legend).
Goal: require memory_kind on consolidated+ rows. Three-part fix, ordered B > backfill > C > A.

## DONE
- diagnosis: 🔸 = `_DEFAULT_EMOJI` fallback @hooks/_render.py:28 — memory_kind NULL
- DB count: None=335 fact=18 gotcha=8 opinion=7 decision=5 invariant=2
- side-fix: hookify rule pattern narrowed `venv/bin/python` > `\.venv/bin/python` @~/.claude/hookify.use-uv-run.local.md
- diagnosis: prompt-type SessionStart err = claude-obsidian plugin bug, NOT memesis

## VERIFIED FACTS
- main CONSOLIDATION_PROMPT does NOT request memory_kind; only DECOMPOSER prompt does @core/prompts.py:863
- consolidator fills kind via `derive_memory_kind(decision["kind"], evidence_count)` @core/consolidator.py:994
- `_OBSERVATION_TO_MEMORY_KIND` maps only decision/correction/constraint/preference @core/validators.py:59
- finding>lesson|fact; open_question>None by design; goal/bias/todo/debt unreachable @core/validators.py:70
- `models.py:94` memory_kind = TextField(null=True) — null intentional for ephemeral/open_question
- schema `ConsolidationDecision` has `kind` validator @core/schemas.py:245 — no `memory_kind` field
- decomposer already emits + consumes memory_kind @core/decomposer.py:74,101

## NEXT
- [H] B: classifier — fn `classify_memory_kind(title, content) -> kind` via call_llm(system_prompt_file=classification). Used by new-memory path + backfill. {M}
- [H] B: wire B into consolidator — when derive_memory_kind() returns None, call classifier before Memory.create @core/consolidator.py:994 {S}
- [H] backfill: script over 335 NULL consolidated/instinctive rows, classify, UPDATE memory_kind. Dry-run flag. {M}  <- B classifier
- [M] C: migration 0008 — CHECK `memory_kind IN (<MEMORY_KIND_VALUES>) OR memory_kind IS NULL`. Rejects garbage, not absence. {S}
- [M] A: promotion gate — `can_promote` returns False if memory_kind NULL @core/lifecycle.py; stage-gated invariant {S}  <- B+backfill (else legacy rows can't promote)
- [L] tests: classifier unit, backfill idempotency, can_promote NULL-block, migration CHECK reject {M}

## BLOCKED / OPEN ?
- ? classification taxonomy prompt — new file `core/system_prompts/classification.md`? confirm naming vs existing extraction/consolidation/curation
- ? A ordering — gate must land AFTER backfill or 335 legacy rows freeze at consolidated. Hard sequencing.
- ? CHECK constraint on existing table needs table-rebuild in SQLite (no ALTER ADD CONSTRAINT) — migration writes new table + copy

## NOTES
- B before backfill before A — A depends on both else legacy rows un-promotable  ref:this doc NEXT
- NOT schema NOT NULL — null valid for ephemeral + open_question  ref:validators.py:70
- all DB access via Peewee db singleton / db.execute_sql — never raw sqlite3.connect  ref:CLAUDE.md Rule 1
- all LLM via core.llm.call_llm  ref:CLAUDE.md Rule 2
- run gitnexus_impact before editing consolidator/lifecycle symbols  ref:CLAUDE.md GitNexus

## REFS
- code: @core/consolidator.py:994 — derive_memory_kind call site, B wiring point
- code: @core/validators.py:43 — MEMORY_KIND_VALUES frozenset (C constraint values)
- code: @core/validators.py:70 — derive_memory_kind, returns None gap
- code: @core/lifecycle.py — can_promote, A gate point
- code: @core/schemas.py:245 — ConsolidationDecision kind validator
- code: @hooks/_render.py:28 — _DEFAULT_EMOJI 🔸
- script: @scripts/backfill_enrichment_fields.py — backfill script template
- skills: /memesis:backfill — backfill runbook

## RESUME
$ cd ~/projects/memesis && uv run python3 -m pytest tests/ -q
#branch feat/single-global-db · uncommitted: 3 (hooks/_safe.py, session_start.py, user_prompt_inject.py) + untracked docs/adr/ · tests: unrun

## REHYDRATE TASKS
- [H] build classify_memory_kind LLM classifier in core || building memory_kind LLM classifier
- [H] wire classifier into consolidator when derive_memory_kind returns None || wiring classifier into consolidator
- [H] write backfill script for 335 NULL memory_kind rows || writing memory_kind backfill script
- [M] add migration 0008 CHECK constraint on memory_kind || adding memory_kind CHECK constraint migration
- [M] add can_promote gate blocking NULL memory_kind || adding can_promote NULL-memory_kind gate
- [L] add tests for classifier, backfill, gate, migration || testing memory_kind classifier and gate
