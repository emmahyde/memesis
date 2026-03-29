"""Pytest configuration and fixtures."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure tests never hit the real Bedrock API
os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.storage import MemoryStore


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp)


@pytest.fixture
def memory_store(temp_dir):
    """Create a MemoryStore instance with temporary storage."""
    store = MemoryStore(base_dir=str(temp_dir))
    yield store
    store.close()


@pytest.fixture
def project_memory_store(temp_dir):
    """Create a MemoryStore instance with project context."""
    # Override home to use temp_dir
    original_home = os.environ.get('HOME')
    os.environ['HOME'] = str(temp_dir)

    try:
        store = MemoryStore(project_context='/Users/test/my-project')
        yield store
    finally:
        if original_home:
            os.environ['HOME'] = original_home
        else:
            del os.environ['HOME']
