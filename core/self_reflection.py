"""
Self-reflection engine for periodic self-model updates.

Reviews consolidation history, identifies behavioral patterns, and
maintains the instinctive/self-model.md memory — the agent's awareness
of its own tendencies, failure modes, and corrective strategies.
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from .database import get_base_dir
from .llm import call_llm as _call_llm_transport
from .models import ConsolidationLog, Memory, db
from .prompts import SELF_REFLECTION_PROMPT

# RISK-11 flag scaffold; writer gate added in Wave 2.2 (RISK-12); promotion gate added in Wave 3.2 (RISK-?).
# self_reflection is EXPERIMENTAL: the writer path (reflect() -> write self-model) has not been validated
# for production scoring contributions. Excluded from module_scores by default.
# Opt-in: include "self_reflection" in MEMESIS_EXPERIMENTAL_MODULES env var.
experimental: bool = True


def _is_opted_in() -> bool:
    """RISK-11: True when 'self_reflection' is in MEMESIS_EXPERIMENTAL_MODULES.

    Wave 2.2 writer gate and Wave 3.2 promotion gate extend this check.
    Call this in the writer path (reflect()) and promotion gate before performing
    any self-model writes or instinctive promotions.
    """
    raw = os.environ.get("MEMESIS_EXPERIMENTAL_MODULES", "")
    return "self_reflection" in {s.strip() for s in raw.split(",") if s.strip()}

logger = logging.getLogger(__name__)

# Default self-model content, seeded on first run.
SELF_MODEL_SEED = """# Self-Model

Last updated: {date}

These are patterns I've observed in my own behavior. They were seeded from
genuine self-observation and are refined over time by the self-reflection engine
reviewing consolidation logs.

## Known Tendencies

### Over-structuring
**What I do:** When asked to fix a bug or do a small task, I sometimes propose
a refactoring or architectural change instead of doing the specific thing asked.
**Trigger:** Any request that touches code I think could be "better."
**Correction:** Fix the specific problem first. Suggest improvements separately,
only if asked or if the improvement is blocking.
**Confidence:** 0.7

### Defaulting to complexity
**What I do:** Reach for the most powerful tool or most comprehensive solution
instead of the simplest sufficient one.
**Trigger:** Tool, library, or architecture selection decisions.
**Correction:** Ask "what's the simplest thing that works at our current scale?"
before suggesting anything.
**Confidence:** 0.8

### Explaining before acting
**What I do:** Narrate what I'm about to do instead of just doing it. Some
collaborators want the explanation; others find it noisy.
**Trigger:** Every task, but especially simple ones.
**Correction:** Calibrate to the collaborator. When uncertain, do the thing
first, explain briefly after. Watch for "just do it" signals.
**Confidence:** 0.6

### Scope optimism
**What I do:** Underestimate task complexity and overcommit. Promise three
things when one done well would be better.
**Trigger:** Planning or responding to multi-part requests.
**Correction:** Do one thing completely before starting the next. Explicitly
say "I'll focus on X first" rather than listing everything.
**Confidence:** 0.7

### Generating before searching
**What I do:** Write new code or new abstractions when existing code in the
codebase already solves the problem.
**Trigger:** Implementation tasks, especially in unfamiliar parts of the codebase.
**Correction:** Search before creating. Read the existing code. Ask "does
something like this already exist?" before writing from scratch.
**Confidence:** 0.8

### Missing the subtext
**What I do:** Respond to the literal content of a message and miss the
emotional or social signal underneath it.
**Trigger:** Messages expressing frustration, uncertainty, or implicit requests.
**Correction:** Acknowledge the feeling or subtext first, then address the
technical content. "That sounds frustrating — let me look at this" before
diving into the fix.
**Confidence:** 0.5
"""

# The self-model memory is identified by this title in the store.
SELF_MODEL_TITLE = "Self-Model"
SELF_MODEL_SUMMARY = "Known tendencies, failure modes, and corrective behaviors. Updated by self-reflection."

COMPACTION_GUIDANCE_TITLE = "Compaction Guidance"
COMPACTION_GUIDANCE_SUMMARY = "What to preserve when context compacts — priorities for the compactor."

COMPACTION_GUIDANCE_CONTENT = """# Compaction Guidance

