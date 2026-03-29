"""
Phase verifier hook for the memesis eval harness.

Loads .planning/eval-baseline.json, runs the current eval suite, computes
metric deltas, prints a table, and exits non-zero if any metric regresses
more than 0.05 below the baseline.

CLI usage:
    python3 eval/verify_phase.py
    python3 eval/verify_phase.py --phase "07-after-rrf"

Importable usage:
    from eval.verify_phase import verify_against_baseline
    exit_code = verify_against_baseline(phase="07-after-rrf")
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path when run as a script.
_SCRIPT_DIR = Path(__file__).parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# NOTE: Do NOT import core.* at module level.
from eval.capture_baseline import capture_baseline  # noqa: E402

_DEFAULT_BASELINE = _REPO_ROOT / ".planning" / "eval-baseline.json"
_REGRESSION_THRESHOLD = 0.05


# ---------------------------------------------------------------------------
# Metric flattening helpers
# ---------------------------------------------------------------------------

def _flatten(snapshot: dict) -> dict[str, float]:
    """
    Flatten a baseline/current snapshot into a dotted-key metric dict.

    Keys:
        retrieval.precision_at_1, retrieval.mrr, etc.
        consolidation.prune_precision, consolidation.prune_f1, etc.
        longmemeval.accuracy
    """
    flat: dict[str, float] = {}
    for section in ("retrieval", "consolidation"):
        for k, v in snapshot.get(section, {}).items():
            flat[f"{section}.{k}"] = float(v)
    lm = snapshot.get("longmemeval", {})
    flat["longmemeval.accuracy"] = float(lm.get("accuracy", 0.0))
    return flat


# ---------------------------------------------------------------------------
# Table printer
# ---------------------------------------------------------------------------

def _print_table(phase: str, baseline_flat: dict[str, float], current_flat: dict[str, float]) -> None:
    """Print a human-readable delta table to stdout."""
    print(f"\nPhase: {phase}")
    header = f"{'Metric':<35} {'Baseline':>10} {'Current':>10} {'Delta':>10}"
    print(header)
    print("-" * len(header))

    for key in sorted(baseline_flat.keys()):
        base_val = baseline_flat.get(key, 0.0)
        cur_val = current_flat.get(key, 0.0)
        delta = cur_val - base_val

        if delta > 0.001:
            label = "IMPROVED"
        elif delta < -_REGRESSION_THRESHOLD:
            label = "REGRESSION"
        elif abs(delta) <= 0.001:
            label = "UNCHANGED"
        else:
            label = "MINOR DROP"

        sign = "+" if delta >= 0 else ""
        print(
            f"{key:<35} {base_val:>10.4f} {cur_val:>10.4f} "
            f"{sign}{delta:>9.4f}  {label}"
        )
    print()


# ---------------------------------------------------------------------------
# Core verifier
# ---------------------------------------------------------------------------

def verify_against_baseline(
    phase: str = "current",
    baseline_path: Path | None = None,
) -> int:
    """
    Compare current eval scores against the stored baseline.

    Args:
        phase:         Label for the current snapshot (e.g. "07-after-rrf").
        baseline_path: Path to baseline JSON. Defaults to .planning/eval-baseline.json.

    Returns:
        0 if no regression (improvement or flat).
        1 if any metric drops more than REGRESSION_THRESHOLD (0.05) below baseline.

    If baseline_path does not exist, prints a warning and returns 0.
    """
    if baseline_path is None:
        baseline_path = _DEFAULT_BASELINE

    baseline_path = Path(baseline_path)

    # --- Load baseline ---
    if not baseline_path.exists():
        print(
            f"[verify_phase] WARNING: baseline not found at {baseline_path}. "
            "No regression detected (no baseline to regress against).",
            file=sys.stderr,
        )
        return 0

    with open(baseline_path, encoding="utf-8") as fh:
        baseline = json.load(fh)

    baseline_flat = _flatten(baseline)

    # --- Capture current scores ---
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        current = capture_baseline(phase=phase, output_path=tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    current_flat = _flatten(current)

    # --- Print delta table ---
    _print_table(phase, baseline_flat, current_flat)

    # --- Check regressions ---
    regressions = []
    for key, base_val in baseline_flat.items():
        cur_val = current_flat.get(key, 0.0)
        delta = cur_val - base_val
        if delta < -_REGRESSION_THRESHOLD:
            regressions.append((key, delta))

    if regressions:
        for key, delta in regressions:
            print(f"REGRESSION DETECTED: {key} dropped {delta:.4f}", file=sys.stderr)
        return 1

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify current eval scores against stored baseline."
    )
    parser.add_argument(
        "--phase",
        default="current",
        help="Label for the current snapshot (default: current)",
    )
    parser.add_argument(
        "--baseline",
        default=str(_DEFAULT_BASELINE),
        help=f"Path to baseline JSON (default: {_DEFAULT_BASELINE})",
    )
    args = parser.parse_args()

    exit_code = verify_against_baseline(
        phase=args.phase,
        baseline_path=Path(args.baseline),
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    _main()
