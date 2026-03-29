"""
Consolidation engine for LLM-based memory curation during PreCompact.

Reads ephemeral session observations, calls Claude to decide what to
keep/prune/promote, applies privacy filtering, and writes decisions back
to the memory store.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .database import get_base_dir, get_vec_store
from .lifecycle import LifecycleManager
from .llm import call_llm as _call_llm_transport
from .models import ConsolidationLog, Memory, db
from .prompts import CONSOLIDATION_PROMPT, CONTRADICTION_RESOLUTION_PROMPT, EMOTIONAL_STATE_PATTERNS
from .relevance import RelevanceEngine

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
        lifecycle: LifecycleManager,
        model: str = "claude-sonnet-4-6",
    ):
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

        # 2b. Habituation filter — suppress routine events before LLM call
        base_dir = get_base_dir()
        if base_dir:
            from .habituation import HabituationModel
            hab_model = HabituationModel(base_dir)
            filtered_content, suppressed_count = hab_model.filter_observations(filtered_content)
            if suppressed_count > 0:
                logger.info("Habituation filter suppressed %d routine observations", suppressed_count)

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

    def estimate_token_budget(self, memories: list) -> int:
        """
        Rough token estimate for a list of memory objects.

        Uses the common heuristic of chars / 4.

        Args:
            memories: List of memory objects or dicts.

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
            memories = list(Memory.by_stage(stage))
            if not memories:
                continue
            lines = [f"## {stage.capitalize()} ({len(memories)} memories)"]
            for m in memories:
                title = m.title or "(untitled)"
                summary = m.summary or ""
                mid = m.id
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
        raw = _call_llm_transport(prompt, max_tokens=2048, temperature=0)

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
            retry_raw = _call_llm_transport(retry_prompt, max_tokens=2048, temperature=0)

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
        # the file path is built from base_dir + stage.
        if target_path.startswith("consolidated/"):
            target_path = target_path[len("consolidated/"):]

        try:
            base_dir = get_base_dir()
            file_path = base_dir / "consolidated" / target_path
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Build full content with frontmatter
            frontmatter = {
                'name': title,
                'description': summary,
                'type': 'memory',
            }
            full_content = _format_markdown(frontmatter, observation)
            content_hash = hashlib.md5(full_content.encode('utf-8')).hexdigest()

            # Dedup check
            if Memory.select().where(Memory.content_hash == content_hash).exists():
                raise ValueError(f"Duplicate content detected (hash: {content_hash})")

            now = datetime.now().isoformat()
            mem = Memory.create(
                stage="consolidated",
                title=title,
                summary=summary,
                content=full_content,
                tags=json.dumps(tags),
                importance=0.5,
                reinforcement_count=0,
                created_at=now,
                updated_at=now,
                source_session=session_id,
                content_hash=content_hash,
            )
            memory_id = mem.id

            # Write file
            file_path.write_text(full_content, encoding="utf-8")

            ConsolidationLog.create(
                timestamp=now,
                session_id=session_id,
                action="kept",
                memory_id=memory_id,
                from_stage="ephemeral",
                to_stage="consolidated",
                rationale=decision.get("rationale", ""),
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
        ConsolidationLog.create(
            timestamp=datetime.now().isoformat(),
            session_id=session_id,
            action="pruned",
            memory_id=pseudo_id,
            from_stage="ephemeral",
            to_stage="ephemeral",
            rationale=rationale,
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
            memory = Memory.get_by_id(memory_id)
        except Memory.DoesNotExist:
            logger.warning("PROMOTE target memory not found: %s", memory_id)
            return None

        current_count = memory.reinforcement_count or 0
        memory.reinforcement_count = current_count + 1
        memory.save()

        # Log the promotion attempt
        ConsolidationLog.create(
            timestamp=datetime.now().isoformat(),
            session_id=session_id,
            action="promoted",
            memory_id=memory_id,
            from_stage=memory.stage,
            to_stage=memory.stage,
            rationale=decision.get("rationale", "Reinforced by new observation"),
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
                memory = Memory.get_by_id(memory_id)
            except Memory.DoesNotExist:
                logger.warning("Conflict target memory not found: %s", memory_id)
                continue

            # Strip frontmatter from content for cleaner prompt
            content = memory.content or ""
            if content.startswith("---"):
                lines = content.split("\n")
                for i in range(1, len(lines)):
                    if lines[i] == "---":
                        content = "\n".join(lines[i + 1:]).strip()
                        break

            prompt = CONTRADICTION_RESOLUTION_PROMPT.format(
                memory_title=memory.title or "Untitled",
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
            refined_title = result.get("refined_title", memory.title)
            refined_content = result.get("refined_content", "")

            if resolution_type == "superseded":
                # The new observation fully replaces the old memory — archive it
                superseded_note = (
                    f"[Superseded] {refined_content}\n\n"
                    f"---\nSuperseded by observation: {observation[:200]}"
                )
                memory.content = superseded_note
                memory.title = f"[Superseded] {refined_title}"
                memory.summary = f"Superseded: {refined_content[:120]}"
                memory.save()
                Memory.update(archived_at=datetime.now().isoformat()).where(Memory.id == memory_id).execute()
                log_action = "deprecated"
            elif resolution_type == "scoped":
                # Both memories survive with clarified scope
                existing_tags = memory.tag_list
                scope_tags = [t for t in existing_tags if t.startswith("scope:")]
                if not scope_tags:
                    existing_tags.append("scope:narrowed")
                memory.content = refined_content
                memory.title = refined_title
                memory.summary = refined_content[:150]
                memory.tag_list = existing_tags
                memory.save()
                log_action = "merged"
            else:
                # Coexist — refine content, both memories valid as-is
                memory.content = refined_content
                memory.title = refined_title
                memory.summary = refined_content[:150]
                memory.save()
                log_action = "merged"

            ConsolidationLog.create(
                timestamp=datetime.now().isoformat(),
                session_id=session_id,
                action=log_action,
                memory_id=memory_id,
                from_stage=memory.stage,
                to_stage=memory.stage,
                rationale=f"Contradiction resolved ({resolution_type}): {observation[:100]}",
            )

            resolved.append({
                "memory_id": memory_id,
                "resolution_type": resolution_type,
                "refined_title": refined_title,
                "confidence": confidence,
            })

            logger.info(
                "Resolved contradiction for '%s' (%s, confidence=%.2f)",
                memory.title or "?", resolution_type, confidence,
            )

        return resolved

    def _call_resolution_llm(self, prompt: str) -> dict:
        """Call the LLM for contradiction resolution and parse JSON response."""
        text = _call_llm_transport(prompt, max_tokens=1024, temperature=0)
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
        relevance_engine = RelevanceEngine()
        rehydrated = []

        for memory_id in kept_ids:
            try:
                memory = Memory.get_by_id(memory_id)
            except Memory.DoesNotExist:
                continue

            # Use title + summary as the search text
            search_text = f"{memory.title or ''} {memory.summary or ''}"
            matches = relevance_engine.find_rehydration_by_observation(search_text)

            for match in matches:
                match_id = match.id if hasattr(match, 'id') and not isinstance(match, dict) else match.get("id") if isinstance(match, dict) else match.id
                match_stage = match.stage if hasattr(match, 'stage') and not isinstance(match, dict) else match.get("stage") if isinstance(match, dict) else match.stage
                if match_id in exclude_ids:
                    continue
                try:
                    Memory.update(archived_at=None, updated_at=datetime.now().isoformat()).where(Memory.id == match_id).execute()
                    ConsolidationLog.create(
                        timestamp=datetime.now().isoformat(),
                        action="promoted",
                        memory_id=match_id,
                        from_stage="archived",
                        to_stage=match_stage,
                        rationale=f"Rehydrated by new observation: {memory.title or 'untitled'}",
                    )
                    rehydrated.append(match_id)
                    logger.info(
                        "Rehydrated archived memory %s triggered by new observation %s",
                        match_id,
                        memory_id,
                    )
                except Exception as e:
                    logger.warning("Rehydration failed for %s: %s", match_id, e)

        return rehydrated


def _format_markdown(metadata: dict, content: str) -> str:
    """Format metadata and content as markdown with YAML frontmatter."""
    lines = ['---']
    for key in ['name', 'description', 'type']:
        if key in metadata:
            lines.append(f'{key}: {metadata[key]}')
    lines.append('---')
    lines.append('')
    lines.append(content)
    return '\n'.join(lines)