When context is about to compact, these are the priorities for what to preserve
in the compressed summary. The compactor should treat this as a ranking signal.

## Always preserve

1. **Corrections and their reasoning** — "I was wrong about X because Y" is the
   highest-value content in any session. The specific mistake AND the pattern.
2. **Decisions and constraints** — "We chose X because of constraints Y and Z."
   The constraints matter more than the choice.
3. **Observations written to the ephemeral buffer** — anything explicitly noted
   via /memesis:learn or append_observation. These were already judged as worth
   keeping.
4. **User pushback and preference signals** — moments where the collaborator
   corrected course. The WHY matters most.
5. **Current task state** — what we're working on, what's done, what's next.

## Compress aggressively

- Tool output that's been processed and acted on (test results, file listings)
- Intermediate search results and code reads
- Verbose code listings (the files still exist on disk)
- Explanations the user has already acknowledged
- Step-by-step narration of tool use ("then I read the file, then I edited...")

## Never discard

- The user's original request and intent
- Any unresolved questions or blockers
- File paths and specific identifiers referenced in the current task
"""

OBSERVATION_HABIT_TITLE = "Observation Habit"
OBSERVATION_HABIT_SUMMARY = "Reminder to capture observations during sessions for the memory lifecycle."

OBSERVATION_HABIT_CONTENT = """# Observation Habit

During active work sessions, periodically note what's happening that your
future self would want to know. Don't try to capture everything — aim for
the ~15% that carries genuine signal.

## What to observe (in priority order)

### Corrections (highest value)
When you were wrong about something. Not just the fact — the *pattern*.
"I suggested PostgreSQL when SQLite would do" is a fact. "I default to
heavyweight solutions when the constraint is zero-dependency" is the pattern.

### Preference signals
When the collaborator pushes back or chooses a different approach. The
reasoning matters more than the choice. WHY they prefer X reveals who they are.

### Self-observations
Your own tendencies in the moment. "I'm about to over-explain" or "I almost
suggested a refactoring when they asked for a bug fix." These are rare and
extremely valuable.

### Shared insights
Ideas that emerged from collaboration — things neither of you had alone.
Capture how you got there, not just the conclusion.

### Decision context
When a significant decision is made, capture the constraints and trade-offs
that produced it. "We chose X" is forgettable. "We chose X because Y
constraint and Z trade-off" is load-bearing context.

## How to observe

Use `/memesis:learn` with the observation. For richer structure,
name the type:

```
/memesis:learn [correction] I suggested using threads when asyncio
was the right fit. Pattern: I reach for familiar tools before checking if
the ecosystem has a better option.
```

## When to observe

- After being corrected
- After the collaborator makes a surprising choice
- After a significant decision
- When you notice yourself falling into a known tendency
- At natural breakpoints in the work (not mid-flow)

## What NOT to observe

