"""
Eval compiler: free-text description → EvalSpec → pytest source file.

D-09: Hybrid path — LLM extracts spec from free text; deterministic template
      compiler renders valid pytest from the spec. LLM-generated assertion
      fallback is deferred (see TODO comment in _render_llm_fallback).

D-10: Match modes supported:
  entity_presence   — assert each entity appears in some memory content/title/tags
  semantic_similarity — cosine ≥ threshold via VecStore.search_vector
  polarity_match    — assert any matching memory has affect_valence == spec.polarity
  absence           — assert NO memory contains the entities (inverted entity_presence)

D-11: Compiled evals land at eval/recall/<slug>_recall.py, collected by
      eval/conftest.py via its pytest_collect_file hook (*_recall.py pattern).

D-12: Failure semantics = pass/fail per expected memory (boolean pytest result).
      Structured diagnostic delta is the caller's responsibility (scripts/evolve.py).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from core.llm import call_llm

logger = logging.getLogger(__name__)

MatchMode = Literal["entity_presence", "semantic_similarity", "polarity_match", "absence"]

# LLM prompt for spec extraction
_SPEC_EXTRACT_PROMPT = """\
You are a test spec extractor. Given a description of an expected memory, return JSON with exactly these fields:

{{
  "slug": "<short-snake-case identifier, no spaces, e.g. 'oauth-token-expiry'>",
  "expected_entities": ["<entity1>", "<entity2>"],
  "polarity": "<positive|negative|corrective|neutral|mixed or null>",
  "stage_target": "<ephemeral|consolidated|crystallized|instinctive or null>",
  "match_mode": "<entity_presence|semantic_similarity|polarity_match|absence>"
}}

Rules:
- slug: lowercase, hyphens only, ≤40 chars
- expected_entities: the key named concepts/terms the memory should mention (1–5 items)
- polarity: only set if the description implies a sentiment/valence; else null
- stage_target: only set if the description mentions a lifecycle stage; else null
- match_mode: pick the most appropriate:
    entity_presence — default; memory should contain these entities
    semantic_similarity — description asks for semantic closeness
    polarity_match — description is about emotional valence/affect
    absence — description says the memory should NOT exist

Description:
{description}

Return ONLY the JSON object, no explanation.
"""


@dataclass
class EvalSpec:
    slug: str
    expected_entities: list[str]
    polarity: str | None
    stage_target: str | None
    match_mode: MatchMode
    # Extra metadata (ignored by template compiler, useful for diagnostics)
    description: str = field(default="", compare=False, repr=False)


def extract_spec_from_text(description: str) -> EvalSpec:
    """
    Call call_llm() to extract an EvalSpec from free-text description.

    Returns a deterministic EvalSpec (no randomness beyond the LLM call).
    Raises ValueError if the LLM response cannot be parsed into a valid spec.
    """
    prompt = _SPEC_EXTRACT_PROMPT.format(description=description)
    raw = call_llm(prompt)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt to extract JSON from surrounding text
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ValueError(f"LLM response is not valid JSON: {raw!r}") from exc
        else:
            raise ValueError(f"LLM response contains no JSON object: {raw!r}")

    # Validate and coerce fields
    slug = str(data.get("slug", "unnamed-eval")).strip().lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)[:40].strip("-") or "unnamed-eval"

    expected_entities = data.get("expected_entities", [])
    if not isinstance(expected_entities, list):
        expected_entities = [str(expected_entities)]
    expected_entities = [str(e) for e in expected_entities]

    polarity = data.get("polarity") or None
    stage_target = data.get("stage_target") or None

    raw_mode = data.get("match_mode", "entity_presence")
    valid_modes: set[MatchMode] = {
        "entity_presence", "semantic_similarity", "polarity_match", "absence"
    }
    if raw_mode not in valid_modes:
        logger.warning("Unknown match_mode %r from LLM; defaulting to entity_presence", raw_mode)
        raw_mode = "entity_presence"

    return EvalSpec(
        slug=slug,
        expected_entities=expected_entities,
        polarity=polarity,
        stage_target=stage_target,
        match_mode=raw_mode,
        description=description,
    )


# ---------------------------------------------------------------------------
# Template compiler
# ---------------------------------------------------------------------------

def compile_to_pytest(spec: EvalSpec, replay_store_path: str) -> str:
    """
    Render a valid pytest source string from an EvalSpec.

    The compiled file:
    - Initialises the DB at replay_store_path via init_db()
    - Queries Memory rows via Memory.select()
    - Asserts the match_mode condition
    - Is named <slug>_recall.py and collected by eval/conftest.py

    Returns a string of Python source (not written to disk — caller does that).
    """
    body = _render_match_mode(spec)
    return _render_module(spec, replay_store_path, body)


def _render_module(spec: EvalSpec, replay_store_path: str, assertion_body: str) -> str:
    """Wrap the assertion body in a full pytest module."""
    entities_repr = repr(spec.expected_entities)
    polarity_repr = repr(spec.polarity)
    stage_repr = repr(spec.stage_target)
    mode_repr = repr(spec.match_mode)

    return f'''\
"""
Auto-generated eval for: {spec.slug}
Description: {spec.description!r}
Match mode:  {spec.match_mode}
Stage target: {spec.stage_target}

