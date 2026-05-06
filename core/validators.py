"""
Schema validators for memesis observation pipeline.

Dependency choice: stdlib dataclasses + manual __post_init__ validation.
Pydantic is not in requirements.txt; adding it is not justified for this scope.
Per panel OD-C resolution.

Fail-fast philosophy (TAXONOMY §5): no coercion, no silent passthrough.
Hard mode (validate_stage1 / validate_stage2): raises ValidationError on any violation.
Soft mode (validate_stage1_soft): returns warnings list, no exception.

Observability: validator decisions are traced to
  backfill-output/observability/validator-trace.jsonl
Written locally here; refactor to use core/observability.py once WS-A lands.
"""

import json
import re
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Closed enums per panel C5 (reduced multi-axis)
# ---------------------------------------------------------------------------

KIND_VALUES = frozenset({
    "decision",
    "finding",
    "preference",
    "constraint",
    "correction",
    "open_question",
})

KNOWLEDGE_TYPE_VALUES = frozenset({
    "factual",
    "conceptual",
    "procedural",
    "metacognitive",
})

KNOWLEDGE_TYPE_CONFIDENCE_VALUES = frozenset({"low", "high"})

# Stage 2 only
SUBJECT_VALUES = frozenset({
    "self",
    "user",
    "system",
    "collaboration",
    "aesthetic",
    "workflow",
    "domain",
})

# Stage 2 only — None is allowed
WORK_EVENT_VALUES = frozenset({"bugfix", "feature", "refactor", "discovery", "change"})

# Session types that may legitimately carry a non-null work_event.
# Research and writing sessions have no code actions; assigning a code work_event
# to them is hallucination and the post-parse layer nulls it out.
WORK_EVENT_ALLOWED_SESSION_TYPES = frozenset({"code"})


def enforce_work_event_session_type(raw: dict, session_type: str | None) -> tuple[dict, str | None]:
    """Defense-in-depth for the prompt's "work_event MUST be null when session_type != code" rule.

    Mutates a copy of the raw observation dict, nullifying work_event when the session
    type isn't allowed to carry one. Returns (normalized_dict, violation_or_None).
    Caller is responsible for logging the violation if non-None.
    """
    if session_type is None or session_type in WORK_EVENT_ALLOWED_SESSION_TYPES:
        return raw, None
    work_event = raw.get("work_event")
    if work_event is None:
        return raw, None
    normalized = dict(raw)
    normalized["work_event"] = None
    violation = (
        f"work_event={work_event!r} produced under session_type={session_type!r}; "
        f"nulled per session_type contract"
    )
    return normalized, violation

# Forward-compat for Sprint B (LLME-F9) — None is allowed
SESSION_TYPE_VALUES = frozenset({"code", "writing", "research"})

# DS-F10: reject facts whose first token (case-insensitive, stripped of leading
# punctuation) is in this set.
PRONOUN_PREFIXES = frozenset({
    "he", "she", "it", "they", "we", "i", "this", "that", "the",
})

# ---------------------------------------------------------------------------
# Trace writer — local JSONL appender, no dependency on core/observability.py
# ---------------------------------------------------------------------------

_TRACE_PATH = Path("backfill-output") / "observability" / "validator-trace.jsonl"


def _write_trace(
    stage: str,
    outcome: str,
    field_errors: list[str],
    raw_excerpt: str,
) -> None:
    """Append one line to the validator trace JSONL."""
    try:
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "stage": stage,
            "outcome": outcome,
            "field_errors": field_errors,
            "raw_excerpt": raw_excerpt[:80],
        }
        with _TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass  # never let tracing crash the ingest path


