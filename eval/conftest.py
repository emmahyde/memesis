"""
Shared fixtures for the memesis evaluation suite.

All evals are runnable with mocked LLM calls — no real API calls are made.

Collection hook: adds *_audit.py and *_recall.py to pytest's file discovery so
that curation_audit.py and spontaneous_recall.py are collected alongside the
standard *_test.py pattern in pytest.ini.
"""

import sys
import pytest
from pathlib import Path


def pytest_collect_file(parent, file_path):
    """Extend collection to include *_audit.py and *_recall.py eval files."""
    if file_path.suffix == ".py" and (
        file_path.name.endswith("_audit.py") or file_path.name.endswith("_recall.py")
    ):
        return pytest.Module.from_parent(parent, path=file_path)

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from core.storage import MemoryStore
    from core.lifecycle import LifecycleManager
    from core.retrieval import RetrievalEngine
    _CORE_STORAGE_AVAILABLE = True
except ImportError:
    MemoryStore = None
    LifecycleManager = None
    RetrievalEngine = None
    _CORE_STORAGE_AVAILABLE = False

FIXED_SEED = 42  # for reproducible synthetic data


# ---------------------------------------------------------------------------
# Synthetic memory definitions
# ---------------------------------------------------------------------------

SYNTHETIC_MEMORIES = [
    # ephemeral — 5 entries
    {
        "path": "ephemeral_auth_tokens.md",
        "content": "User asked about OAuth token expiry during session setup.",
        "stage": "ephemeral",
        "title": "OAuth Token Query",
        "summary": "Question about OAuth token expiry",
        "importance": 0.3,
        "tags": ["oauth", "session"],
    },
    {
        "path": "ephemeral_deploy_question.md",
        "content": "Asked how to run migrations before deploying.",
        "stage": "ephemeral",
        "title": "Migration Deploy Question",
        "summary": "Pre-deploy migration question",
        "importance": 0.2,
        "tags": ["deploy", "migration"],
    },
    {
        "path": "ephemeral_test_coverage.md",
        "content": "Requested an explanation of coverage thresholds.",
        "stage": "ephemeral",
        "title": "Test Coverage Question",
        "summary": "Coverage threshold clarification",
        "importance": 0.2,
        "tags": ["testing"],
    },
    {
        "path": "ephemeral_debug_session.md",
        "content": "Debugging a nil-pointer error in the user service.",
        "stage": "ephemeral",
        "title": "Nil Pointer Debug",
        "summary": "Debug session for nil pointer",
        "importance": 0.25,
        "tags": ["debug"],
    },
    {
        "path": "ephemeral_ci_failure.md",
        "content": "CI pipeline failed on lint step; path issue.",
        "stage": "ephemeral",
        "title": "CI Lint Failure",
        "summary": "CI pipeline path issue",
        "importance": 0.2,
        "tags": ["ci"],
    },
    # consolidated — 5 entries
    {
        "path": "decision_ruby_style.md",
        "content": "Prefer single quotes for Ruby string literals unless interpolation is needed.",
        "stage": "consolidated",
        "title": "Ruby String Style",
        "summary": "Ruby style: single quotes preferred",
        "importance": 0.7,
        "tags": ["ruby", "style"],
    },
    {
        "path": "decision_pr_workflow.md",
        "content": "All PRs require two reviewer approvals before merge.",
        "stage": "consolidated",
        "title": "PR Approval Workflow",
        "summary": "Two-approval requirement for PRs",
        "importance": 0.75,
        "tags": ["workflow", "pr"],
    },
    {
        "path": "decision_log_format.md",
        "content": "Use structured JSON logging; avoid raw string log messages.",
        "stage": "consolidated",
        "title": "JSON Logging Standard",
        "summary": "Structured JSON logging policy",
        "importance": 0.7,
        "tags": ["logging"],
    },
    {
        "path": "decision_env_vars.md",
        "content": "Environment variables must be documented in .env.example before use.",
        "stage": "consolidated",
        "title": "Env Var Documentation",
        "summary": "Env var documentation requirement",
        "importance": 0.65,
        "tags": ["environment", "docs"],
    },
    {
        "path": "decision_branch_naming.md",
        "content": "Branch names follow pattern: <type>/<ticket>-<short-description>.",
        "stage": "consolidated",
        "title": "Branch Naming Convention",
        "summary": "Branch naming pattern",
        "importance": 0.6,
        "tags": ["git", "convention"],
    },
    # crystallized — 5 entries
    {
        "path": "crystal_db_connection.md",
        "content": "Database connection pool size is capped at 10 per service instance.",
        "stage": "crystallized",
        "title": "DB Connection Pool",
        "summary": "Pool cap: 10 per instance",
        "importance": 0.85,
        "tags": ["database", "performance"],
    },
    {
        "path": "crystal_api_versioning.md",
        "content": "API versioning uses URL prefix: /v1/, /v2/. Never break existing endpoints.",
        "stage": "crystallized",
        "title": "API Versioning Policy",
        "summary": "URL-prefix versioning, no breaking changes",
        "importance": 0.9,
        "tags": ["api", "versioning"],
    },
    {
        "path": "crystal_deploy_window.md",
        "content": "Production deploys only allowed Tuesday–Thursday, 10am–4pm UTC.",
        "stage": "crystallized",
        "title": "Deploy Window",
        "summary": "Tue–Thu 10am–4pm UTC deploy window",
        "importance": 0.88,
        "tags": ["deploy", "policy"],
    },
    {
        "path": "crystal_error_budget.md",
        "content": "Error budget is 0.1% per month. Exceeding it freezes new feature deployments.",
        "stage": "crystallized",
        "title": "Error Budget Policy",
        "summary": "0.1% monthly error budget",
        "importance": 0.87,
        "tags": ["sre", "policy"],
    },
    {
        "path": "crystal_secret_management.md",
        "content": "Secrets must never be committed to git. Use Vault for all credentials.",
        "stage": "crystallized",
        "title": "Secret Management",
        "summary": "No secrets in git; use Vault",
        "importance": 0.92,
        "tags": ["security", "secrets"],
    },
    # instinctive — 5 entries
    {
        "path": "inst_respond_clearly.md",
        "content": "Always prefer the clearest, simplest explanation over the most technically correct one.",
        "stage": "instinctive",
        "title": "Clarity First",
        "summary": "Prefer clarity over technical correctness",
        "importance": 0.95,
        "tags": ["communication"],
    },
    {
        "path": "inst_minimal_code.md",
        "content": "Write the minimum amount of code needed to solve the problem.",
        "stage": "instinctive",
        "title": "Minimal Code",
        "summary": "Minimalism in code",
        "importance": 0.93,
        "tags": ["coding", "philosophy"],
    },
    {
        "path": "inst_test_first.md",
        "content": "Always ask whether a test exists before implementing a fix.",
        "stage": "instinctive",
        "title": "Test-First Mindset",
        "summary": "Test before fix",
        "importance": 0.94,
        "tags": ["testing", "workflow"],
    },
    {
        "path": "inst_no_assumptions.md",
        "content": "Never assume the user's intent. Ask one clarifying question when ambiguous.",
        "stage": "instinctive",
        "title": "No Silent Assumptions",
        "summary": "Ask when ambiguous",
        "importance": 0.91,
        "tags": ["communication"],
    },
    {
        "path": "inst_cite_sources.md",
        "content": "Back every factual claim with a reference: doc URL, error message, or command output.",
        "stage": "instinctive",
        "title": "Evidence-Backed Claims",
        "summary": "Always cite sources",
        "importance": 0.93,
        "tags": ["communication", "quality"],
    },
]