DO NOT EDIT — regenerate via core.eval_compile.compile_to_pytest().
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure project root is on path when run standalone
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.database import init_db, close_db
from core.models import Memory


REPLAY_STORE_PATH = os.environ.get("MEMESIS_REPLAY_STORE", {repr(replay_store_path)})
EXPECTED_ENTITIES = {entities_repr}
POLARITY = {polarity_repr}
STAGE_TARGET = {stage_repr}
MATCH_MODE = {mode_repr}


@pytest.fixture(autouse=True, scope="module")
def _db():
    """Bind the Peewee database to the replay store for this eval module."""
    init_db(base_dir=REPLAY_STORE_PATH)
    yield
    close_db()


{assertion_body}
'''


def _render_match_mode(spec: EvalSpec) -> str:
    """Dispatch to the per-match-mode renderer."""
    if spec.match_mode == "entity_presence":
        return _render_entity_presence(spec)
    if spec.match_mode == "absence":
        return _render_absence(spec)
    if spec.match_mode == "polarity_match":
        return _render_polarity_match(spec)
    if spec.match_mode == "semantic_similarity":
        return _render_semantic_similarity(spec)
    # Should never reach here given validation in extract_spec_from_text,
    # but provide a TODO stub per D-09 deferred path.
    return _render_llm_fallback(spec)


def _render_entity_presence(spec: EvalSpec) -> str:
    stage_filter = (
        f'\n    memories = memories.where(Memory.stage == {spec.stage_target!r})'
        if spec.stage_target else ""
    )
    return f'''\
def test_{_safe_fn(spec.slug)}_entity_presence():
    """Assert each expected entity appears in at least one memory."""
    memories = Memory.select().where(Memory.archived_at.is_null()){stage_filter}

    def _memory_text(m) -> str:
        parts = [m.content or "", m.title or "", m.summary or ""]
        tags = []
        if m.tags:
            try:
                tags = json.loads(m.tags)
            except (ValueError, TypeError):
                pass
        parts.extend(tags)
        return " ".join(parts).lower()

    all_text = [_memory_text(m) for m in memories]

    for entity in EXPECTED_ENTITIES:
        assert any(entity.lower() in text for text in all_text), (
            f"Entity {{entity!r}} not found in any memory. "
            f"Checked {{len(all_text)}} memories."
        )
'''


def _render_absence(spec: EvalSpec) -> str:
    stage_filter = (
        f'\n    memories = memories.where(Memory.stage == {spec.stage_target!r})'
        if spec.stage_target else ""
    )
    return f'''\
def test_{_safe_fn(spec.slug)}_absence():
    """Assert NO memory contains the expected entities (absence assertion)."""
    memories = Memory.select().where(Memory.archived_at.is_null()){stage_filter}

    def _memory_text(m) -> str:
        parts = [m.content or "", m.title or "", m.summary or ""]
        tags = []
        if m.tags:
            try:
                tags = json.loads(m.tags)
            except (ValueError, TypeError):
                pass
        parts.extend(tags)
        return " ".join(parts).lower()

    all_text = [_memory_text(m) for m in memories]

    for entity in EXPECTED_ENTITIES:
        matches = [text for text in all_text if entity.lower() in text]
        assert not matches, (
            f"Entity {{entity!r}} was found in {{len(matches)}} memory/memories "
            f"but should be absent."
        )
