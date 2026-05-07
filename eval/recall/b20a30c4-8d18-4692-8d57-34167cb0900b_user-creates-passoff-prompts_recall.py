"""
Auto-generated eval for: user-creates-passoff-prompts
Description: 'User creates passoff prompts'
Match mode:  semantic_similarity
Stage target: None

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


REPLAY_STORE_PATH = os.environ.get("MEMESIS_REPLAY_STORE", '/var/folders/1m/fmknnw9n3cxf_slfs6sw832h0000gn/T/memesis-replay-6vb7xqvt')
EXPECTED_ENTITIES = ['passoff prompts', 'user', 'creates']
POLARITY = None
STAGE_TARGET = None
MATCH_MODE = 'semantic_similarity'


@pytest.fixture(autouse=True, scope="module")
def _db():
    """Bind the Peewee database to the replay store for this eval module."""
    init_db(base_dir=REPLAY_STORE_PATH)
    yield
    close_db()


def test_user_creates_passoff_prompts_semantic_similarity():
    """Assert cosine similarity ≥ threshold via VecStore.search_vector."""
    from core.database import get_vec_store
    from core.embeddings import embed_text

    SIMILARITY_THRESHOLD = 0.5  # cosine distance ≤ 1 - threshold
    QUERY = 'passoff prompts user creates'

    vec_store = get_vec_store()
    if vec_store is None or not vec_store.available:
        pytest.skip("VecStore unavailable — semantic_similarity eval requires embeddings")

    query_embedding = embed_text(QUERY)
    if query_embedding is None:
        pytest.skip("embed_text returned None — embedding service unavailable")

    memories = Memory.select().where(Memory.archived_at.is_null())
    memories = list(memories)
    memory_ids = {m.id for m in memories}

    results = vec_store.search_vector(query_embedding, k=10)

    hits = [r for r in results if r["memory_id"] in memory_ids]

    assert hits, (
        f"No memories found via semantic search for query {QUERY!r}."
    )

    # sqlite-vec returns distance (lower = more similar); convert to similarity
    best_distance = min(r["distance"] for r in hits)
    best_similarity = 1.0 - best_distance

    assert best_similarity >= SIMILARITY_THRESHOLD, (
        f"Best semantic similarity {best_similarity:.3f} < threshold "
        f"{SIMILARITY_THRESHOLD} for query {QUERY!r}."
    )

    # TODO: LLM-generated assertion fallback

