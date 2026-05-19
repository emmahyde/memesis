"""
Auto-generated eval for: cc-transcript-path-correction
Description: 'Emma corrected that real CC transcripts live at ~/.claude/projects/<slug>/<uuid>.jsonl, not ~/.claude/transcripts'
Match mode:  entity_presence
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


REPLAY_STORE_PATH = os.environ.get("MEMESIS_REPLAY_STORE", '/var/folders/1m/fmknnw9n3cxf_slfs6sw832h0000gn/T/memesis-replay-ll9rqvp_')
EXPECTED_ENTITIES = ['~/.claude/projects', '<slug>/<uuid>.jsonl', '~/.claude/transcripts']
POLARITY = 'corrective'
STAGE_TARGET = None
MATCH_MODE = 'entity_presence'


@pytest.fixture(autouse=True, scope="module")
def _db():
    """Bind the Peewee database to the replay store for this eval module."""
    init_db(base_dir=REPLAY_STORE_PATH)
    yield
    close_db()


def test_cc_transcript_path_correction_entity_presence():
    """Assert each expected entity appears in at least one memory."""
    memories = Memory.select().where(Memory.archived_at.is_null())

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
            f"Entity {entity!r} not found in any memory. "
            f"Checked {len(all_text)} memories."
        )