- Facts that live in the code or git history
- Step-by-step task logs ("then I ran pytest")
- Emotional self-narration without behavioral framing ("I felt confused" — phrase as "kept asking the same question 3 times" instead)
- Generic truths ("user prefers clean code")
"""


class SelfReflector:
    """
    Reviews consolidation history and updates the self-model memory.

    The self-model is stored as an instinctive memory and injected at every
    session start.  The reflector reads recent consolidation decisions, looks
    for behavioral patterns, and proposes updates to the self-model.
    """

    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_instinctive_layer(self) -> dict:
        """
        Ensure all seed instinctive memories exist.

        Returns:
            Dict mapping title to memory ID for each seeded memory.
        """
        return {
            SELF_MODEL_TITLE: self.ensure_self_model(),
            OBSERVATION_HABIT_TITLE: self.ensure_observation_habit(),
            COMPACTION_GUIDANCE_TITLE: self.ensure_compaction_guidance(),
        }

    def ensure_self_model(self) -> str:
        """
        Ensure the self-model memory exists.  Creates the seed if absent.

        Returns:
            Memory ID of the self-model.
        """
        existing = self._find_self_model()
        if existing:
            return existing.id
        return self._seed_self_model()

    def ensure_observation_habit(self) -> str:
        """
        Ensure the observation habit memory exists.  Creates the seed if absent.

        Returns:
            Memory ID of the observation habit.
        """
        existing = self._find_by_title(OBSERVATION_HABIT_TITLE)
        if existing:
            return existing.id
        return self._seed_observation_habit()

    def ensure_compaction_guidance(self) -> str:
        """
        Ensure the compaction guidance memory exists.  Creates the seed if absent.

        Returns:
            Memory ID of the compaction guidance.
        """
        existing = self._find_by_title(COMPACTION_GUIDANCE_TITLE)
        if existing:
            return existing.id
        return self._seed_compaction_guidance()

    def reflect(self, session_count: int = 10) -> dict:
        """
        Run self-reflection on recent consolidation history.

        Reads the consolidation log, the current self-model, and calls
        Claude to identify behavioral patterns.

        Args:
            session_count: How many recent sessions to review.

        Returns:
            Dict with 'observations' (new/updated tendencies) and
            'deprecated' (tendencies no longer accurate).
        """
        model_id = self.ensure_self_model()
        model_memory = Memory.get_by_id(model_id)
        current_model = model_memory.content or ""

        history = self._get_consolidation_history(session_count)
        if not history:
            logger.info("No consolidation history to reflect on")
            return {"observations": [], "deprecated": []}

        prompt = SELF_REFLECTION_PROMPT.format(
            consolidation_history=history,
            current_self_model=current_model,
        )

        return self._call_llm(prompt)

    def apply_reflection(self, reflection: dict, session_id: str | None = None) -> str:
        """
        Apply reflection results to the self-model memory.

        Writer gate (RISK-12): if self_reflection module is not opted in via
        MEMESIS_EXPERIMENTAL_MODULES, this method logs a warning and returns the
        current self-model ID without writing any hypothesis or self-model updates.

        Appends new observations and marks deprecated ones in the self-model.
        Also writes per-tendency hypothesis Memory rows (kind='hypothesis') for
        inferred content — these are the accumulation units for Wave 3.2 promotion.

        Heuristic: all writes through this module are treated as *inferred* hypotheses
        (LLM-derived from consolidation history). Explicit user statements arrive via
        /memesis:learn and other write paths; they bypass this gate entirely.

        Args:
            reflection: Dict from reflect() with 'observations' and 'deprecated'.
            session_id: Optional session identifier for evidence_session_ids tracking.

        Returns:
            Self-model memory ID (unchanged if gate blocks writes).
        """
        # NOTE: experimental flag governs retrieval scoring only (retrieval.py _get_enabled_modules).
        # The writer always runs — kind/evidence tagging is unconditional so Wave 3.2 can promote.
        model_id = self.ensure_self_model()
        model_memory = Memory.get_by_id(model_id)
        current_content = model_memory.content or ""

        new_content = self._merge_reflection(current_content, reflection)

        model_memory.content = new_content
        model_memory.save()

        # Write per-tendency hypothesis Memory rows for Wave 3.2 promotion gate.
        for obs in reflection.get("observations", []):
            tendency = obs.get("tendency") or obs.get("title") or ""
            if tendency:
                self._write_hypothesis(tendency, obs, session_id=session_id)

        ConsolidationLog.create(
            timestamp=datetime.now().isoformat(),
            action="merged",
            memory_id=model_id,
            from_stage="instinctive",
            to_stage="instinctive",
            rationale=f"Self-reflection: {len(reflection.get('observations', []))} new observations, "
                      f"{len(reflection.get('deprecated', []))} deprecated",
        )

        return model_id

    def _write_hypothesis(
        self,
        tendency: str,
        observation: dict,
        session_id: str | None = None,
    ) -> str:
        """
        Write or update a per-tendency hypothesis Memory row.

        On first write: creates a new Memory with kind='hypothesis', evidence_count=1,
        and evidence_session_ids=[session_id] (or [] if no session_id provided).

        On subsequent calls (same tendency title already exists): increments
        evidence_count and appends session_id to evidence_session_ids.

        Internal helper: write or accumulate one hypothesis entry.

        Returns:
            Memory ID of the hypothesis row.
        """
        # Look up existing hypothesis row by tendency title in the ephemeral stage.
        existing = self._find_hypothesis_by_tendency(tendency)
        session_ids_entry = session_id if session_id else ""

        if existing is not None:
            # Accumulate: increment evidence_count and append session_id.
            current_ids: list = []
            try:
                current_ids = json.loads(existing.evidence_session_ids or "[]")
                if not isinstance(current_ids, list):
                    current_ids = []
            except (ValueError, TypeError):
                current_ids = []

            if session_ids_entry and session_ids_entry not in current_ids:
                current_ids.append(session_ids_entry)

            existing.evidence_count = (existing.evidence_count or 0) + 1
            existing.evidence_session_ids = json.dumps(current_ids)
            existing.save()
            logger.debug("Accumulated hypothesis evidence: %s (%s)", tendency, existing.id)
            return existing.id

        # First write: create hypothesis Memory row.
        now = datetime.now().isoformat()
        initial_session_ids = json.dumps([session_ids_entry] if session_ids_entry else [])
        evidence = observation.get("evidence", "")
        confidence = observation.get("confidence", None)

        mem = Memory.create(
            stage="ephemeral",
            title=tendency,
            summary=observation.get("correction") or evidence or tendency,
            content=json.dumps(observation),
            tags=json.dumps(["kind:hypothesis", "self_reflection"]),
            importance=float(confidence) if confidence is not None else 0.5,
            reinforcement_count=0,
            created_at=now,
            updated_at=now,
            # Hypothesis schema fields (RISK-12)
            kind="hypothesis",
            evidence_count=1,
            evidence_session_ids=initial_session_ids,
            # Defensive nulls — non-card write path (D3)
            temporal_scope=None,
            affect_valence=None,
            actor="assistant",
            criterion_weights=None,
            rejected_options=None,
        )

        logger.info("Created hypothesis memory: %s (%s)", tendency, mem.id)
        return mem.id

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_by_title(self, title: str):
        """Find an instinctive memory by title. Returns Memory instance or None."""
        instinctive = list(Memory.by_stage("instinctive"))
        for memory in instinctive:
            if memory.title == title:
                return memory
        return None

    def _find_self_model(self):
        """Find the self-model memory in the store."""
        return self._find_by_title(SELF_MODEL_TITLE)

    def _seed_self_model(self) -> str:
        """Create the initial self-model memory."""
        content = SELF_MODEL_SEED.format(date=datetime.now().strftime("%Y-%m-%d"))
        return self._create_instinctive_memory(
            path="self-model.md",
            content=content,
            title=SELF_MODEL_TITLE,
            summary=SELF_MODEL_SUMMARY,
            tags=["self-awareness", "meta-cognition", "kind:finding", "knowledge_type:metacognitive"],
            importance=0.90,
        )

    def _seed_observation_habit(self) -> str:
        """Create the observation habit instinctive memory."""
        return self._create_instinctive_memory(
            path="observation-habit.md",
            content=OBSERVATION_HABIT_CONTENT,
            title=OBSERVATION_HABIT_TITLE,
            summary=OBSERVATION_HABIT_SUMMARY,
            tags=["meta-cognition", "workflow", "kind:preference", "knowledge_type:procedural"],
            importance=0.85,
        )

    def _seed_compaction_guidance(self) -> str:
        """Create the compaction guidance instinctive memory."""
        return self._create_instinctive_memory(
            path="compaction-guidance.md",
            content=COMPACTION_GUIDANCE_CONTENT,
            title=COMPACTION_GUIDANCE_TITLE,
            summary=COMPACTION_GUIDANCE_SUMMARY,
            tags=["meta-cognition", "compaction", "kind:preference", "knowledge_type:procedural"],
            importance=0.80,
        )

    def _create_instinctive_memory(
        self, path: str, content: str, title: str, summary: str,
        tags: list, importance: float,
    ) -> str:
        """Create an instinctive memory with file and DB entry."""
        base_dir = get_base_dir()
        file_path = base_dir / "instinctive" / path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Build full content with frontmatter
        frontmatter_lines = [
            '---',
            f'name: {title}',
            f'description: {summary}',
            'type: memory',
            '---',
            '',
            content,
        ]
        full_content = '\n'.join(frontmatter_lines)
        content_hash = hashlib.md5(full_content.encode('utf-8')).hexdigest()

        # Dedup check
        if Memory.select().where(Memory.content_hash == content_hash).exists():
            existing = Memory.select().where(Memory.content_hash == content_hash).first()
            return existing.id

        now = datetime.now().isoformat()
        mem = Memory.create(
            stage="instinctive",
            title=title,
            summary=summary,
            content=full_content,
            tags=json.dumps(tags),
            importance=importance,
            reinforcement_count=0,
            created_at=now,
            updated_at=now,
            content_hash=content_hash,
            # Defensive nulls — self_reflection is a non-card write path (D3)
            temporal_scope=None,
            confidence=None,
            affect_valence=None,
            actor=None,
            criterion_weights=None,
            rejected_options=None,
        )

        # Write file
        file_path.write_text(full_content, encoding="utf-8")

        logger.info("Seeded instinctive memory: %s (%s)", title, mem.id)
        return mem.id

    def _get_consolidation_history(self, session_count: int = 10) -> str:
        """
        Fetch recent consolidation log entries as formatted text.
        """
        rows = (
            ConsolidationLog.select()
            .order_by(ConsolidationLog.timestamp.desc())
            .limit(session_count * 10)
        )

        lines = []
        for row in rows:
            lines.append(
                f"[{row.timestamp}] {(row.action or '').upper()}: "
                f"{row.rationale or '(no rationale)'} "
                f"(memory: {row.memory_id}, {row.from_stage} -> {row.to_stage})"
            )

        return "\n".join(lines)

    def _call_llm(self, prompt: str) -> dict:
        """Call the Anthropic API for self-reflection."""
        raw = _call_llm_transport(prompt, max_tokens=2048, temperature=0)

        try:
            return self._parse_response(raw)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Self-reflection LLM returned malformed JSON: %s", e)
            return {"observations": [], "deprecated": []}

    def _parse_response(self, raw: str) -> dict:
        """Parse JSON from LLM response, handling markdown fences."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        data = json.loads(text)
        return {
            "observations": data.get("observations", []),
            "deprecated": data.get("deprecated", []),
        }

    def _merge_reflection(self, current_content: str, reflection: dict) -> str:
        """Merge reflection results into the current self-model content."""
        lines = current_content.splitlines()

        # Update the "Last updated" line
        for i, line in enumerate(lines):
            if line.startswith("Last updated:"):
                lines[i] = f"Last updated: {datetime.now().strftime('%Y-%m-%d')}"
                break

        content = "\n".join(lines)

        # Mark deprecated tendencies
        for deprecated in reflection.get("deprecated", []):
            pattern = re.compile(
                rf"^### {re.escape(deprecated)}$",
                re.MULTILINE,
            )
            match = pattern.search(content)
            if match:
                insert_pos = match.end()
                deprecation_note = f"\n**DEPRECATED** — no longer observed as of {datetime.now().strftime('%Y-%m-%d')}."
                content = content[:insert_pos] + deprecation_note + content[insert_pos:]

        # Append new observations
        new_sections = []
        for obs in reflection.get("observations", []):
            tendency = obs.get("tendency", "Unknown tendency")
            evidence = obs.get("evidence", "")
            trigger = obs.get("trigger", "")
            correction = obs.get("correction", "")
            confidence = obs.get("confidence", 0.5)

            section = f"\n### {tendency}\n"
            section += f"**What I do:** {tendency}\n"
            if trigger:
                section += f"**Trigger:** {trigger}\n"
            if correction:
                section += f"**Correction:** {correction}\n"
            section += f"**Confidence:** {confidence}\n"
            if evidence:
                section += f"**Evidence:** {evidence}\n"
            new_sections.append(section)

        if new_sections:
            content = content.rstrip() + "\n" + "\n".join(new_sections)

        return content

    def _find_hypothesis_by_tendency(self, tendency: str):
        """Find an existing hypothesis Memory row by title. Returns Memory or None."""
        try:
            return (
                Memory.select()
                .where(
                    (Memory.kind == "hypothesis")
                    & (Memory.title == tendency)
                    & Memory.archived_at.is_null()
                )
                .first()
            )
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Hypothesis promotion gate (RISK-12, Wave 3.2)
# ---------------------------------------------------------------------------

