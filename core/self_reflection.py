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

    def apply_reflection(self, reflection: dict) -> str:
        """
        Apply reflection results to the self-model memory.

        Appends new observations and marks deprecated ones.

        Args:
            reflection: Dict from reflect() with 'observations' and 'deprecated'.

        Returns:
            Updated memory ID.
        """
        model_id = self.ensure_self_model()
        model_memory = Memory.get_by_id(model_id)
        current_content = model_memory.content or ""

        new_content = self._merge_reflection(current_content, reflection)

        model_memory.content = new_content
        model_memory.save()

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
