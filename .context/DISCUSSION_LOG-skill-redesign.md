# Discussion Log: Redesign memesis skills

> **Audit trail only.** Do not use as input to implementation agents.
> Decisions are captured in CONTEXT-skill-redesign.md — this log preserves the alternatives considered.

**Date:** 2026-03-29
**Work:** Redesign and expand memesis plugin skills
**Areas discussed:** Skill scope, /learn write path, /learn classification, /recall UX, /reflect flow, /memory focus, API target, skill shape, additional skills

---

## Skill Scope

| Option | Description | Selected |
| --- | --- | --- |
| Redesign /learn | Rethink as direct-write with auto-classification | ✓ |
| Add /recall | Search memories by topic with FTS + semantic | ✓ |
| Add /reflect | On-demand self-reflection | ✓ |
| Redesign /memory | Richer dashboard with health, threads, usage | ✓ |

**User's choice:** All four
**Notes:** None

---

## /learn Write Path

| Option | Description | Selected |
| --- | --- | --- |
| Direct write | Bypass consolidation, write to consolidated immediately | |
| Ephemeral + priority flag | Write to buffer with always-keep flag | |
| Two-tier | Simple facts go direct, complex observations go to buffer | ✓ |

**User's choice:** Two-tier with user choice on ambiguity
**Notes:** "Present the user with a choice if there is any uncertainty about if its a simple fact or not."

---

## /learn Classification

| Option | Description | Selected |
| --- | --- | --- |
| LLM classification | Extra API call for type/tags | |
| Heuristic + LLM fallback | Pattern-match then fall back | |
| Claude does it inline | Session Claude classifies before writing | ✓ |

**User's choice:** Claude does it inline
**Notes:** None

---

## /recall Output UX

| Option | Description | Selected |
| --- | --- | --- |
| Ranked list with scores | Show title, summary, score, stage | |
| Conversational summary | Natural synthesis with citations | |
| Both modes | Default conversational, --detail for ranked list | ✓ |

**User's choice:** Both modes
**Notes:** None

---

## /reflect Flow

| Option | Description | Selected |
| --- | --- | --- |
| Preview then apply | Show findings, let user approve | ✓ |
| Apply then report | Apply updates, show what changed | |
| You decide | Claude picks approach | |

**User's choice:** Preview then apply
**Notes:** None

---

## /memory Focus Areas

| Option | Description | Selected |
| --- | --- | --- |
| Relevance health | Archival threshold, stale, rehydration | ✓ |
| Thread visualization | Narrative threads with evolution arcs | ✓ |
| Usage analytics | Injection counts, usage rates, trends | ✓ |
| Cross-project view | Shared memories, project distribution | ✓ |

**User's choice:** All four
**Notes:** None

---

## API Target

| Option | Description | Selected |
| --- | --- | --- |
| Peewee models | Target new API, ship with migration | ✓ |
| Current API, migrate later | Ship now against MemoryStore | |
| Abstraction layer | Thin interface for both backends | |

**User's choice:** Peewee models
**Notes:** None

---

## Skill Shape

| Option | Description | Selected |
| --- | --- | --- |
| Subcommands under /memory | One skill, multiple modes | |
| Separate skills | Each is its own focused skill | ✓ |
| Hybrid | /memory for dashboard, subcommands for views | |

**User's choice:** Separate skills
**Notes:** None

---

## Additional Skills

| Option | Description | Selected |
| --- | --- | --- |
| Just these four | Focus on learn, recall, reflect, memory | |
| Add /teach | Structured multi-part knowledge creation | ✓ |
| Add /connect | Manually link memories into threads | ✓ |

**User's choice:** Add both /teach and /connect
**Notes:** None

---

## Claude's Discretion

- Exact prompt wording and examples in each SKILL.md
- How /teach decomposes knowledge into parts
- Whether /reflect shows diff-style or narrative-style preview

## Deferred Ideas

- /forget redesign — revisit after Peewee migration
- /backfill redesign — operational, low priority
- Skill-to-skill chaining (e.g., /teach → /connect)
