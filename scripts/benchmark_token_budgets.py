#!/usr/bin/env python3
"""
Token budget benchmarking script for the memesis eval suite.

Runs all 5 evals at 4 budget levels (8%, 4%, 2%, 1%) and reports
a comparison table to find the "knee point" — the budget below which
quality drops sharply (>5% relative to the 8% baseline).

Usage:
    python3 scripts/benchmark_token_budgets.py

No external dependencies beyond the memesis project itself.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add project root to path so core.* and eval.* imports resolve
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.models import Memory
from core.retrieval import RetrievalEngine
from core.lifecycle import LifecycleManager
from core.consolidator import Consolidator

from eval.conftest import seed_store
from eval.needle_test import NEEDLES, _count_needles_in_context
from eval.continuity_test import (
    SESSION_A_DECISION,
    SESSION_A_REASONING,
    DECISION_TOKEN,
    REASONING_TOKEN,
)
from eval.curation_audit import (
    IMPORTANT_OBSERVATIONS,
    _build_ephemeral_content,
    _build_mock_llm_decisions,
)
from eval.spontaneous_recall import PREFERENCE_MEMORIES
from eval.staleness_test import SCENARIOS, _make_updated_memory


BUDGET_LEVELS = [0.08, 0.04, 0.02, 0.01]
BUDGET_LABELS = {0.08: "8%", 0.04: "4%", 0.02: "2%", 0.01: "1%"}

TARGETS = {
    "needle": 0.85,
    "continuity": 0.80,
    "curation": 0.80,
    "spontaneous_recall": 0.70,
    "staleness": 0.10,
}

LOWER_IS_BETTER = {"staleness"}
KNEE_DROP_THRESHOLD = 0.05


# ---------------------------------------------------------------------------
# Eval runners (one per eval file)
# ---------------------------------------------------------------------------

def _make_crystallized_memory(spec: dict) -> str:
    mem = Memory.create(
        stage="consolidated",
        title=spec["title"],
        summary=spec["summary"],
        content=spec["content"],
        importance=spec["importance"],
        tags=json.dumps(spec["tags"]),
    )
    mem.reinforcement_count = 3
    mem.save()
    lifecycle = LifecycleManager()
    lifecycle.promote(mem.id, rationale="Promoted by eval seeder: 3 reinforcements")
    return mem.id


def run_needle_eval(budget_pct: float) -> float:
    """Returns fraction of needles found (0-1)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_db(base_dir=tmpdir)
        try:
            seed_store()
            for needle in NEEDLES:
                Memory.create(
                    stage="crystallized",
                    title=needle["title"],
                    summary=needle["summary"],
                    content=needle["content"],
                    importance=needle["importance"],
                    tags=json.dumps(["needle", "eval"]),
                )
            engine = RetrievalEngine(token_budget_pct=budget_pct)
            context = engine.inject_for_session(session_id="needle_eval_session")
            found = _count_needles_in_context(context)
            return found / len(NEEDLES)
        finally:
            close_db()


def run_continuity_eval(budget_pct: float) -> float:
    """Returns fraction of cross-session memories surviving (0-1)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_db(base_dir=tmpdir)
        try:
            _make_crystallized_memory(SESSION_A_DECISION)
            _make_crystallized_memory(SESSION_A_REASONING)

            close_db()
            init_db(base_dir=tmpdir)

            engine = RetrievalEngine(token_budget_pct=budget_pct)
            context = engine.inject_for_session(session_id="session_b_continuity_eval")

            tokens = [DECISION_TOKEN, REASONING_TOKEN]
            found = sum(1 for t in tokens if t in context)
            return found / len(tokens)
        finally:
            close_db()


def run_curation_eval() -> float:
    """Returns curation precision (0-1). Not affected by token budget."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_db(base_dir=tmpdir)
        try:
            lifecycle = LifecycleManager()
            consolidator = Consolidator(lifecycle)

            ephemeral_file = Path(tmpdir) / "session_ephemeral.md"
            ephemeral_file.write_text(_build_ephemeral_content(), encoding="utf-8")

            mock_response = _build_mock_llm_decisions()
            with patch.object(
                consolidator,
                "_call_llm",
                return_value=consolidator._parse_decisions(mock_response),
            ):
                result = consolidator.consolidate_session(
                    ephemeral_path=str(ephemeral_file),
                    session_id="curation_eval_session",
                )

            kept_ids = result["kept"]
            if not kept_ids:
                return 0.0

            important_texts = {obs["text"] for obs in IMPORTANT_OBSERVATIONS}
            correctly_kept = 0
            for mid in kept_ids:
                mem = Memory.get_by_id(mid)
                content = mem.content or ""
                if any(text in content for text in important_texts):
                    correctly_kept += 1

            return correctly_kept / len(kept_ids)
        finally:
            close_db()


def run_spontaneous_recall_eval(budget_pct: float) -> float:
    """Returns fraction of preference tokens found (0-1)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_db(base_dir=tmpdir)
        try:
            for pref in PREFERENCE_MEMORIES:
                Memory.create(
                    stage="instinctive",
                    title=pref["title"],
                    summary=pref["summary"],
                    content=pref["content"],
                    importance=pref["importance"],
                    tags=json.dumps(pref["tags"]),
                )
            engine = RetrievalEngine(token_budget_pct=budget_pct)
            context = engine.inject_for_session(session_id="spontaneous_recall_session")
            found = sum(1 for pref in PREFERENCE_MEMORIES if pref["token"] in context)
            return found / len(PREFERENCE_MEMORIES)
        finally:
            close_db()


def run_staleness_eval(budget_pct: float) -> float:
    """Returns stale injection rate (0-1). Lower is better."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_db(base_dir=tmpdir)
        try:
            for scenario in SCENARIOS:
                _make_updated_memory(scenario)

            engine = RetrievalEngine(token_budget_pct=budget_pct)
            context = engine.inject_for_session(session_id="staleness_eval_session")

            stale_found = sum(1 for s in SCENARIOS if s["stale_token"] in context)
            return stale_found / len(SCENARIOS)
        finally:
            close_db()


