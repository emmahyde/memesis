"""Pytest configuration and fixtures."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure tests never hit the real Bedrock API
os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)

# Redirect observability JSONL output to a session-scoped temp dir so test
# runs don't pollute backfill-output/observability/ in the real repo.
_TEST_OBS_DIR = Path(tempfile.mkdtemp(prefix="memesis-test-obs-"))
os.environ["MEMESIS_OBS_DIR"] = str(_TEST_OBS_DIR)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db, get_base_dir


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp)


@pytest.fixture
def memory_store(temp_dir):
    """Initialize the Peewee database with temporary storage."""
    init_db(base_dir=str(temp_dir))
    yield temp_dir  # yield the base_dir path
    close_db()


@pytest.fixture
def project_memory_store(temp_dir):
    """Initialize the Peewee database with project context."""
    # Override home to use temp_dir
    original_home = os.environ.get('HOME')
    os.environ['HOME'] = str(temp_dir)

    try:
        init_db(project_context='/Users/test/my-project')
        yield temp_dir
    finally:
        close_db()
        if original_home:
            os.environ['HOME'] = original_home
        else:
            del os.environ['HOME']
