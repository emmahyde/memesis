# Compression Patterns for LLM Explore Injection

## Overview

This document analyzes the memesis memory injection and retrieval system to understand where compressed memories would be consumed, how the injection format works, token budget constraints, and how memory content is formatted for LLM context.

---

## 1. SessionStart Hook Implementation

**File:** `/Users/emmahyde/projects/memesis/hooks/session_start.py`

The SessionStart hook orchestrates memory initialization at the start of each Claude Code session:

```python
def main():
    session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
    project_context = os.getcwd()
    base_dir = init_db(project_context=project_context)

    # Seed instinctive layer on first run (idempotent)
    reflector = SelfReflector()
    reflector.ensure_instinctive_layer()

    # Ingest native Claude Code memories (deduplicates automatically)
    ingestor = NativeMemoryIngestor()
    ingestor.ingest(project_context)

    # Rehydrate archived memories relevant to this project context
    relevance = RelevanceEngine()
    relevance.rehydrate_for_context(project_context)

    # Inject memory context
    retriever = RetrievalEngine()
    injected = retriever.inject_for_session(session_id, project_context)
    create_ephemeral_buffer(base_dir)

    print(injected)
```

**Key insight:** The hook prints the injected memory context to stdout, which Claude Code captures and injects into the session context.

---

## 2. RetrievalEngine Class

**File:** `/Users/emmahyde/projects/memesis/core/retrieval.py`

The `RetrievalEngine` class (lines 65-987) implements three-tier retrieval with token budget management.

### Token Budget Calculation

```python
def __init__(self, token_budget_pct: float = 0.08):
    self.token_budget_pct = token_budget_pct
    # token_limit is in *characters* (chars/4 is the token estimate)
    self.token_limit = int(token_budget_pct * 200_000) * 4  # chars
```

- **Default:** 8% of 200K token context window
- **token_limit:** 64,000 characters (~16,000 tokens at 4 chars/token)
- **Configurable** via `token_budget_pct` parameter

### Main Injection Method

```python
def inject_for_session(
    self,
    session_id: str,
    project_context: str = None,
    query: str = None,
    query_embedding: bytes | None = None,
    session_affect: dict | None = None,
) -> str:
```

Returns the full memory context string for injection.

---

## 3. Three-Tier Retrieval System

### Tier 1 — Instinctive (Always Active)

**Method:** `get_instinctive_memories()` (lines 680-685)

```python
def get_instinctive_memories(self) -> list:
    """Return all instinctive memories with their content loaded."""
    return list(Memory.by_stage("instinctive"))
```

**Characteristics:**
- No filtering, no budget limits
- Always injected at session start
- Includes seed memories: Self-Model, Observation Habit, Compaction Guidance
- **Never expires** (T1 tier_ttl=None)

**Seeded by SelfReflector** (`core/self_reflection.py`):
- **Self-Model:** Known tendencies, failure modes, corrective behaviors (importance: 0.90)
- **Observation Habit:** Reminder to capture observations during sessions (importance: 0.85)
- **Compaction Guidance:** What to preserve when context compacts (importance: 0.80)

### Tier 2 — Crystallized (Token-Budgeted)

**Method:** `get_crystallized_for_context()` (lines 687-756)

**Static Path (no query):**
```python
records = [m for m in Memory.by_stage("crystallized") if is_injection_eligible(m)]

# Three-pass stable sort:
# 1. last_used_at (descending - most recent first)
# 2. importance (descending)
# 3. project_context match (matching projects first)
```

**Hybrid Path (with query):**
```python
ranked = self.hybrid_search(query, query_embedding, k=50, vec_store=get_vec_store())
# Then: project_context boost, affect boost, greedy token budget packing
```

**SM-2 Eligibility Check** (`core/spaced.py`):
```python
def is_injection_eligible(memory: Memory) -> bool:
    if not get_flag("sm2_spaced_injection"):
        return True
    if not memory.next_injection_due:
        return True
    due = datetime.fromisoformat(memory.next_injection_due)
    return datetime.now() >= due
```

**Token Budget Packing:**
```python
budget_remaining = token_limit
selected = []
for record in records_sorted:
    content = record.content or ""
    cost = len(content)
    if cost <= budget_remaining:
        selected.append(record)
        budget_remaining -= cost
```