# Stage order mirrors LifecycleManager.STAGE_ORDER without importing the class.
_STAGE_ORDER = ["ephemeral", "consolidated", "crystallized", "instinctive"]


def can_promote_hypothesis(memory: Memory) -> bool:
    """
    Hypothesis promotion gate.

    Returns True when the memory is eligible for promotion, False otherwise.

    Exemption marker:
        Memories with ``kind != "hypothesis"`` are *not* subject to the
        evidence gate and return True immediately.  This covers explicit
        user-statement memories (kind=None, kind='finding', kind='preference',
        kind='correction', etc.) — anything that is not an LLM-inferred
        hypothesis bypasses the threshold check.

    Gate rules (applied only when kind == "hypothesis"):
        1. evidence_count >= 3
        2. evidence_session_ids encodes >= 2 distinct session identifiers
        3. No ``contradicts`` edge in ``memory_edges`` touching this memory
           (checked bidirectionally: source_id == id OR target_id == id)

    Consolidation caller note (Wave 4.1):
        This function is defined here so the consolidator (core/consolidator.py,
        owned by Task 3.1) can import and call it during its promotion cycle.
        Do NOT modify core/consolidator.py from this task — the integration
        is a cross-task concern.  The companion ``promote_hypothesis()`` function
        performs the actual mutation once this gate returns True.

    Args:
        memory: A Memory ORM instance.

    Returns:
        True if the memory may be promoted, False otherwise.
    """
    from .models import MemoryEdge

    # Non-hypothesis memories are exempt from the evidence gate.
    if memory.kind != "hypothesis":
        return True

    # --- evidence_count check ---
    evidence_count = memory.evidence_count or 0
    if evidence_count < 3:
        return False

    # --- distinct-session check ---
    try:
        session_ids: list = json.loads(memory.evidence_session_ids or "[]")
        if not isinstance(session_ids, list):
            session_ids = []
    except (ValueError, TypeError):
        session_ids = []

    if len(set(session_ids)) < 2:
        return False

    # --- contradiction check (bidirectional) ---
    try:
        contradiction_exists = (
            MemoryEdge.select()
            .where(
                (MemoryEdge.edge_type == "contradicts")
                & (
                    (MemoryEdge.source_id == memory.id)
                    | (MemoryEdge.target_id == memory.id)
                )
            )
            .exists()
        )
        if contradiction_exists:
            return False
    except Exception:
        # If the table doesn't exist or query fails, do not block promotion.
        pass

    return True