# ---------------------------------------------------------------------------
# ValidationError
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    """Raised by hard-mode validators on schema violation."""

    def __init__(self, field_name: str, value: Any, reason: str) -> None:
        self.field_name = field_name
        self.value = value
        self.reason = reason
        super().__init__(f"[{field_name}={value!r}] {reason}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_pronoun_prefixed(fact: str) -> bool:
    """Return True if the fact's first token is a known pronoun prefix.

    Strips leading punctuation before comparison (case-insensitive).
    """
    stripped = fact.lstrip(string.punctuation + " ")
    if not stripped:
        return False
    first_token = re.split(r"\s+", stripped)[0].lower().rstrip(string.punctuation)
    return first_token in PRONOUN_PREFIXES


def _raw_excerpt(raw: dict) -> str:
    try:
        return json.dumps(raw)[:80]
    except Exception:
        return str(raw)[:80]


# ---------------------------------------------------------------------------
# Stage 1 dataclass
# ---------------------------------------------------------------------------


@dataclass
class Stage1Observation:
    """Validated Stage 1 observation.

    Required fields only — multi-axis collapse per panel C5.
    Optional content field retained for legacy mode/content migration path.
    """

    kind: str
    knowledge_type: str
    knowledge_type_confidence: str
    importance: float
    facts: list[str]
    cwd: str | None

    # Legacy field — present in current Stage 1 output, not required in new schema
    content: str | None = None


# ---------------------------------------------------------------------------
# Stage 2 dataclass
# ---------------------------------------------------------------------------


@dataclass
class Stage2Observation:
    """Validated Stage 2 observation — adds optional axes per panel C5."""

    kind: str
    knowledge_type: str
    knowledge_type_confidence: str
    importance: float
    facts: list[str]
    cwd: str | None

    # Stage 2 re-scored importance; Stage 1 score preserved here for audit (panel C7)
    raw_importance: float = 0.0

    # Optional Stage 2 axes
    subject: str | None = None
    work_event: str | None = None
    subtitle: str | None = None

    # Populated by post-processing, not LLM (panel C3)
    linked_observation_ids: list[str] = field(default_factory=list)

    # Legacy
    content: str | None = None


# ---------------------------------------------------------------------------
# Internal shared core validator (called by both parse functions)
# ---------------------------------------------------------------------------


def _validate_stage1_core(
    kind: str,
    knowledge_type: str,
    knowledge_type_confidence: str,
    importance: float,
    facts: list[str],
    cwd: str | None,
) -> None:
    """Shared hard-mode validation for Stage 1 fields.

    Raises ValidationError on first violation.
    """
    if kind not in KIND_VALUES:
        raise ValidationError("kind", kind, f"must be one of {sorted(KIND_VALUES)}")

    if knowledge_type not in KNOWLEDGE_TYPE_VALUES:
        raise ValidationError(
            "knowledge_type",
            knowledge_type,
            f"must be one of {sorted(KNOWLEDGE_TYPE_VALUES)}",
        )

    if knowledge_type_confidence not in KNOWLEDGE_TYPE_CONFIDENCE_VALUES:
        raise ValidationError(
            "knowledge_type_confidence",
            knowledge_type_confidence,
            f"must be one of {sorted(KNOWLEDGE_TYPE_CONFIDENCE_VALUES)}",
        )

    if not isinstance(importance, (int, float)):
        raise ValidationError("importance", importance, "must be a float")
    if not (0.0 <= float(importance) <= 1.0):
        raise ValidationError("importance", importance, "must be in [0.0, 1.0]")

    if not isinstance(facts, list):
        raise ValidationError("facts", facts, "must be a list")
    if len(facts) > 5:
        raise ValidationError("facts", len(facts), "must contain 0-5 items")

    for i, fact in enumerate(facts):
        if is_pronoun_prefixed(fact):
            raise ValidationError(
                f"facts[{i}]",
                fact[:40],
                "fact begins with a pronoun prefix; use a named subject",
            )

    if cwd is not None and not isinstance(cwd, str):
        raise ValidationError("cwd", cwd, "must be a string or null")


# ---------------------------------------------------------------------------
# Public parse API — hard mode
# ---------------------------------------------------------------------------


def validate_stage1(raw: dict) -> Stage1Observation:
    """Parse and validate a Stage 1 observation dict.

    Raises ValidationError (or KeyError for missing required fields) on any violation.
    Traces outcome to validator-trace.jsonl.
    """
    excerpt = _raw_excerpt(raw)
    try:
        # KeyError on missing required fields — converted to ValidationError below
        kind = raw["kind"]
        knowledge_type = raw["knowledge_type"]
        knowledge_type_confidence = raw["knowledge_type_confidence"]
        importance = raw["importance"]
        facts = raw.get("facts", [])
        cwd = raw.get("cwd")

        _validate_stage1_core(kind, knowledge_type, knowledge_type_confidence, importance, facts, cwd)

        obs = Stage1Observation(
            kind=kind,
            knowledge_type=knowledge_type,
            knowledge_type_confidence=knowledge_type_confidence,
            importance=importance,
            facts=facts,
            cwd=cwd,
            content=raw.get("content"),
        )
        _write_trace("stage1", "valid", [], excerpt)
        return obs
    except ValidationError as exc:
        _write_trace("stage1", "rejected", [str(exc)], excerpt)
        raise
    except KeyError as exc:
        error_msg = f"missing required field: {exc}"
        _write_trace("stage1", "rejected", [error_msg], excerpt)
        raise ValidationError(str(exc), None, "required field missing") from exc


def validate_stage2(raw: dict, session_type: str | None = None) -> Stage2Observation:
    """Parse and validate a Stage 2 observation dict.

    Raises ValidationError on any violation.
    Traces outcome to validator-trace.jsonl.
    """
    excerpt = _raw_excerpt(raw)
    # Defense-in-depth: enforce work_event session_type contract before validation.
    raw, violation = enforce_work_event_session_type(raw, session_type)
    if violation is not None:
        import logging as _logging
        _logging.getLogger(__name__).info("validate_stage2: %s", violation)
    try:
        kind = raw["kind"]
        knowledge_type = raw["knowledge_type"]
        knowledge_type_confidence = raw["knowledge_type_confidence"]
        importance = raw["importance"]
        facts = raw.get("facts", [])
        cwd = raw.get("cwd")
        subject = raw.get("subject")
        work_event = raw.get("work_event")
        subtitle = raw.get("subtitle")
        raw_importance = raw.get("raw_importance", raw.get("importance", 0.0))
        linked_observation_ids = raw.get("linked_observation_ids", [])

        # Stage 1 field validation
        _validate_stage1_core(kind, knowledge_type, knowledge_type_confidence, importance, facts, cwd)

        # Stage 2 additional validation
        if subject is not None and subject not in SUBJECT_VALUES:
            raise ValidationError(
                "subject", subject, f"must be one of {sorted(SUBJECT_VALUES)} or null"
            )

        if work_event is not None and work_event not in WORK_EVENT_VALUES:
            raise ValidationError(
                "work_event", work_event, f"must be one of {sorted(WORK_EVENT_VALUES)} or null"
            )

        if subtitle is not None:
            word_count = len(subtitle.split())
            if word_count > 24:
                raise ValidationError("subtitle", f"({word_count} words)", "must be ≤24 words")

        if not isinstance(raw_importance, (int, float)):
            raise ValidationError("raw_importance", raw_importance, "must be a float")
        if not (0.0 <= float(raw_importance) <= 1.0):
            raise ValidationError("raw_importance", raw_importance, "must be in [0.0, 1.0]")

        if not isinstance(linked_observation_ids, list):
            raise ValidationError("linked_observation_ids", linked_observation_ids, "must be a list")

        obs = Stage2Observation(
            kind=kind,
            knowledge_type=knowledge_type,
            knowledge_type_confidence=knowledge_type_confidence,
            importance=importance,
            facts=facts,
            cwd=cwd,
            raw_importance=raw_importance,
            subject=subject,
            work_event=work_event,
            subtitle=subtitle,
            linked_observation_ids=linked_observation_ids,
            content=raw.get("content"),
        )
        _write_trace("stage2", "valid", [], excerpt)
        return obs
    except ValidationError as exc:
        _write_trace("stage2", "rejected", [str(exc)], excerpt)
        raise
    except KeyError as exc:
        error_msg = f"missing required field: {exc}"
        _write_trace("stage2", "rejected", [error_msg], excerpt)
        raise ValidationError(str(exc), None, "required field missing") from exc


# ---------------------------------------------------------------------------
# Public parse API — soft mode (migration period default)
# ---------------------------------------------------------------------------


def validate_stage1_soft(
    raw: dict,
) -> tuple["Stage1Observation | None", list[str]]:
    """Soft-mode Stage 1 validator.

    Returns (parsed_observation_or_None, list_of_warnings).
    Never raises; collects all warnings instead.
    Suitable for the migration period where prompt output is not yet stable.
    """
    warnings: list[str] = []
    excerpt = _raw_excerpt(raw)

    # --- required fields ---
    missing = [f for f in ("kind", "knowledge_type", "knowledge_type_confidence", "importance") if f not in raw]
    if missing:
        for f in missing:
            warnings.append(f"missing required field: {f!r}")
        _write_trace("stage1", "soft_warning", warnings, excerpt)
        return None, warnings

    # --- enum checks ---
    kind = raw["kind"]
    if kind not in KIND_VALUES:
        warnings.append(f"kind={kind!r} not in valid set; accepted with warning")

    knowledge_type = raw["knowledge_type"]
    if knowledge_type not in KNOWLEDGE_TYPE_VALUES:
        warnings.append(f"knowledge_type={knowledge_type!r} not in valid set")

    knowledge_type_confidence = raw["knowledge_type_confidence"]
    if knowledge_type_confidence not in KNOWLEDGE_TYPE_CONFIDENCE_VALUES:
        warnings.append(
            f"knowledge_type_confidence={knowledge_type_confidence!r} not in valid set"
        )

    # --- importance bounds ---
    importance = raw["importance"]
    try:
        importance = float(importance)
        if not (0.0 <= importance <= 1.0):
            warnings.append(f"importance={importance} out of [0.0, 1.0]")
    except (TypeError, ValueError):
        warnings.append(f"importance={importance!r} is not a float")
        importance = 0.0

    # --- facts pronoun check (soft: warn, keep) ---
    facts = raw.get("facts", [])
    if not isinstance(facts, list):
        warnings.append(f"facts must be a list, got {type(facts).__name__}")
        facts = []
    if len(facts) > 5:
        warnings.append(f"facts has {len(facts)} items; max 5")
        facts = facts[:5]

    pronoun_facts: list[str] = []
    clean_facts: list[str] = []
    for fact in facts:
        if is_pronoun_prefixed(fact):
            warnings.append(
                f"fact begins with pronoun prefix (soft-warn, kept): {fact[:50]!r}"
            )
            pronoun_facts.append(fact)
        clean_facts.append(fact)

    cwd = raw.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        warnings.append(f"cwd={cwd!r} must be a string or null; coerced to None")
        cwd = None

    outcome = "soft_warning" if warnings else "valid"
    _write_trace("stage1", outcome, warnings, excerpt)

    obs = Stage1Observation(
        kind=kind if kind in KIND_VALUES else "finding",
        knowledge_type=knowledge_type if knowledge_type in KNOWLEDGE_TYPE_VALUES else "factual",
        knowledge_type_confidence=(
            knowledge_type_confidence
            if knowledge_type_confidence in KNOWLEDGE_TYPE_CONFIDENCE_VALUES
            else "low"
        ),
        importance=importance if 0.0 <= importance <= 1.0 else 0.0,
        facts=clean_facts,
        cwd=cwd,
        content=raw.get("content"),
    )
    return obs, warnings