### Tier 3 — Agent-Initiated (On-Demand)

**Method:** `active_search()` (lines 199-266)

```python
def active_search(self, query: str, session_id: str, limit: int = 10) -> list[dict]:
    # Uses hybrid RRF (FTS + vector) via /memesis:recall
    ranked = self.hybrid_search(query, query_embedding, k=limit, vec_store=get_vec_store())
    # Returns progressive-disclosure dicts with rank, importance, tags, etc.
```

---

## 4. MEMORY CONTEXT Block Format

**File:** `/Users/emmahyde/projects/memesis/core/retrieval.py` (lines 133-197)

The `---MEMORY CONTEXT---` block is built in `inject_for_session()`:

```python
sections = ["---MEMORY CONTEXT---", ""]

# Tier 1 — Instinctive (behavioral guidelines)
if tier1:
    sections.append("## Your Behavioral Guidelines (always active)")
    for memory in tier1:
        sections.append("")
        title = memory.title or "Guideline"
        sections.append(f"### {title}")
        content = (memory.content or "").strip()
        if content:
            sections.append(content)

# Tier 2 — Crystallized (context-relevant knowledge)
if tier2:
    if get_flag("provenance_signals"):
        provenance_map = self._compute_provenance_batch([m.id for m in tier2])
    
    sections.append("")
    sections.append("## Context-Relevant Knowledge")
    for memory in tier2:
        sections.append("")
        title = memory.title or "Memory"
        importance = memory.importance or 0.5
        sections.append(f"### {title} (importance: {importance:.2f})")
        if memory.id in provenance_map:
            sections.append(f"*{provenance_map[memory.id]}*")
        summary = (memory.summary or "").strip()
        if summary:
            sections.append(f"*{summary}*")
        content = (memory.content or "").strip()
        if content:
            sections.append(content)

# Tier 2.5 — Narrative threads
thread_narratives = self._get_thread_narratives(tier2, session_affect=session_affect)
if thread_narratives:
    sections.append("")
    sections.append("## Narrative Threads (how understanding evolved)")
    for thread in thread_narratives:
        sections.append("")
        title = thread.title or "Thread"
        sections.append(f"### {title}")
        narrative = (thread.narrative or "").strip()
        if narrative:
            sections.append(narrative)

# Tier 2.6 — Active Tensions
if get_flag("contradiction_tensors") and tier2:
    tension_blocks = self._get_active_tensions(tier2)
    if tension_blocks:
        sections.append("")
        sections.append("## Active Tensions (conflicting memories)")

sections.append("")
sections.append("---END MEMORY CONTEXT---")
```

### Example Output Structure

```
---MEMORY CONTEXT---

## Your Behavioral Guidelines (always active)

### Self-Model
[content with frontmatter]

### Observation Habit
[content]

### Compaction Guidance
[content]

## Context-Relevant Knowledge

### Python Style (importance: 0.85)
*Established across 5 sessions over 2 weeks*
*Python idioms and conventions*
[content]

### Docker Best Practices (importance: 0.72)
*First observed 3 days ago*
*Use --no-install-recommends in Dockerfiles*
[content]

## Narrative Threads (how understanding evolved)

### API Design Evolution
[thread narrative content]

## Active Tensions (conflicting memories)

### Tension
Position A: Use PostgreSQL for this project: scalability...
Position B: Use SQLite for this project: simplicity...

---END MEMORY CONTEXT---
```

---

## 5. Token Budget Constraints

### Budget Calculation

| Parameter | Value |
|-----------|-------|
| Context window | 200,000 tokens |
| Default budget % | 8% |
| Default token budget | 16,000 tokens |
| Character limit | 64,000 chars (200,000 * 4 * 0.08) |

### Budget Allocation

1. **Tier 1 (Instinctive):** No budget limit — all instinctive memories are included
2. **Tier 2 (Crystallized):** Greedy packing within remaining budget
3. **Tier 2.5 (Narrative Threads):** Separate 8,000 char budget (`THREAD_BUDGET_CHARS`)
4. **Tier 2.6 (Active Tensions):** Separate 2,000 char budget (`TENSION_BUDGET_CHARS`)

### Greedy Packing Algorithm

