"""
Consolidation engine for LLM-based memory curation during PreCompact.

Reads ephemeral session observations, calls Claude to decide what to
keep/prune/promote, applies privacy filtering, and writes decisions back
to the memory store.
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import anthropic

from .lifecycle import LifecycleManager
from .prompts import CONSOLIDATION_PROMPT, CONTRADICTION_RESOLUTION_PROMPT, EMOTIONAL_STATE_PATTERNS
from .relevance import RelevanceEngine
from .storage import MemoryStore

logger = logging.getLogger(__name__)


class Consolidator:
    """
    LLM-based memory curation engine.

    During a PreCompact hook, reads ephemeral observations from the current
    session, asks Claude which observations are worth keeping, and executes
    the resulting decisions against the memory store.
    """

    def __init__(
        self,
        store: MemoryStore,
        lifecycle: LifecycleManager,
        model: str = "claude-sonnet-4-6",
    ):
        self.store = store
        self.lifecycle = lifecycle
        self.model = model
        self._privacy_patterns = [re.compile(p, re.IGNORECASE) for p in EMOTIONAL_STATE_PATTERNS]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def consolidate_session(self, ephemeral_path: str, session_id: str) -> dict:
        """
        Run consolidation for one session.

        Steps:
        1. Read the ephemeral markdown file at ephemeral_path.
        2. Build a manifest summary from the existing memory store.
        3. Privacy-filter the ephemeral content before sending to the LLM.
        4. Call Claude to get per-observation decisions.
        5. Execute decisions (keep/prune/promote) and collect conflicts.
        6. Return a summary dict.

        Args:
            ephemeral_path: Absolute path to the ephemeral session markdown.
            session_id: Identifier for the current session.

        Returns:
            {
                "kept": [memory_id, ...],
                "pruned": [{"observation": ..., "rationale": ...}, ...],
                "promoted": [memory_id, ...],
                "conflicts": [{"observation": ..., "contradicts": memory_id}, ...],
            }
        """
        # 1. Read ephemeral content
        ephemeral_content = Path(ephemeral_path).read_text(encoding="utf-8")

        # 2. Privacy-filter before sending to LLM
        filtered_content, was_filtered = self.filter_privacy(ephemeral_content)
        if was_filtered:
            logger.info("Privacy filter removed emotional state observations from ephemeral content")

        # 3. Build manifest summary
        manifest_summary = self._build_manifest_summary()

        # 4. Build and send prompt to Claude
        prompt = CONSOLIDATION_PROMPT.format(
            ephemeral_content=filtered_content,
            manifest_summary=manifest_summary,
        )
        decisions = self._call_llm(prompt)

        # 5. Execute decisions
        kept = []
        pruned = []
        promoted = []
        conflicts = []

        for decision in decisions:
            action = decision.get("action", "").lower()
            observation = decision.get("observation", "")
            rationale = decision.get("rationale", "")
            contradicts = decision.get("contradicts")

            # Track conflicts regardless of action
            if contradicts:
                conflicts.append({"observation": observation, "contradicts": contradicts})

            if action == "keep":
                memory_id = self._execute_keep(decision, session_id)
                if memory_id:
                    kept.append(memory_id)

            elif action == "prune":
                self._execute_prune(observation, rationale, session_id)
                pruned.append({"observation": observation, "rationale": rationale})

            elif action == "promote":
                memory_id = self._execute_promote(decision, session_id)
                if memory_id:
                    promoted.append(memory_id)
            else:
                logger.warning("Unknown action '%s' in LLM response; skipping", action)

        # 6. Resolve contradictions — refine conflicting memories with scope/nuance
        resolved = []
        if conflicts:
            resolved = self._resolve_conflicts(conflicts, session_id)

        # 7. Check if kept observations match any archived memories → rehydrate
        # Exclude memories that were just archived by contradiction resolution
        just_archived = {r["memory_id"] for r in resolved if r["resolution_type"] == "superseded"}
        rehydrated = []
        if kept:
            rehydrated = self._check_rehydration(kept, exclude_ids=just_archived)

        return {
            "kept": kept,
            "pruned": pruned,
            "promoted": promoted,
            "conflicts": conflicts,
            "resolved": resolved,
            "rehydrated": rehydrated,
        }

    def estimate_token_budget(self, memories: list[dict]) -> int:
        """
        Rough token estimate for a list of memory dicts.

        Uses the common heuristic of chars / 4.

        Args:
            memories: List of memory dicts (any shape).

        Returns:
            Estimated token count (int).
        """
        total_chars = sum(len(str(m)) for m in memories)
        return total_chars // 4

    def filter_privacy(self, text: str) -> tuple[str, bool]:
        """
        Remove lines matching emotional state patterns (D-12).

        Args:
            text: Raw text to filter.

        Returns:
            (filtered_text, was_anything_filtered) — the cleaned text and a
            boolean indicating whether any lines were removed.
        """
        lines = text.splitlines(keepends=True)
        kept_lines = []
        any_filtered = False

        for line in lines:
            if self._is_emotional_state(line):
                any_filtered = True
                logger.debug("Privacy filter removed line: %s", line.rstrip())
            else:
                kept_lines.append(line)

        return "".join(kept_lines), any_filtered

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_emotional_state(self, text: str) -> bool:
        """Return True if text matches any emotional state pattern."""
        return any(p.search(text) for p in self._privacy_patterns)

    def _build_manifest_summary(self) -> str:
        """
        Build a text summary of existing memories for prompt context.

        Lists title + summary for each memory in every non-ephemeral stage.
        """
        sections = []
        for stage in ("consolidated", "crystallized", "instinctive"):
            memories = self.store.list_by_stage(stage)
            if not memories:
                continue
            lines = [f"## {stage.capitalize()} ({len(memories)} memories)"]
            for m in memories:
                title = m.get("title") or "(untitled)"
                summary = m.get("summary") or ""
                mid = m.get("id", "")
                lines.append(f"- [{mid}] {title}: {summary}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections) if sections else "(no existing memories)"

    def _call_llm(self, prompt: str) -> list[dict]:
        """
        Call the Anthropic API and parse the JSON response.

        Retries once with explicit JSON instructions if the first response is
        malformed. Raises ValueError if both attempts fail.

        Args:
            prompt: The full consolidation prompt.

        Returns:
            List of decision dicts from the LLM.
        """
        if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
            client = anthropic.AnthropicBedrock()
            model = "us.anthropic.claude-sonnet-4-6"
        else:
            client = anthropic.Anthropic()
            model = self.model
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text

        try:
            return self._parse_decisions(raw)
        except (json.JSONDecodeError, KeyError, TypeError) as first_err:
            logger.warning("LLM returned malformed JSON on first attempt: %s — retrying", first_err)

            retry_prompt = (
                prompt
                + "\n\nIMPORTANT: Your previous response was not valid JSON. "
                "Respond ONLY with a valid JSON object starting with { and ending with }. "
                "No markdown fences, no explanation, no preamble."
            )
            retry_response = client.messages.create(
                model=model,
                max_tokens=2048,
                temperature=0,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            retry_raw = retry_response.content[0].text

            try:
                return self._parse_decisions(retry_raw)
            except (json.JSONDecodeError, KeyError, TypeError) as second_err:
                raise ValueError(
                    f"LLM returned malformed JSON on both attempts. "
                    f"First error: {first_err}. Second error: {second_err}. "
                    f"Last raw response: {retry_raw[:500]}"
                ) from second_err

    def _parse_decisions(self, raw: str) -> list[dict]:
        """
        Parse JSON from LLM response, handling optional markdown fences.

        Args:
            raw: Raw text from the LLM.

        Returns:
            List of decision dicts.

        Raises:
            json.JSONDecodeError: If text is not valid JSON.
            KeyError: If expected keys are missing.
        """
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove opening fence (```json or ```)
            lines = lines[1:]
            # Remove closing fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        data = json.loads(text)
        return data["decisions"]

    def _execute_keep(self, decision: dict, session_id: str) -> str | None:
        """
        Execute a KEEP decision by creating a consolidated memory.

        Args:
            decision: Decision dict from LLM.
            session_id: Current session identifier.

        Returns:
            New memory_id, or None if creation failed.
        """
        target_path = decision.get("target_path") or "general/observation.md"
        title = decision.get("title") or "Untitled"
        summary = decision.get("summary") or ""
        tags = list(decision.get("tags") or [])
        observation = decision.get("observation") or ""

        # Tag observation type if the consolidator classified it
        obs_type = decision.get("observation_type")
        if obs_type and obs_type != "null" and f"type:{obs_type}" not in tags:
            tags.append(f"type:{obs_type}")

        # Normalise path: strip any leading "consolidated/" prefix since
        # store.create() prepends the stage directory itself.
        if target_path.startswith("consolidated/"):
            target_path = target_path[len("consolidated/"):]

        try:
            memory_id = self.store.create(
                path=target_path,
                content=observation,
                metadata={
                    "stage": "consolidated",
                    "title": title,
                    "summary": summary,
                    "tags": tags,
                    "source_session": session_id,
                },
            )
            self.store.log_consolidation(
                action="kept",
                memory_id=memory_id,
                from_stage="ephemeral",
                to_stage="consolidated",
                rationale=decision.get("rationale", ""),
                session_id=session_id,
            )
            return memory_id
        except ValueError as exc:
            logger.warning("KEEP failed for '%s': %s", title, exc)
            return None

    def _execute_prune(self, observation: str, rationale: str, session_id: str) -> None:
        """
        Execute a PRUNE decision by logging it (no memory created).

        Args:
            observation: The observation being pruned.
            rationale: Why it was pruned.
            session_id: Current session identifier.
        """
        # Observations that are pruned were never persisted, so we generate a
        # deterministic pseudo-id from the observation content for audit tracing.
        pseudo_id = f"pruned-{hashlib.md5(observation.encode()).hexdigest()[:8]}"
        self.store.log_consolidation(
            action="pruned",
            memory_id=pseudo_id,
            from_stage="ephemeral",
            to_stage="ephemeral",
            rationale=rationale,
            session_id=session_id,
        )
        logger.debug("PRUNE: %s — %s", observation[:80], rationale)

    def _execute_promote(self, decision: dict, session_id: str) -> str | None:
        """
        Execute a PROMOTE decision by incrementing reinforcement_count,
        then checking if the memory qualifies for lifecycle promotion.

        Args:
            decision: Decision dict from LLM (must include "reinforces" key).
            session_id: Current session identifier.

        Returns:
            memory_id if successfully promoted, else None.
        """
        memory_id = decision.get("reinforces")
        if not memory_id or memory_id == "null":
            logger.warning("PROMOTE decision missing reinforces id; skipping")
            return None

        try:
            memory = self.store.get(memory_id)
        except ValueError:
            logger.warning("PROMOTE target memory not found: %s", memory_id)
            return None

        current_count = memory.get("reinforcement_count", 0)
        self.store.update(
            memory_id,
            metadata={"reinforcement_count": current_count + 1},
        )

        # Log the promotion attempt
        self.store.log_consolidation(
            action="promoted",
            memory_id=memory_id,
            from_stage=memory["stage"],
            to_stage=memory["stage"],
            rationale=decision.get("rationale", "Reinforced by new observation"),
            session_id=session_id,
        )

        # Check if memory now qualifies for lifecycle stage advancement
        can_advance, reason = self.lifecycle.can_promote(memory_id)
        if can_advance:
            logger.info(
                "Memory %s qualifies for stage promotion: %s", memory_id, reason
            )

        return memory_id

    def _resolve_conflicts(self, conflicts: list[dict], session_id: str) -> list[dict]:
        """
        Resolve contradictions between new observations and existing memories.

        For each conflict, calls the LLM to produce a refined version of the
        contradicted memory that incorporates the new information with proper
        scoping. Updates the original memory in place.

        Args:
            conflicts: List of {"observation": str, "contradicts": memory_id}.
            session_id: Current session identifier.

        Returns:
            List of resolution dicts:
            [{"memory_id": ..., "resolution_type": ..., "refined_title": ...}, ...]
        """
        resolved = []

        for conflict in conflicts:
            memory_id = conflict.get("contradicts")
            observation = conflict.get("observation", "")

            if not memory_id or memory_id == "null":
                continue

            try:
                memory = self.store.get(memory_id)
            except ValueError:
                logger.warning("Conflict target memory not found: %s", memory_id)
                continue

            # Strip frontmatter from content for cleaner prompt
            content = memory.get("content", "")
            if content.startswith("---"):
                lines = content.split("\n")
                for i in range(1, len(lines)):
                    if lines[i] == "---":
                        content = "\n".join(lines[i + 1:]).strip()
                        break

            prompt = CONTRADICTION_RESOLUTION_PROMPT.format(
                memory_title=memory.get("title", "Untitled"),
                memory_content=content,
                observation=observation,
            )

            try:
                result = self._call_resolution_llm(prompt)
            except (ValueError, Exception) as e:
                logger.warning("Contradiction resolution LLM failed for %s: %s", memory_id, e)
                continue

            confidence = result.get("confidence", 0.0)
            if confidence < 0.4:
                logger.info("Low confidence resolution for %s (%.2f) — skipping", memory_id, confidence)
                continue

            resolution_type = result.get("resolution_type", "scoped")
            refined_title = result.get("refined_title", memory.get("title"))
            refined_content = result.get("refined_content", "")

            if resolution_type == "superseded":
                # The new observation fully replaces the old memory — archive it
                superseded_note = (
                    f"[Superseded] {refined_content}\n\n"
                    f"---\nSuperseded by observation: {observation[:200]}"
                )
                self.store.update(
                    memory_id,
                    content=superseded_note,
                    metadata={
                        "title": f"[Superseded] {refined_title}",
                        "summary": f"Superseded: {refined_content[:120]}",
                    },
                )
                self.store.archive(memory_id)
                log_action = "deprecated"
            elif resolution_type == "scoped":
                # Both memories survive with clarified scope
                existing_tags = memory.get("tags", [])
                if isinstance(existing_tags, str):
                    import json as _json
                    try:
                        existing_tags = _json.loads(existing_tags)
                    except (ValueError, TypeError):
                        existing_tags = []
                scope_tags = [t for t in existing_tags if t.startswith("scope:")]
                if not scope_tags:
                    existing_tags.append("scope:narrowed")
                self.store.update(
                    memory_id,
                    content=refined_content,
                    metadata={
                        "title": refined_title,
                        "summary": refined_content[:150],
                        "tags": existing_tags,
                    },
                )
                log_action = "merged"
            else:
                # Coexist — refine content, both memories valid as-is
                self.store.update(
                    memory_id,
                    content=refined_content,
                    metadata={
                        "title": refined_title,
                        "summary": refined_content[:150],
                    },
                )
                log_action = "merged"

            self.store.log_consolidation(
                action=log_action,
                memory_id=memory_id,
                from_stage=memory["stage"],
                to_stage=memory["stage"],
                rationale=f"Contradiction resolved ({resolution_type}): {observation[:100]}",
                session_id=session_id,
            )

            resolved.append({
                "memory_id": memory_id,
                "resolution_type": resolution_type,
                "refined_title": refined_title,
                "confidence": confidence,
            })

            logger.info(
                "Resolved contradiction for '%s' (%s, confidence=%.2f)",
                memory.get("title", "?"), resolution_type, confidence,
            )

        return resolved

    def _call_resolution_llm(self, prompt: str) -> dict:
        """Call the LLM for contradiction resolution and parse JSON response."""
        if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
            client = anthropic.AnthropicBedrock()
            model = "us.anthropic.claude-sonnet-4-6"
        else:
            client = anthropic.Anthropic()
            model = self.model

        response = client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return json.loads(text)

    def _check_rehydration(self, kept_ids: list[str], exclude_ids: set = None) -> list[str]:
        """
        Check if newly kept observations match any archived memories.

        For each kept memory, searches archived memories via FTS for
        topic overlap.  Matching archived memories are rehydrated.

        Args:
            kept_ids: List of memory IDs that were just created (KEEP decisions).
            exclude_ids: Memory IDs to skip (e.g., just-superseded memories).

        Returns:
            List of rehydrated memory IDs.
        """
        if exclude_ids is None:
            exclude_ids = set()
        relevance_engine = RelevanceEngine(self.store)
        rehydrated = []

        for memory_id in kept_ids:
            try:
                memory = self.store.get(memory_id)
            except ValueError:
                continue

            # Use title + summary as the search text
            search_text = f"{memory.get('title', '')} {memory.get('summary', '')}"
            matches = relevance_engine.find_rehydration_by_observation(search_text)

            for match in matches:
                if match["id"] in exclude_ids:
                    continue
                try:
                    self.store.unarchive(match["id"])
                    self.store.log_consolidation(
                        action="promoted",
                        memory_id=match["id"],
                        from_stage="archived",
                        to_stage=match["stage"],
                        rationale=f"Rehydrated by new observation: {memory.get('title', 'untitled')}",
                    )
                    rehydrated.append(match["id"])
                    logger.info(
                        "Rehydrated archived memory %s triggered by new observation %s",
                        match["id"],
                        memory_id,
                    )
                except ValueError as e:
                    logger.warning("Rehydration failed for %s: %s", match["id"], e)

        return rehydrated
