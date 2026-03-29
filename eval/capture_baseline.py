"""
Baseline capture script for the memesis eval harness.

Runs the full eval suite (internal metrics + LongMemEval fixture) and writes
a snapshot to .planning/eval-baseline.json.

Usage:
    python3 eval/capture_baseline.py
    python3 eval/capture_baseline.py --phase "07-after-rrf"
    python3 eval/capture_baseline.py --output path/to/output.json

When core.storage is not available (Phase 1 not yet complete), all retrieval
and consolidation metrics are recorded as 0.0 with
captured_without_core_storage: true.  The script always succeeds.

Importable API:
    from eval.capture_baseline import capture_baseline, run_internal_evals
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the repo root is on sys.path so `eval.*` and `core.*` imports resolve
# whether the script is run as `python3 eval/capture_baseline.py` or via
# `python3 -m eval.capture_baseline`.
_SCRIPT_DIR = Path(__file__).parent
_REPO_ROOT_EARLY = _SCRIPT_DIR.parent
if str(_REPO_ROOT_EARLY) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_EARLY))

# ---------------------------------------------------------------------------
# Optional core.storage import — absent until Phase 1 completes
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Internal eval imports (always available — no core.* dependencies)
# ---------------------------------------------------------------------------

from eval.metrics import precision_at_k, mrr as mrr_metric, prune_accuracy, injection_utility_rate
from eval.longmemeval_adapter import LongMemEvalAdapter, LONGMEMEVAL_FIXTURE

# ---------------------------------------------------------------------------
# Default output path
# ---------------------------------------------------------------------------

_REPO_ROOT = _REPO_ROOT_EARLY
_DEFAULT_OUTPUT = _REPO_ROOT / ".planning" / "eval-baseline.json"


# ---------------------------------------------------------------------------
# Zero-value snapshots (used when core.storage absent)
# ---------------------------------------------------------------------------

_ZERO_RETRIEVAL = {
    "precision_at_1": 0.0,
    "precision_at_5": 0.0,
    "precision_at_10": 0.0,
    "mrr": 0.0,
    "injection_utility_rate": 0.0,
}

_ZERO_CONSOLIDATION = {
    "prune_precision": 0.0,
    "prune_recall": 0.0,
    "prune_f1": 0.0,
}

_LONGMEMEVAL_CATEGORIES = [
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
]


# ---------------------------------------------------------------------------
# Core retrieval metrics (requires core.storage)
# ---------------------------------------------------------------------------

def run_internal_evals() -> tuple[dict, dict]:
    """
    Run retrieval and consolidation metrics against a live MemoryStore.

    Returns:
        Tuple of (retrieval_dict, consolidation_dict).
        Both match the JSON schema in eval-baseline.json.

    Raises:
        RuntimeError if core.storage is not available.
    """
    if not _CORE_STORAGE_AVAILABLE:
        raise RuntimeError("core.storage not available")

    import tempfile

    # Import SYNTHETIC_MEMORIES from conftest without triggering pytest
    from eval.conftest import SYNTHETIC_MEMORIES, seed_store

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(base_dir=str(Path(tmp) / "eval_memory"))
        try:
            ids = seed_store(store)

            # Build a lookup: id -> spec, and path -> id
            path_to_id = {}
            id_to_spec = {}
            for mid, spec in zip(ids, SYNTHETIC_MEMORIES):
                path_to_id[spec["path"]] = mid
                id_to_spec[mid] = spec

            crystallized_ids = {
                mid for mid, spec in id_to_spec.items()
                if spec["stage"] == "crystallized"
            }

            # --- Precision@k / MRR ---
            # inject_for_session() and active_search() don't exist yet (Phase 7).
            # For now we use a stub that returns [] — same as "no retrieval".
            # When Phase 7 lands, swap these stubs for real engine calls.

            # Stub: no results returned means P@k = 0, MRR = 0
            retrieved: list[str] = []
            p1 = precision_at_k(retrieved, crystallized_ids, k=1)
            p5 = precision_at_k(retrieved, crystallized_ids, k=5)
            p10 = precision_at_k(retrieved, crystallized_ids, k=10)
            mrr_val = mrr_metric(retrieved, crystallized_ids)

            # --- Injection utility rate ---
            # Stub: no injected entries → 0.0
            injected: list[str] = []
            used: set[str] = set()
            iur = injection_utility_rate(injected, used)

            retrieval = {
                "precision_at_1": p1,
                "precision_at_5": p5,
                "precision_at_10": p10,
                "mrr": mrr_val,
                "injection_utility_rate": iur,
            }

            # --- Prune accuracy ---
            # Use curation_audit fixture expectations as ground truth.
            # Crystallized (5) should be kept; ephemeral (5) should be pruned.
            # Consolidated and instinctive fall to the pruner's discretion.
            # Without a real prune call (Phase 5), stub kept=[] → all zeros.
            kept: list[str] = []
            true_keep: set[str] = crystallized_ids
            pruned_list: list[str] = []
            # instinctive memories expected to survive too
            instinctive_ids = {
                mid for mid, spec in id_to_spec.items()
                if spec["stage"] == "instinctive"
            }
            true_prune_ids = {
                mid for mid, spec in id_to_spec.items()
                if spec["stage"] == "ephemeral"
            }
            prune_stats = prune_accuracy(kept, true_keep, pruned_list, true_prune_ids)

            consolidation = {
                "prune_precision": prune_stats["precision"],
                "prune_recall": prune_stats["recall"],
                "prune_f1": prune_stats["f1"],
            }

        finally:
            store.close()

    return retrieval, consolidation


# ---------------------------------------------------------------------------
# LongMemEval runner (always available)
# ---------------------------------------------------------------------------

def run_longmemeval(retrieval_fn=None) -> dict:
    """
    Run the 10-question LongMemEval fixture and return aggregate results.

    Args:
        retrieval_fn: Optional callable (query: str) -> list[str].
                      Defaults to stub that returns [].

    Returns:
        Dict with keys: accuracy, by_category, total.
    """
    if retrieval_fn is None:
        retrieval_fn = lambda q: []

    adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
    results = adapter.run_fixture()
    agg = adapter.aggregate(results)

    # Ensure all fixture categories are present in by_category (even if 0.0)
    by_cat = dict(agg.get("by_category", {}))
    for cat in _LONGMEMEVAL_CATEGORIES:
        by_cat.setdefault(cat, 0.0)

    return {
        "accuracy": agg["accuracy"],
        "by_category": by_cat,
        "total": agg["total"],
    }


# ---------------------------------------------------------------------------
# Top-level capture function
# ---------------------------------------------------------------------------

def capture_baseline(
    phase: str = "00.5-baseline",
    output_path: Path | None = None,
) -> dict:
    """
    Capture the current eval baseline and write it to output_path.

    When core.storage is unavailable, all retrieval/consolidation metrics are
    recorded as 0.0 with captured_without_core_storage: true.

    Args:
        phase:       Label for this snapshot (e.g. "07-after-rrf").
        output_path: Destination JSON file. Defaults to .planning/eval-baseline.json.

    Returns:
        The dict that was written to disk.
    """
    if output_path is None:
        output_path = _DEFAULT_OUTPUT

    output_path = Path(output_path)

    # --- Retrieval + consolidation metrics ---
    if _CORE_STORAGE_AVAILABLE:
        try:
            retrieval, consolidation = run_internal_evals()
            without_storage = False
        except Exception as exc:
            print(f"[capture_baseline] core.storage available but eval failed: {exc}", file=sys.stderr)
            retrieval = dict(_ZERO_RETRIEVAL)
            consolidation = dict(_ZERO_CONSOLIDATION)
            without_storage = True
    else:
        retrieval = dict(_ZERO_RETRIEVAL)
        consolidation = dict(_ZERO_CONSOLIDATION)
        without_storage = True

    # --- LongMemEval ---
    longmemeval = run_longmemeval()

    # --- Assemble snapshot ---
    snapshot = {
        "phase": phase,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "captured_without_core_storage": without_storage,
        "retrieval": retrieval,
        "consolidation": consolidation,
        "longmemeval": longmemeval,
    }

    # --- Write ---
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2)

    return snapshot


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture eval baseline and write to eval-baseline.json."
    )
    parser.add_argument(
        "--phase",
        default="00.5-baseline",
        help="Phase label for this snapshot (default: 00.5-baseline)",
    )
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help=f"Output JSON path (default: {_DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    print(f"[capture_baseline] core.storage available: {_CORE_STORAGE_AVAILABLE}")
    snapshot = capture_baseline(phase=args.phase, output_path=Path(args.output))

    print(f"[capture_baseline] Written to: {args.output}")
    print(f"[capture_baseline] phase: {snapshot['phase']}")
    print(f"[capture_baseline] captured_at: {snapshot['captured_at']}")
    print(f"[capture_baseline] captured_without_core_storage: {snapshot['captured_without_core_storage']}")
    print(f"[capture_baseline] longmemeval.accuracy: {snapshot['longmemeval']['accuracy']:.4f}")
    print(f"[capture_baseline] longmemeval.total: {snapshot['longmemeval']['total']}")


if __name__ == "__main__":
    _main()