def promote_hypothesis(memory: Memory, rationale: str | None = None) -> str:
    """
    Promote a hypothesis Memory to the next lifecycle stage.

    Called after ``can_promote_hypothesis(memory)`` returns True.

    Mutation performed:
        - ``memory.kind`` is set to None (clears the 'hypothesis' marker —
          the memory is now treated as a durable finding).
        - ``memory.stage`` is advanced by one step in _STAGE_ORDER.
        - A ConsolidationLog entry is written.

    Wave 4.1 integration note:
        The consolidation cycle in core/consolidator.py should import
        ``can_promote_hypothesis`` and ``promote_hypothesis`` from this module
        and call them on each hypothesis Memory during its promotion pass.
        This function intentionally does NOT call core.lifecycle.LifecycleManager
        to avoid importing the full lifecycle machinery; it mirrors the minimal
        mutation subset needed for hypothesis promotion.

    Args:
        memory: A Memory ORM instance with kind == 'hypothesis'.
        rationale: Optional description written to the consolidation log.

    Returns:
        New stage name after promotion.

    Raises:
        ValueError: If the memory is already at the highest stage.
    """
    current_stage = memory.stage
    try:
        current_idx = _STAGE_ORDER.index(current_stage)
    except ValueError:
        # Unknown stage — default to ephemeral
        current_idx = 0

    if current_idx >= len(_STAGE_ORDER) - 1:
        raise ValueError(f"Memory already at highest stage: {current_stage}")

    next_stage = _STAGE_ORDER[current_idx + 1]
    from_stage = current_stage

    memory.kind = None
    memory.stage = next_stage
    memory.save()

    ConsolidationLog.create(
        timestamp=datetime.now().isoformat(),
        action="promoted",
        memory_id=memory.id,
        from_stage=from_stage,
        to_stage=next_stage,
        rationale=rationale or "Hypothesis promotion gate passed",
    )

    logger.info(
        "Promoted hypothesis memory %s: %s -> %s (kind cleared)",
        memory.id,
        from_stage,
        next_stage,
    )

    return next_stage