```python
budget_remaining = token_limit
selected = []
for memory in ranked_memories:
    content = memory.content or ""
    cost = len(content)
    if cost <= budget_remaining:
        selected.append(memory)
        budget_remaining -= cost
```

### Factors Affecting Selection

1. **Importance score** — higher importance memories selected first
2. **Project context match** — memories matching current project get priority
3. **Recency** — recently-used memories ranked higher
4. **SM-2 schedule** — memories with `next_injection_due` in future are skipped
5. **Affect valence** — friction memories get a 0.02 boost (`AFFECT_FRICTION_BOOST`)
6. **Thompson sampling** — stochastic reranking when flag enabled

---

## 6. Where Compressed Memories Would Be Consumed

### Entry Point: `inject_for_session()`

**File:** `/Users/emmahyde/projects/memesis/core/retrieval.py` (lines 93-197)

Compressed memories would be consumed at:

1. **Line 108-109:** Tier 1 retrieval
   ```python
   tier1 = self.get_instinctive_memories()
   ```

2. **Line 109-114:** Tier 2 retrieval
   ```python
   tier2 = self.get_crystallized_for_context(
       project_context=project_context,
       token_limit=self.token_limit,
       query=query,
       query_embedding=query_embedding,
   )
   ```

3. **Line 170:** Narrative thread retrieval
   ```python
   thread_narratives = self._get_thread_narratives(tier2, session_affect=session_affect)
   ```

4. **Line 184:** Active tensions retrieval
   ```python
   tension_blocks = self._get_active_tensions(tier2)
   ```

### Memory Model Fields Used

**File:** `/Users/emmahyde/projects/memesis/core/models.py` (lines 77-146)

| Field | Usage |
|-------|-------|
| `title` | Section headers in MEMORY CONTEXT block |
| `summary` | Italicized preview in Tier 2 section |
| `content` | Full content injected into context |
| `importance` | Displayed in section header, affects sorting |
| `stage` | Determines tier (instinctive → T1, crystallized → T2) |
| `project_context` | Project-matching boost |
| `affect_valence` | Friction boost |
| `next_injection_due` | SM-2 eligibility check |
| `last_used_at` | Recency sorting |

### Injection Flow

```
SessionStart Hook
    ↓
RetrievalEngine.inject_for_session()
    ├── get_instinctive_memories() → Tier 1 (no limit)
    ├── get_crystallized_for_context() → Tier 2 (token budget)
    │   └── is_injection_eligible() → SM-2 check
    ├── _get_thread_narratives() → Tier 2.5 (8K char budget)
    └── _get_active_tensions() → Tier 2.6 (2K char budget)
    ↓
Format into MEMORY CONTEXT block
    ↓
Print to stdout → Claude Code captures as context
```

---

## 7. Key Files Reference

| File | Purpose |
|------|---------|
| `hooks/session_start.py` | SessionStart hook entry point |
| `core/retrieval.py` | RetrievalEngine, three-tier retrieval, MEMORY CONTEXT formatting |
| `core/tiers.py` | Tier definitions, TTL, decay constants |
| `core/spaced.py` | SM-2 spaced injection scheduling |
| `core/self_reflection.py` | Instinctive layer seeding (Self-Model, etc.) |
| `core/models.py` | Memory model, FTS search |
| `core/flags.py` | Feature flags |
| `hooks/user_prompt_inject.py` | Just-in-time memory injection on user prompts |

---

## 8. Configuration

**File:** `.claude-plugin` under `config` key

| Key | Default | Description |
|-----|---------|-------------|
| `token_budget_pct` | `0.08` | Fraction of context window for Tier-2 memories |
| `stale_session_threshold` | `30` | Days before ephemeral memory is deprecation candidate |

---

## 9. Key Insights for Compression

1. **Tier 1 is uncapped** — all instinctive memories are always injected
2. **Tier 2 uses greedy packing** — content length is the primary cost metric
3. **Summary field is used for preview** — content may not be shown if summary exists
4. **Provenance signals add overhead** — "*Established across N sessions*" adds ~50 chars
5. **Narrative threads and tensions have separate budgets** — 8K + 2K chars
6. **SM-2 can suppress injection** — `next_injection_due` in future skips memory
7. **Memory content includes frontmatter** — instinctive memories store YAML frontmatter in content field