'''


def _render_polarity_match(spec: EvalSpec) -> str:
    polarity_val = spec.polarity or "neutral"
    stage_filter = (
        f'\n    memories = memories.where(Memory.stage == {spec.stage_target!r})'
        if spec.stage_target else ""
    )
    return f'''\
def test_{_safe_fn(spec.slug)}_polarity_match():
    """Assert any memory containing the entities has affect_valence == POLARITY."""
    memories = Memory.select().where(Memory.archived_at.is_null()){stage_filter}

    def _memory_text(m) -> str:
        parts = [m.content or "", m.title or "", m.summary or ""]
        tags = []
        if m.tags:
            try:
                tags = json.loads(m.tags)
            except (ValueError, TypeError):
                pass
        parts.extend(tags)
        return " ".join(parts).lower()

    # Find memories that match any entity
    matching = [
        m for m in memories
        if any(e.lower() in _memory_text(m) for e in EXPECTED_ENTITIES)
    ]

    assert matching, (
        f"No memories found containing any of {{EXPECTED_ENTITIES}}. "
        f"Cannot verify polarity."
    )

    polarity_hits = [m for m in matching if m.affect_valence == {polarity_val!r}]
    assert polarity_hits, (
        f"Found {{len(matching)}} memories with matching entities but none have "
        f"affect_valence == {{POLARITY!r}}. "
        f"Actual valences: {{[m.affect_valence for m in matching]}}"
    )
'''


def _render_semantic_similarity(spec: EvalSpec) -> str:
    query_str = " ".join(spec.expected_entities)
    stage_filter = (
        f'\n    memories = list(memories.where(Memory.stage == {spec.stage_target!r}))'
        if spec.stage_target else "\n    memories = list(memories)"
    )
    return f'''\
def test_{_safe_fn(spec.slug)}_semantic_similarity():
    """Assert cosine similarity ≥ threshold via VecStore.search_vector."""
    from core.database import get_vec_store
    from core.embeddings import embed_text

    SIMILARITY_THRESHOLD = 0.5  # cosine distance ≤ 1 - threshold
    QUERY = {query_str!r}

    vec_store = get_vec_store()
    if vec_store is None or not vec_store.available:
        pytest.skip("VecStore unavailable — semantic_similarity eval requires embeddings")

    query_embedding = embed_text(QUERY)
    if query_embedding is None:
        pytest.skip("embed_text returned None — embedding service unavailable")

    memories = Memory.select().where(Memory.archived_at.is_null()){stage_filter}
    memory_ids = {{m.id for m in memories}}

    results = vec_store.search_vector(query_embedding, k=10)

    hits = [r for r in results if r["memory_id"] in memory_ids]

    assert hits, (
        f"No memories found via semantic search for query {{QUERY!r}}."
    )

    # sqlite-vec returns distance (lower = more similar); convert to similarity
    best_distance = min(r["distance"] for r in hits)
    best_similarity = 1.0 - best_distance

    assert best_similarity >= SIMILARITY_THRESHOLD, (
        f"Best semantic similarity {{best_similarity:.3f}} < threshold "
        f"{{SIMILARITY_THRESHOLD}} for query {{QUERY!r}}."
    )

    # TODO: LLM-generated assertion fallback
'''


def _render_llm_fallback(spec: EvalSpec) -> str:
    """Stub for assertions the template cannot express."""
    return f'''\
def test_{_safe_fn(spec.slug)}_eval():
    """Placeholder — match_mode not recognised by template compiler."""
    # TODO: LLM-generated assertion fallback
    pytest.skip(
        "match_mode {spec.match_mode!r} has no template; "
        "replace this stub with a hand-written assertion."
    )
'''


def _safe_fn(slug: str) -> str:
    """Convert a slug to a valid Python function-name suffix."""
    return re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_") or "eval"
