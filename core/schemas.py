"""
Pydantic schemas for LLM consolidation output validation (RISK-02).

Provides strict contract enforcement for all LLM response shapes consumed
by core.consolidator.  No peewee imports; no storage logic.

Two top-level shapes:
  - ConsolidationResponse  — wraps the "decisions" array from the main
                             consolidation LLM call.
  - ContradictionResolution — the dict returned by _call_resolution_llm.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {"keep", "update", "merge", "archive", "prune", "promote"}
)

DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset({"prune", "archive"})

# Valid stage transition map: stage → set of stages it may transition to.
# "*" is represented as any source stage may transition to pending_delete.
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "ephemeral": frozenset({"consolidated", "pending_delete"}),
    "consolidated": frozenset({"crystallized", "pending_delete"}),
    "crystallized": frozenset({"instinctive", "pending_delete"}),
    "instinctive": frozenset({"pending_delete"}),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_null_string(v: Any) -> Any:
    """Coerce the literal string "null" to Python None."""
    if v == "null":
        return None
    return v


def _validate_uuid4(v: str | None, field_name: str) -> str | None:
    """Validate that v is a syntactically correct UUID4 string."""
    if v is None:
        return None
    try:
        parsed = uuid.UUID(v)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a valid UUID4, got {v!r}") from exc
    if parsed.version != 4:
        raise ValueError(
            f"{field_name} must be a UUID version 4 (got version {parsed.version}): {v!r}"
        )
    return v


# ---------------------------------------------------------------------------
# Stage transition validator
# ---------------------------------------------------------------------------

class StageTransition(BaseModel):
    """Validates a single stage transition.

    Used when the consumer needs to validate an explicit from_stage → to_stage
    pair (e.g., Wave 3.1 wiring).  Not embedded in ConsolidationDecision because
    the current LLM decision shape does not emit from_stage/to_stage fields.
    """

    from_stage: str
    to_stage: str

    @model_validator(mode="after")
    def validate_transition(self) -> "StageTransition":
        allowed = VALID_TRANSITIONS.get(self.from_stage)
        if allowed is None:
            raise ValueError(
                f"Unknown from_stage {self.from_stage!r}. "
                f"Must be one of: {sorted(VALID_TRANSITIONS)}"
            )
        if self.to_stage not in allowed:
            raise ValueError(
                f"Invalid stage transition: {self.from_stage!r} → {self.to_stage!r}. "
                f"Allowed targets: {sorted(allowed)}"
            )
        return self


# ---------------------------------------------------------------------------
# Consolidation decision model
# ---------------------------------------------------------------------------

class ConsolidationDecision(BaseModel):
    """One entry in the LLM 'decisions' array.

    Covers both plain-observation decisions and card-shaped decisions
    (which include scope, evidence_quotes, user_affect_valence, etc.).
    Unknown extra fields are silently allowed to avoid rejecting future
    prompt schema additions.
    """

    model_config = {"extra": "allow"}

    # Core fields — always present
    action: str
    observation: str = ""
    rationale: str = ""

    # Importance — optional because promote decisions may omit it
    raw_importance: float | None = None
    importance: float | None = None

    # Keep-specific fields
    target_path: str | None = None
    title: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    observation_type: str | None = None

    # Promote-specific field
    reinforces: str | None = None  # memory UUID4 or None

    # Conflict tracking
    contradicts: str | None = None  # memory UUID4 or None

    # Metadata fields from the Stage 2 prompt schema
    kind: str | None = None
    knowledge_type: str | None = None
    knowledge_type_confidence: str | None = None
    facts: list[str] | None = None
    cwd: str | None = None
    subject: str | None = None
    work_event: str | None = None
    subtitle: str | None = None
    resolves_question_id: str | None = None  # memory UUID4 or None

    # Card-shaped optional fields (present when "scope" or "evidence_quotes" in dict)
    scope: str | None = None
    evidence_quotes: list[str] | None = None
    user_affect_valence: str | None = None
    criterion_weights: dict[str, str] | None = None
    rejected_options: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError(f"action must be a string, got {type(v).__name__}: {v!r}")
        normalised = v.strip().lower()
        if normalised not in ALLOWED_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(ALLOWED_ACTIONS)}, got {v!r}"
            )
        return normalised

    @field_validator("importance", "raw_importance", mode="before")
    @classmethod
    def validate_importance(cls, v: Any) -> float | None:
        if v is None:
            return None
        try:
            fv = float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"importance must be a float in [0.0, 1.0], got {v!r}"
            ) from exc
        if not (0.0 <= fv <= 1.0):
            raise ValueError(
                f"importance must be in [0.0, 1.0], got {fv}"
            )
        return fv

    @field_validator("reinforces", "contradicts", "resolves_question_id", mode="before")
    @classmethod
    def coerce_null_string_ids(cls, v: Any) -> Any:
        return _coerce_null_string(v)

    @field_validator("reinforces", mode="after")
    @classmethod
    def validate_reinforces_uuid(cls, v: str | None) -> str | None:
        return _validate_uuid4(v, "reinforces")

    @field_validator("contradicts", mode="after")
    @classmethod
    def validate_contradicts_uuid(cls, v: str | None) -> str | None:
        return _validate_uuid4(v, "contradicts")

    @field_validator("resolves_question_id", mode="after")
    @classmethod
    def validate_resolves_question_uuid(cls, v: str | None) -> str | None:
        return _validate_uuid4(v, "resolves_question_id")

    @model_validator(mode="after")
    def validate_destructive_requires_rationale(self) -> "ConsolidationDecision":
        if self.action in DESTRUCTIVE_ACTIONS:
            if not self.rationale or not self.rationale.strip():
                raise ValueError(
                    f"action={self.action!r} is destructive and requires a non-empty rationale"
                )
        return self

    @model_validator(mode="after")
    def validate_promote_requires_reinforces(self) -> "ConsolidationDecision":
        if self.action == "promote" and not self.reinforces:
            raise ValueError(
                "action='promote' requires a non-null 'reinforces' (target memory id)"
            )
        return self


# ---------------------------------------------------------------------------
# Consolidation response envelope
# ---------------------------------------------------------------------------

class ConsolidationResponse(BaseModel):
    """Top-level LLM response wrapper: {"decisions": [...]}."""

    decisions: list[ConsolidationDecision] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Contradiction resolution model
# ---------------------------------------------------------------------------

class ContradictionResolution(BaseModel):
    """LLM response shape from _call_resolution_llm.

    {"confidence": 0.0-1.0, "resolution_type": ..., "refined_title": ..., "refined_content": ...}
    """

    model_config = {"extra": "allow"}

    confidence: float = 0.0
    resolution_type: Literal["superseded", "scoped", "coexist"] = "scoped"
    refined_title: str = ""
    refined_content: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, v: Any) -> float:
        try:
            fv = float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"confidence must be a float in [0.0, 1.0], got {v!r}"
            ) from exc
        if not (0.0 <= fv <= 1.0):
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {fv}"
            )
        return fv
