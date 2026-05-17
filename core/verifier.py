"""
Verifier sweep — auto-archive memories whose verification predicate no longer holds.

A memory may carry a declarative predicate (``verify_kind`` + ``verify_arg``).
The hourly cron evaluates each predicate; a memory whose predicate *fails*
(definitely no longer true) is archived. Inconclusive evaluations never archive.

Predicate kinds (``verify_kind``):
  grep_present  — verify_arg is a regex; holds while the pattern is present in the repo
  grep_absent   — verify_arg is a regex; holds while the pattern is absent from the repo
  file_exists   — verify_arg is a repo-relative path; holds while the file exists
  test_passes   — verify_arg is a pytest node id; holds while that test passes

Evaluation is tri-state: True (holds), False (failed → archive), None (inconclusive
→ skip). Anything that cannot be evaluated safely (missing project dir, git error,
timeout) is inconclusive by design — the sweep never archives on uncertainty.

All predicate execution uses argument-list subprocess calls (no shell), bounded by
a timeout. See CLAUDE.md Rule 1 — archiving goes through the Memory model + a
ConsolidationLog row, mirroring core/relevance.py:archive_stale.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from core.models import ConsolidationLog, Memory

logger = logging.getLogger(__name__)

VERIFY_KINDS = frozenset({"grep_present", "grep_absent", "file_exists", "test_passes"})

_SUBPROCESS_TIMEOUT = 30  # seconds — bounds any single predicate evaluation


def _project_root(memory: Memory) -> Path | None:
    """Resolve the repo root to evaluate a memory's predicate against.

    Uses the memory's ``project`` column. Returns None when it is unset or not a
    directory — the caller treats that as inconclusive.
    """
    project = getattr(memory, "project", None)
    if not project:
        return None
    root = Path(project).expanduser()
    return root if root.is_dir() else None


def _git_grep_found(pattern: str, root: Path) -> bool | None:
    """Return True/False if a regex is present in the git-tracked tree, None on error."""
    try:
        result = subprocess.run(
            ["git", "grep", "-I", "-q", "-E", "--", pattern],
            cwd=str(root),
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("verifier: git grep failed for %r in %s: %s", pattern, root, exc)
        return None
    # git grep: 0 = match found, 1 = no match, other = error
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    logger.warning("verifier: git grep exit %d for %r in %s", result.returncode, pattern, root)
    return None


def _test_passes(node_id: str, root: Path) -> bool | None:
    """Return True/False if a pytest node id passes, None on error."""
    try:
        result = subprocess.run(
            ["uv", "run", "--extra", "dev", "python", "-m", "pytest", node_id, "-q", "-x"],
            cwd=str(root),
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT * 6,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("verifier: pytest failed to run %r: %s", node_id, exc)
        return None
    return result.returncode == 0


def evaluate_predicate(verify_kind: str, verify_arg: str, memory: Memory) -> bool | None:
    """Evaluate one memory's predicate.

    Returns True (predicate holds — memory still valid), False (predicate failed —
    memory is stale), or None (inconclusive — do not act).
    """
    if verify_kind not in VERIFY_KINDS:
        logger.warning("verifier: unknown verify_kind %r on memory %s", verify_kind, memory.id)
        return None
    if not verify_arg:
        return None

    root = _project_root(memory)
    if root is None:
        return None

    if verify_kind == "grep_present":
        return _git_grep_found(verify_arg, root)

    if verify_kind == "grep_absent":
        found = _git_grep_found(verify_arg, root)
        return None if found is None else (not found)

    if verify_kind == "file_exists":
        # Constrain to within the project root — no traversal outside it.
        target = (root / verify_arg).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            logger.warning("verifier: file_exists arg %r escapes project root", verify_arg)
            return None
        return target.exists()

    if verify_kind == "test_passes":
        return _test_passes(verify_arg, root)

    return None


def run_verifier_sweep(project_context: str | None = None) -> dict:
    """Evaluate every memory carrying a predicate; archive those that fail.

    Returns a counts dict: ``checked``, ``archived``, ``inconclusive``, ``holds``.
    """
    counts = {"checked": 0, "archived": 0, "inconclusive": 0, "holds": 0}

    query = Memory.active().where(Memory.verify_kind.is_null(False))
    if project_context:
        query = query.where(Memory.project == project_context)

    for memory in query:
        counts["checked"] += 1
        verdict = evaluate_predicate(memory.verify_kind, memory.verify_arg or "", memory)
        if verdict is None:
            counts["inconclusive"] += 1
            continue
        if verdict is True:
            counts["holds"] += 1
            continue

        # verdict is False — predicate failed, archive the stale memory.
        now = datetime.now().isoformat()
        try:
            Memory.update(archived_at=now).where(Memory.id == memory.id).execute()
            ConsolidationLog.create(
                timestamp=now,
                action="deprecated",
                memory_id=memory.id,
                from_stage=memory.stage,
                to_stage="archived",
                rationale=(
                    f"Verifier predicate failed: {memory.verify_kind}"
                    f"({memory.verify_arg!r})"
                ),
            )
            counts["archived"] += 1
            logger.info(
                "verifier: archived %s (%s) — predicate %s(%r) failed",
                memory.title or "untitled",
                memory.id,
                memory.verify_kind,
                memory.verify_arg,
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the sweep
            logger.warning("verifier: failed to archive %s: %s", memory.id, exc)

    logger.info("verifier sweep: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return counts