def seed_store(store) -> list[str]:  # store: MemoryStore when available
    """
    Populate a MemoryStore with all 20 synthetic memories.

    Returns list of created memory IDs in insertion order.
    """
    ids = []
    for spec in SYNTHETIC_MEMORIES:
        mid = store.create(
            path=spec["path"],
            content=spec["content"],
            metadata={
                "stage": spec["stage"],
                "title": spec["title"],
                "summary": spec["summary"],
                "importance": spec["importance"],
                "tags": spec["tags"],
            },
        )
        ids.append(mid)
    return ids


@pytest.fixture
def eval_store(tmp_path):
    """Isolated MemoryStore for each eval. Checkpoints WAL on teardown."""
    if not _CORE_STORAGE_AVAILABLE:
        pytest.skip("core.storage not available — run after Phase 1")
    store = MemoryStore(base_dir=str(tmp_path / "eval_memory"))
    yield store
    store.close()


@pytest.fixture
def seeded_store(tmp_path):
    """Store pre-seeded with 20 synthetic memories. Checkpoints WAL on teardown."""
    if not _CORE_STORAGE_AVAILABLE:
        pytest.skip("core.storage not available — run after Phase 1")
    store = MemoryStore(base_dir=str(tmp_path / "eval_memory"))
    seed_store(store)
    yield store
    store.close()


@pytest.fixture
def eval_engine(eval_store):
    """RetrievalEngine bound to the eval_store."""
    if not _CORE_STORAGE_AVAILABLE:
        pytest.skip("core.storage not available — run after Phase 1")
    return RetrievalEngine(eval_store)


@pytest.fixture
def seeded_engine(seeded_store):
    """RetrievalEngine bound to the seeded_store."""
    if not _CORE_STORAGE_AVAILABLE:
        pytest.skip("core.storage not available — run after Phase 1")
    return RetrievalEngine(seeded_store)


@pytest.fixture
def lifecycle(eval_store):
    """LifecycleManager bound to the eval_store."""
    if not _CORE_STORAGE_AVAILABLE:
        pytest.skip("core.storage not available — run after Phase 1")
    return LifecycleManager(eval_store)
