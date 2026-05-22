---
title: Memesis Wiki
type: index
---

# Memesis Wiki

Knowledge base for the memesis self-driven memory lifecycle plugin. Audience: future-Claude sessions orienting to the system.

## Sources

| Source | Date | Summary |
|---|---|---|
| [[memesis-system-status-2026-05-19]] | 2026-05-19 | Full operational status of all major subsystems: lifecycle, gates, importance rubric, storage, transport, memory_kind taxonomy, cron |

## Entities

See [[entities/_index]] for the full list.

| Entity | Description |
|---|---|
| [[LifecycleManager]] | Stage transition logic and gate checks |
| [[Crystallizer]] | LLM synthesis engine for consolidated → crystallized |
| [[SelfReflector]] | Experimental hypothesis accumulation and self-model maintenance |

## Concepts

See [[concepts/_index]] for the full list.

| Concept | Description |
|---|---|
| [[memory-lifecycle]] | Four-stage progression and demotion/deprecation rules |
| [[promotion-gates]] | Gate conditions at each stage boundary |
| [[importance-rubric]] | Five-band scoring rubric; thresholds 0.75 and 0.85 |
| [[memory-kind-taxonomy]] | Curated 10-value enum enforced by DB triggers |

## Key Operational Facts

- Global DB: `~/.claude/memory/index.db` (single store, project identity via `project` column)
- Transport: claude-agent-sdk (preferred) → `claude -p` subprocess fallback; Bedrock removed
- Default model: `claude-sonnet-4-6`
- Cron: consolidate-cron at :07/hour + transcript-cron every 15 min; both currently unloaded
- Crystallize batch cap: 10/tick (`MEMESIS_CRYSTALLIZE_BATCH_LIMIT`)
- Importance thresholds: 0.75 → crystallize, 0.85 + 10 sessions → instinctive
- `memory_kind` NULL valid at ephemeral; required before crystallization (except open_question)
