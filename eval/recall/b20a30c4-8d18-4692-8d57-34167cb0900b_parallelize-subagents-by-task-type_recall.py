"""
Auto-generated eval for: parallelize-subagents-by-task-type
Description: 'Claude should parallelize independent work by spawning sonnet and haiku subagents for different task types'
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


REPLAY_STORE_PATH = os.environ.get("MEMESIS_REPLAY_STORE", '/var/folders/1m/fmknnw9n3cxf_slfs6sw832h0000gn/T/memesis-replay-azwh1s7c')
EXPECTED_ENTITIES = ['parallelize', 'subagents', 'sonnet', 'haiku', 'independent']
POLARITY = None
STAGE_TARGET = None
MATCH_MODE = 'semantic_similarity'


@pytest.fixture(autouse=True, scope="module")
def _db():
    """Bind the Peewee database to the replay store for this eval module."""
    init_db(base_dir=REPLAY_STORE_PATH)
    yield
    close_db()


def test_parallelize_subagents_by_task_type_semantic_similarity():
    """Assert cosine similarity ≥ threshold via VecStore.search_vector."""
    from core.database import get_vec_store
    from core.embeddings import embed_text

    SIMILARITY_THRESHOLD = 0.5  # cosine distance ≤ 1 - threshold
    QUERY = 'parallelize subagents sonnet haiku independent'

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

    if os.environ.get("EVOLVE_VERBOSE"):
        from core.database import get_db_path
        from core.models import db as _db
        try:
            n_vec = _db.execute_sql("SELECT COUNT(*) FROM vec_memories").fetchone()[0]
        except Exception as _e:
            n_vec = f"err:{_e}"
        print(f"\n[eval-debug] db_path={get_db_path()}")
        print(f"[eval-debug] vec_memories rows: {n_vec}")
        print(f"[eval-debug] Memory rows (active): {len(memory_ids)} ids={memory_ids}")
        print(f"[eval-debug] search_vector returned {len(results)}: {results}")

    hits = [r for r in results if r["memory_id"] in memory_ids]

    assert hits, (
        f"No memories found via semantic search for query {QUERY!r}."
    )

    # sqlite-vec returns L2 distance on unit vectors; convert to cosine similarity:
    #   cosine_sim = 1 - L2_dist² / 2
    best_distance = min(r["distance"] for r in hits)
    best_similarity = 1.0 - (best_distance ** 2) / 2.0

    assert best_similarity >= SIMILARITY_THRESHOLD, (
        f"Best semantic similarity {best_similarity:.3f} < threshold "
        f"{SIMILARITY_THRESHOLD} for query {QUERY!r}."
    )

    # TODO: LLM-generated assertion fallback