EVAL_RUNNERS = {
    "needle": run_needle_eval,
    "continuity": run_continuity_eval,
    "curation": run_curation_eval,
    "spontaneous_recall": run_spontaneous_recall_eval,
    "staleness": run_staleness_eval,
}


def _run_all_evals(budget_pct: float) -> dict[str, float]:
    results: dict[str, float] = {}
    for name, runner in EVAL_RUNNERS.items():
        if name == "curation":
            results[name] = runner()
        else:
            results[name] = runner(budget_pct)
    return results


def _find_knee_point(eval_name: str, scores: dict[float, float]) -> str | None:
    baseline = scores[0.08]
    lower_better = eval_name in LOWER_IS_BETTER

    for budget in [0.04, 0.02, 0.01]:
        score = scores[budget]
        if lower_better:
            delta = score - baseline
        else:
            delta = baseline - score

        if delta > KNEE_DROP_THRESHOLD:
            return BUDGET_LABELS[budget]

    return None


def _format_score(eval_name: str, score: float, budget: float) -> str:
    target = TARGETS[eval_name]
    if eval_name in LOWER_IS_BETTER:
        meets = score < target
    else:
        meets = score >= target

    pct = score * 100
    marker = "✓" if meets else "✗"
    return f"{pct:5.1f}% {marker}"


def main() -> None:
    print("=" * 80)
    print("MEMESIS TOKEN BUDGET BENCHMARK")
    print("=" * 80)
    print()
    print("Running 5 evals at 4 budget levels (8%, 4%, 2%, 1%)...")
    print("This may take a moment...")
    print()

    # ------------------------------------------------------------------
    # Run evals
    # ------------------------------------------------------------------
    all_results: dict[float, dict[str, float]] = {}
    for budget in BUDGET_LEVELS:
        label = BUDGET_LABELS[budget]
        print(f"  → Budget {label} ...", end=" ", flush=True)
        all_results[budget] = _run_all_evals(budget)
        print("done")

    # ------------------------------------------------------------------
    # Print comparison table
    # ------------------------------------------------------------------
    print()
    print("-" * 80)
    print("COMPARISON TABLE")
    print("-" * 80)
    print()

    # Header
    header = f"{'Eval':<20}"
    for budget in BUDGET_LEVELS:
        header += f"{BUDGET_LABELS[budget]:>12}"
    header += f"{'Target':>12}"
    print(header)
    print("-" * 80)

    # Rows
    eval_display_names = {
        "needle": "Needle",
        "continuity": "Continuity",
        "curation": "Curation",
        "spontaneous_recall": "Spontaneous",
        "staleness": "Staleness",
    }

    for eval_name in EVAL_RUNNERS:
        row = f"{eval_display_names[eval_name]:<20}"
        for budget in BUDGET_LEVELS:
            score = all_results[budget][eval_name]
            row += f"{_format_score(eval_name, score, budget):>12}"
        target = TARGETS[eval_name]
        if eval_name in LOWER_IS_BETTER:
            row += f"{'<' + f'{target*100:.0f}%':>11}"
        else:
            row += f"{'≥' + f'{target*100:.0f}%':>11}"
        print(row)

    print("-" * 80)
    print("  ✓ = meets target    ✗ = below target")
    print()

    # ------------------------------------------------------------------
    # Knee-point analysis
    # ------------------------------------------------------------------
    print("-" * 80)
    print("KNEE-POINT ANALYSIS")
    print("-" * 80)
    print()
    print(
        f"Knee point = first budget level where quality drops > "
        f"{KNEE_DROP_THRESHOLD*100:.0f}% relative to the 8% baseline."
    )
    print()

    for eval_name in EVAL_RUNNERS:
        scores = {b: all_results[b][eval_name] for b in BUDGET_LEVELS}
        knee = _find_knee_point(eval_name, scores)
        baseline = scores[0.08]

        if knee:
            print(f"  {eval_display_names[eval_name]:<12}  KNEE at {knee}  "
                  f"(baseline {baseline*100:.1f}%)")
        else:
            print(f"  {eval_display_names[eval_name]:<12}  No knee detected  "
                  f"(baseline {baseline*100:.1f}%)")

    print()

    # ------------------------------------------------------------------
    # Overall recommendation
    # ------------------------------------------------------------------
    print("-" * 80)
    print("RECOMMENDATION")
    print("-" * 80)
    print()

    # Find the most conservative knee point across all evals
    all_knees: list[str] = []
    for eval_name in EVAL_RUNNERS:
        scores = {b: all_results[b][eval_name] for b in BUDGET_LEVELS}
        knee = _find_knee_point(eval_name, scores)
        if knee:
            all_knees.append(knee)

    if all_knees:
        # Map labels back to pct for ordering
        label_to_pct = {v: k for k, v in BUDGET_LABELS.items()}
        # Most conservative = highest pct that still shows a knee
        conservative_knee = max(all_knees, key=lambda lbl: label_to_pct[lbl])
        print(
            f"  Most conservative knee point: {conservative_knee}"
        )
        print(
            f"  → Do NOT reduce token budget below {conservative_knee} "
            f"without further testing."
        )
    else:
        print("  No knee point detected in the tested range.")
        print("  → Budgets as low as 1% may be viable; test <1% to find the true knee.")

    print()
    print("=" * 80)


if __name__ == "__main__":
    main()
