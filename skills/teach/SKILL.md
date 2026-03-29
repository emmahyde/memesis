---
name: teach
description: This skill should be used when the user says "teach you about", "document our process", "explain how X works", "walk you through our system", or provides multi-part structured knowledge — processes, architectures, workflows, or any topic that has distinct logical components. Does NOT trigger for single isolated facts, corrections, or preferences — use /learn for those.
---

# Teach — Decompose and Store Structured Knowledge

Store multi-part structured knowledge by decomposing it into logical components. Each component becomes a linked Memory record. Use this when the knowledge has inherent structure — steps in a process, layers in an architecture, phases in a workflow — where a single monolithic record would lose the navigable parts.

## Usage

```
/memesis:teach [content]
/memesis:teach our deployment process goes through three stages: build, promote, and release, each with distinct gate checks
/memesis:teach [architecture] the frontend, API gateway, and worker tier each have separate scaling policies
/memesis:teach explain how our feature flag system works — it has a config layer, an evaluation engine, and a targeting DSL
```

## Procedure

1. **Read the full input** before decomposing. Identify the natural logical boundaries — steps, layers, phases, components, concerns. Do not force a split if the knowledge is genuinely atomic (use /learn instead).

2. **Decompose into 2–8 parts.** For each part assign:
   - A short, specific **title** (under 80 chars)
   - A one-line **summary** (under 150 chars) capturing the part's core role
   - **Content** — the full substance of that part with enough detail to be useful in isolation

   The decomposition structure is at Claude's discretion per input. Parts should be coherent on their own but also meaningful as a set.

3. **Derive a slug** from the topic — lowercase, hyphenated, no punctuation (e.g., `deployment-process`, `feature-flag-system`). This slug becomes the linking tag `teach:<slug>` shared by every part.

4. **Write each part** as a `Memory.create()` call with:
   - `stage='consolidated'` — structured knowledge is write-once into consolidated, not queued for consolidation review
   - `tags` including `type:domain_knowledge`, `teach:<slug>`, and `part:<N>-of-<M>` for ordering
   - `importance=0.7` — structured knowledge has high recall value by default

5. **Confirm** with a summary listing:
   - The linking tag used (`teach:<slug>`)
   - Each part: memory ID, positional tag, and title

## Implementation

```python
import os, sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}")
from core.database import init_db
from core.models import Memory
init_db(project_context=os.getcwd())

import json
from datetime import datetime

# Claude fills in these values after decomposing the input.
# slug: lowercase-hyphenated identifier for the topic
# parts: list of dicts with title, summary, content
slug = "deployment-process"
parts = [
    {
        "title": "Build stage — compile and package",
        "summary": "CI compiles, runs unit tests, and produces a versioned artifact",
        "content": "...",
    },
    # ... additional parts ...
]

total = len(parts)
created = []

for i, part in enumerate(parts, start=1):
    memory = Memory.create(
        stage="consolidated",
        title=part["title"],
        summary=part["summary"],
        content=part["content"],
        tags=json.dumps([
            "type:domain_knowledge",
            f"teach:{slug}",
            f"part:{i}-of-{total}",
        ]),
        importance=0.7,
        project_context=os.getcwd(),
        source_session=os.environ.get("CLAUDE_SESSION_ID", "unknown"),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    created.append((memory.id, part["title"]))
    print(f"[{i}/{total}] Stored: {memory.id} — {part['title']}")

print(f"\nAll parts linked by tag: teach:{slug}")
```

## Examples

**Procedural — deployment process**
```
/memesis:teach our deployment process: (1) Build — CI compiles, runs tests, produces a versioned Docker image; (2) Promote — image is pushed to staging and smoke tests run against it; (3) Release — after manual approval, Helm upgrades the production release and a canary monitor watches error rates for 10 minutes before full rollout
```
Decomposed into 3 parts. Slug: `deployment-process`. Tags per part: `type:domain_knowledge`, `teach:deployment-process`, `part:1-of-3` / `part:2-of-3` / `part:3-of-3`.

Confirmation:
```
Stored 3 memories linked by teach:deployment-process

  part:1-of-3  <id-a>  Build stage — CI compiles and produces versioned image
  part:2-of-3  <id-b>  Promote stage — staging deploy with smoke tests
  part:3-of-3  <id-c>  Release stage — Helm upgrade with canary monitor
```

**Conceptual — system architecture**
```
/memesis:teach explain how our event pipeline works: the ingestion layer accepts webhook payloads and writes to a Kafka topic; the processor service consumes from Kafka, validates and enriches events, then writes to Postgres; the query layer serves a read-optimized view built from Postgres via a materialized projection updated on a 30-second schedule
```
Decomposed into 3 parts. Slug: `event-pipeline`. Tags per part: `type:domain_knowledge`, `teach:event-pipeline`, `part:1-of-3` / `part:2-of-3` / `part:3-of-3`.

Confirmation:
```
Stored 3 memories linked by teach:event-pipeline

  part:1-of-3  <id-a>  Ingestion layer — webhook payloads to Kafka topic
  part:2-of-3  <id-b>  Processor service — validation, enrichment, Postgres write
  part:3-of-3  <id-c>  Query layer — materialized projection with 30s refresh
```

**Note on decomposition:** Claude decides the part boundaries. A four-step process becomes four parts. A system with two concerns becomes two parts. The only rule is that each part should be coherent on its own and that the full set covers the topic without duplication.
