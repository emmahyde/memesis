#!/usr/bin/env python3
"""
Feature flag ablation experiments — measures impact of each flag on eval metrics.

Runs the eval suite under different flag configurations to find which flags
improve or degrade retrieval quality. Each config writes a flags.json to the
eval's temp database directory, clears the flag cache, and re-runs.

Usage:
    python3 eval/experiment.py                     # Full ablation study
    python3 eval/experiment.py --quick             # Baseline + all-on only
    python3 eval/experiment.py --flag thompson_sampling  # Single flag ablation
    python3 eval/experiment.py --group retrieval   # Flag group ablation
    python3 eval/experiment.py --budget-sweep      # Test budget levels 2-16%
"""

import json
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import init_db, close_db
from core.models import Memory
from core.retrieval import RetrievalEngine
from eval.conftest import (
    EVAL_OBSERVATIONS_DB,
    seed_from_observations,
    seed_store,
)
from eval.longmemeval_adapter import LongMemEvalAdapter
from eval.report import make_retrieval_fn, score_fts, score_injection

# Re-seed random per config to ensure thompson_sampling shows stochastic effect
import random as _random

# ---------------------------------------------------------------------------
# Retrieval scenario queries — test flag impact on query-aware paths
# ---------------------------------------------------------------------------

RETRIEVAL_SCENARIOS = [
    # Each scenario: query + expected memory titles (substring match against new DB).
    {
        "id": "rs-01",
        "query": "autonomous agent delegation and review",
        "expected_titles": [
            "Delegates deep implementation review to autonomous agents",
            "multi-agent minion/orchestrator review pipeline",
        ],
        "category": "workflow",
    },
    {
        "id": "rs-02",
        "query": "worktree directory convention for ticket work",
        "expected_titles": [
            "Worktree-per-ticket directory convention",
        ],
        "category": "tooling",
    },
    {
        "id": "rs-03",
        "query": "terse communication style and scope cutting",
        "expected_titles": [
            "Terse, low-ceremony communication style",
            "Cuts scope mid-task with a single phrase",
        ],
        "category": "communication",
    },
    {
        "id": "rs-04",
        "query": "review findings verification and correctness",
        "expected_titles": [
            "Correctness over claimed fixes",
            "Previous review findings carry forward",
            "Iterative review findings are tracked",
        ],
        "category": "accuracy",
    },
    {
        "id": "rs-05",
        "query": "context recovery and artifact files",
        "expected_titles": [
            "Context recovery via artifact files",
            "Minion output artifact: plan.md",
        ],
        "category": "workflow",
    },
    {
        "id": "rs-06",
        "query": "interrupting agents and killing off-track work",
        "expected_titles": [
            "Interrupts mid-execution when direction is clear",
            "Kills agents and asks for synthesis",
            "Cuts scope fast when something feels wrong",
        ],
        "category": "collaboration",
    },
    {
        "id": "rs-07",
        "query": "file write race conditions and concurrency",
        "expected_titles": [
            "file-write race conditions in multi-agent",
        ],
        "category": "accuracy",
    },
    {
        "id": "rs-08",
        "query": "PR formatting and body conventions",
        "expected_titles": [
            "PR body must begin with",
        ],
        "category": "preference",
    },
    {
        "id": "rs-09",
        "query": "review agents finding implementation files",
        "expected_titles": [
            "Review agents must locate implementation files",
        ],
        "category": "workflow",
    },
    {
        "id": "rs-10",
        "query": "pushback on overclaiming and honest assessment",
        "expected_titles": [
            "Pushes back when overclaiming",
            "Correctness over optimism",
        ],
        "category": "collaboration",
    },
]

# ---------------------------------------------------------------------------
# Flag taxonomy — grouped by subsystem
# ---------------------------------------------------------------------------

FLAG_GROUPS = {
    "retrieval": [
        "prompt_aware_tier2",
        "thompson_sampling",
        "graph_expansion",
    ],
    "ranking": [
        "saturation_decay",
        "integration_factor",
        "sm2_spaced_injection",
        "provenance_signals",
    ],
    "observation": [
        "orienting_detector",
        "habituation_baseline",
        "somatic_markers",
        "replay_priority",
    ],
    "lifecycle": [
        "reconsolidation",
        "ghost_coherence",
        "affect_awareness",
    ],
}

ALL_FLAGS = [flag for group in FLAG_GROUPS.values() for flag in group]


# ---------------------------------------------------------------------------
# Experiment configs
# ---------------------------------------------------------------------------

def make_configs(mode: str = "full", flag: str = None, group: str = None) -> list[dict]:
    """Build experiment configurations.

    Each config is {"name": ..., "flags": {flag: bool, ...}, "description": ...}.
    """
    configs = []

    # Baseline: all off
    configs.append({
        "name": "baseline",
        "flags": {f: False for f in ALL_FLAGS},
        "description": "All features disabled (original behavior)",
    })

    # All on (current default)
    configs.append({
        "name": "all_on",
        "flags": {f: True for f in ALL_FLAGS},
        "description": "All features enabled (current default)",
    })

    if mode == "quick":
        return configs

    if mode == "single" and flag:
        # Single flag ablation: all on except this one
        flags = {f: True for f in ALL_FLAGS}
        flags[flag] = False
        configs.append({
            "name": f"-{flag}",
            "flags": flags,
            "description": f"All on except {flag}",
        })
        # Also test: all off except this one
        flags2 = {f: False for f in ALL_FLAGS}
        flags2[flag] = True
        configs.append({
            "name": f"+{flag}_only",
            "flags": flags2,
            "description": f"Only {flag} enabled",
        })
        return configs

    if mode == "group" and group:
        groups_to_test = [group] if group != "all" else list(FLAG_GROUPS.keys())
        for g in groups_to_test:
            # Group off: all on except this group
            flags = {f: True for f in ALL_FLAGS}
            for f in FLAG_GROUPS[g]:
                flags[f] = False
            configs.append({
                "name": f"-{g}",
                "flags": flags,
                "description": f"All on except {g} group ({', '.join(FLAG_GROUPS[g])})",
            })
            # Group only: all off except this group
            flags2 = {f: False for f in ALL_FLAGS}
            for f in FLAG_GROUPS[g]:
                flags2[f] = True
            configs.append({
                "name": f"+{g}_only",
                "flags": flags2,
                "description": f"Only {g} group enabled",
            })
        return configs

    if mode == "budget_sweep":
        # Test all-on at different budget levels
        all_on_flags = {f: True for f in ALL_FLAGS}
        configs = []  # Override baseline/all_on — budgets are the variable
        for pct in [0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.16]:
            configs.append({
                "name": f"budget_{pct:.0%}",
                "flags": all_on_flags,
                "token_budget_pct": pct,
                "description": f"All flags on, {pct:.0%} token budget",
            })
        return configs

    # Full ablation: baseline + all_on + each flag individually removed
    for f in ALL_FLAGS:
        flags = {fl: True for fl in ALL_FLAGS}
        flags[f] = False
        configs.append({
            "name": f"-{f}",
            "flags": flags,
            "description": f"All on except {f}",
        })

    return configs


# ---------------------------------------------------------------------------
# Single eval run under a flag config
# ---------------------------------------------------------------------------

def score_retrieval_scenarios(engine: RetrievalEngine, session_id: str) -> dict:
    """Score query-aware retrieval against known scenarios.

    Uses title-based precision: checks if specific expected memories
    (by title substring) appear in the retrieval results. This is
    precise enough that reranking flags can differentiate.
    """
    scenario_results = []

    for scenario in RETRIEVAL_SCENARIOS:
        query = scenario["query"]
        expected_titles = scenario["expected_titles"]

        # Test active_search (Tier 3 — hybrid RRF)
        try:
            active_results = engine.active_search(
                query=query, session_id=session_id, limit=10
            )
        except Exception:
            active_results = []

        # Test query-aware Tier 2
        try:
            tier2_results = engine.get_crystallized_for_context(query=query)
        except Exception:
            tier2_results = []

        # Score: which expected titles appear in results?
        active_result_titles = [r.get("title", "") for r in active_results]
        tier2_result_titles = [m.title or "" for m in tier2_results]

        def title_match(expected: str, result_titles: list[str]) -> bool:
            return any(expected.lower() in rt.lower() for rt in result_titles)

        active_hits = sum(1 for et in expected_titles if title_match(et, active_result_titles))
        tier2_hits = sum(1 for et in expected_titles if title_match(et, tier2_result_titles))
        n_expected = len(expected_titles)

        scenario_results.append({
            "id": scenario["id"],
            "category": scenario["category"],
            "active_count": len(active_results),
            "tier2_count": len(tier2_results),
            "active_precision": active_hits / max(n_expected, 1),
            "tier2_precision": tier2_hits / max(n_expected, 1),
            "active_hits": active_hits,
            "tier2_hits": tier2_hits,
            "expected": n_expected,
        })

    # Aggregate
    n = len(scenario_results)
    return {
        "active_recall": sum(s["active_precision"] for s in scenario_results) / max(n, 1),
        "tier2_recall": sum(s["tier2_precision"] for s in scenario_results) / max(n, 1),
        "active_hit_rate": sum(1 for s in scenario_results if s["active_hits"] > 0) / max(n, 1),
        "tier2_hit_rate": sum(1 for s in scenario_results if s["tier2_hits"] > 0) / max(n, 1),
        "avg_active_results": sum(s["active_count"] for s in scenario_results) / max(n, 1),
        "avg_tier2_results": sum(s["tier2_count"] for s in scenario_results) / max(n, 1),
        "per_scenario": scenario_results,
    }


def _seed_realistic_state(memories: list) -> None:
    """Seed injection history, usage patterns, and graph edges.

    Creates the state that flags like thompson_sampling, sm2, saturation_decay,
    integration_factor, and graph_expansion need to differentiate.
    """
    import random
    from datetime import timedelta
    from core.models import MemoryEdge, NarrativeThread, ThreadMember, RetrievalLog
    from core.graph import compute_edges

    random.seed(42)
    now = datetime.now()

    # Promote high-importance consolidated memories to crystallized so there's
    # enough selection pressure for retrieval flags to differentiate.
    # In production, crystallization happens via the lifecycle; in the eval,
    # we simulate a mature system with ~30 crystallized memories.
    consolidated_high = list(
        Memory.select()
        .where(Memory.stage == "consolidated", Memory.importance >= 0.75)
        .order_by(Memory.importance.desc())
        .limit(20)
    )
    if consolidated_high:
        promote_ids = [m.id for m in consolidated_high]
        Memory.update(stage="crystallized").where(Memory.id.in_(promote_ids)).execute()

    # Refresh after promotions
    memories = list(Memory.select())
    crystallized = [m for m in memories if m.stage == "crystallized"]
    consolidated = [m for m in memories if m.stage == "consolidated"]

    # 1. Seed injection history — some memories heavily used, some never
    for mem in crystallized + consolidated[:20]:
        injection_count = random.choice([0, 0, 1, 3, 5, 10, 20])
        usage_count = min(injection_count, random.randint(0, injection_count + 1))
        unused = injection_count - usage_count

        Memory.update(
            injection_count=injection_count,
            usage_count=usage_count,
            last_injected_at=(now - timedelta(days=random.randint(0, 30))).isoformat(),
        ).where(Memory.id == mem.id).execute()

        # Create RetrievalLog entries for provenance
        for j in range(min(injection_count, 3)):
            session_ts = (now - timedelta(days=random.randint(1, 60))).isoformat()
            RetrievalLog.create(
                timestamp=session_ts,
                session_id=f"sim-session-{random.randint(1000, 9999)}",
                memory_id=mem.id,
                retrieval_type="injected",
            )

    # 2. Seed SM-2 state — most memories eligible, a few on cooldown
    # Realistic: ~80% eligible (due in past or never scheduled), ~20% on cooldown
    for mem in crystallized:
        ease = random.uniform(1.3, 3.0)
        interval = random.uniform(0.5, 7.0)
        if random.random() < 0.2:
            # On cooldown — recently injected, not yet due
            due = (now + timedelta(days=random.uniform(1, 5))).isoformat()
        else:
            # Eligible — due in the past
            due = (now - timedelta(days=random.uniform(0, 10))).isoformat()
        Memory.update(
            injection_ease_factor=ease,
            injection_interval_days=interval,
            next_injection_due=due,
        ).where(Memory.id == mem.id).execute()

    # 3. Create narrative threads for graph edges
    if len(crystallized) >= 4:
        t1 = NarrativeThread.create(title="Workflow patterns")
        for i, mem in enumerate(crystallized[:3]):
            ThreadMember.create(thread_id=t1.id, memory_id=mem.id, position=i)

        t2 = NarrativeThread.create(title="Communication style")
        for i, mem in enumerate(crystallized[2:5]):
            ThreadMember.create(thread_id=t2.id, memory_id=mem.id, position=i)

    # 4. Compute graph edges from threads + tag co-occurrence
    compute_edges()


def run_eval_with_flags(
    flag_config: dict[str, bool],
    data_source: str = "synthetic",
    token_budget_pct: float = 0.08,
) -> dict:
    """Run eval metrics under a specific flag configuration.

    Args:
        flag_config: Dict of flag_name -> bool.
        data_source: "synthetic" or "live".
        token_budget_pct: Fraction of context window for Tier 2 (default 8%).

    Returns:
        Dict of metric_name -> value.
    """
    import core.flags

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "experiment"
        init_db(base_dir=str(base))

        try:
            # Write flags.json into the base dir
            flags_path = base / "flags.json"
            with open(flags_path, "w") as f:
                json.dump(flag_config, f)

            # Clear flag cache so next get_flag() reads our config
            core.flags.reload()

            # Seed data
            if data_source == "live" and EVAL_OBSERVATIONS_DB.exists():
                seed_from_observations()
            else:
                seed_store()

            # Seed realistic state so flags can differentiate
            all_memories = list(Memory.select())
            _seed_realistic_state(all_memories)

            memories = list(Memory.select())
            engine = RetrievalEngine(token_budget_pct=token_budget_pct)

            # --- Static injection scoring ---
            session_id = f"experiment_{data_source}"
            injection = score_injection(engine, session_id, memories)

            # --- FTS scoring ---
            if data_source == "synthetic":
                fts_queries = [
                    ("ruby style", {"Ruby String Style"}),
                    ("deploy window", {"Deploy Window"}),
                    ("secret vault", {"Secret Management"}),
                    ("API versioning", {"API Versioning Policy"}),
                    ("test coverage", {"Test Coverage Question"}),
                ]
            else:
                # Use top memories by reinforcement as self-retrieval queries
                top = sorted(memories, key=lambda m: m.reinforcement_count or 0, reverse=True)[:10]
                fts_queries = []
                for m in top:
                    words = (m.title or "").split()
                    if len(words) >= 2:
                        fts_queries.append((" ".join(words[:3]), {m.title}))

            fts = score_fts(fts_queries) if fts_queries else {
                "macro_precision": 0, "macro_recall": 0, "avg_hits": 0, "total_queries": 0,
            }

            # --- LongMemEval ---
            retrieval_fn = make_retrieval_fn(engine, f"lme_{data_source}")
            adapter = LongMemEvalAdapter(retrieval_fn=retrieval_fn)
            lme_results = adapter.run_fixture()
            lme_agg = adapter.aggregate(lme_results)

            # --- Query-aware retrieval scenarios ---
            scenarios = score_retrieval_scenarios(engine, session_id)

            # --- Stage distribution ---
            stages = {}
            for m in memories:
                stages[m.stage] = stages.get(m.stage, 0) + 1

            # --- Crystallized count (static path, no query) ---
            tier2_static = engine.get_crystallized_for_context(token_limit=engine.token_limit)
            tier2_count = len(tier2_static)
            tier2_chars = sum(len(m.content or "") for m in tier2_static)

            return {
                "memory_count": len(memories),
                "stages": stages,
                "injection_rate": injection["injection_rate"],
                "injected_count": injection["injected_count"],
                "context_length": injection["context_length_chars"],
                "fts_precision": fts.get("macro_precision", 0),
                "fts_recall": fts.get("macro_recall", 0),
                "fts_avg_hits": fts.get("avg_hits", 0),
                "lme_accuracy": lme_agg["accuracy"],
                "lme_by_category": lme_agg.get("by_category", {}),
                "tier2_count": tier2_count,
                "tier2_chars": tier2_chars,
                # Query-aware metrics (where flags actually matter)
                "active_recall": scenarios["active_recall"],
                "tier2_recall": scenarios["tier2_recall"],
                "active_hit_rate": scenarios["active_hit_rate"],
                "tier2_hit_rate": scenarios["tier2_hit_rate"],
                "avg_active_results": scenarios["avg_active_results"],
                "avg_tier2_results": scenarios["avg_tier2_results"],
                "scenario_detail": scenarios["per_scenario"],
            }

        finally:
            close_db()
            core.flags.reload()  # Reset cache


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    configs: list[dict],
    data_source: str = "synthetic",
    token_budget_pct: float = 0.08,
) -> dict:
    """Run all configs and return results.

    Returns:
        {"configs": [...], "results": {config_name: metrics}, "comparison": {...}}
    """
    results = {}

    for i, config in enumerate(configs):
        name = config["name"]
        budget = config.get("token_budget_pct", token_budget_pct)
        print(f"  [{i+1}/{len(configs)}] {name}...", end="", flush=True)
        try:
            metrics = run_eval_with_flags(config["flags"], data_source=data_source, token_budget_pct=budget)
            results[name] = metrics
            print(f" done (inject={metrics['injection_rate']:.0%}, "
                  f"lme={metrics['lme_accuracy']:.0%})")
        except Exception as e:
            print(f" ERROR: {e}")
            results[name] = {"error": str(e)}

    # Compute deltas from baseline and all_on
    comparison = compute_comparison(results)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_source": data_source,
        "configs": configs,
        "results": results,
        "comparison": comparison,
    }


def compute_comparison(results: dict) -> dict:
    """Compute metric deltas relative to baseline and all_on."""
    baseline = results.get("baseline", {})
    all_on = results.get("all_on", {})

    if "error" in baseline or "error" in all_on:
        return {"error": "baseline or all_on failed"}

    comparison_metrics = [
        "injection_rate", "fts_precision", "fts_recall",
        "lme_accuracy", "tier2_count", "tier2_chars", "context_length",
        "active_recall", "tier2_recall", "active_hit_rate", "tier2_hit_rate",
    ]

    deltas = {}
    for name, metrics in results.items():
        if "error" in metrics or name in ("baseline", "all_on"):
            continue
        delta = {}
        for m in comparison_metrics:
            val = metrics.get(m, 0)
            base_val = baseline.get(m, 0)
            all_val = all_on.get(m, 0)
            delta[m] = {
                "value": val,
                "vs_baseline": val - base_val if isinstance(val, (int, float)) else None,
                "vs_all_on": val - all_val if isinstance(val, (int, float)) else None,
            }
        deltas[name] = delta

    # Impact ranking: which flags matter most (weighted composite)
    impact = []
    for name, delta in deltas.items():
        lme_delta = delta.get("lme_accuracy", {}).get("vs_all_on", 0) or 0
        inject_delta = delta.get("injection_rate", {}).get("vs_all_on", 0) or 0
        active_delta = delta.get("active_recall", {}).get("vs_all_on", 0) or 0
        tier2_delta = delta.get("tier2_recall", {}).get("vs_all_on", 0) or 0
        # Weighted composite: query-aware metrics matter more
        combined = (active_delta * 0.35) + (tier2_delta * 0.35) + (lme_delta * 0.2) + (inject_delta * 0.1)
        impact.append({
            "config": name,
            "lme_impact": lme_delta,
            "inject_impact": inject_delta,
            "active_impact": active_delta,
            "tier2_impact": tier2_delta,
            "combined_impact": combined,
        })
    impact.sort(key=lambda x: x["combined_impact"])

    return {"deltas": deltas, "impact_ranking": impact}


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

REPORT_DIR = Path(__file__).parent / "reports"

KEY_METRICS = [
    ("active_recall", "Act-P", "{:.1%}"),       # Active search precision (Tier 3)
    ("tier2_recall", "T2-P", "{:.1%}"),          # Query-aware Tier 2 precision
    ("active_hit_rate", "Act-HR", "{:.1%}"),     # Fraction of scenarios with any active hit
    ("tier2_hit_rate", "T2-HR", "{:.1%}"),       # Fraction of scenarios with any T2 hit
    ("lme_accuracy", "LME", "{:.1%}"),           # LongMemEval accuracy
    ("avg_tier2_results", "T2-N", "{:.1f}"),     # Avg memories returned per query
]


def print_experiment(experiment: dict):
    """Pretty-print experiment results as a comparison table."""
    results = experiment["results"]
    comparison = experiment["comparison"]

    print("=" * 80)
    print("  MEMESIS FLAG ABLATION EXPERIMENT")
    print(f"  {experiment['timestamp']}")
    print(f"  Data source: {experiment['data_source']}")
    print("=" * 80)

    # Header
    name_width = max(len(n) for n in results) + 2
    header = f"  {'Config':<{name_width}}"
    for _, label, _ in KEY_METRICS:
        header += f" {label:>8}"
    print(f"\n{header}")
    print(f"  {'-' * (name_width + 8 * len(KEY_METRICS) + len(KEY_METRICS))}")

    # Rows
    for name in ["baseline", "all_on"] + [n for n in results if n not in ("baseline", "all_on")]:
        metrics = results.get(name, {})
        if "error" in metrics:
            print(f"  {name:<{name_width}} ERROR: {metrics['error']}")
            continue
        row = f"  {name:<{name_width}}"
        for key, _, fmt in KEY_METRICS:
            val = metrics.get(key, 0)
            try:
                row += f" {fmt.format(val):>8}"
            except (ValueError, TypeError):
                row += f" {'?':>8}"
        print(row)

    # Delta summary
    if "deltas" in comparison:
        print(f"\n  --- Deltas vs all_on (negative = removing flag hurt) ---")
        print(f"  {'Config':<{name_width}} {'Act-R':>8} {'T2-R':>8} {'LME':>8} {'Inj':>8} {'Score':>8}")
        print(f"  {'-' * (name_width + 43)}")

        for item in comparison.get("impact_ranking", []):
            name = item["config"]
            act = item["active_impact"]
            t2 = item["tier2_impact"]
            lme = item["lme_impact"]
            inj = item["inject_impact"]
            combo = item["combined_impact"]
            print(f"  {name:<{name_width}} {act:>+7.1%} {t2:>+7.1%} {lme:>+7.1%} {inj:>+7.1%} {combo:>+7.2f}")

    # Winners and losers
    if "impact_ranking" in comparison:
        ranking = comparison["impact_ranking"]
        print(f"\n  --- Impact Summary ---")

        # Flags that hurt when removed (positive impact = removing helped, negative = removing hurt)
        helpful = [r for r in ranking if r["combined_impact"] < -0.001]
        harmful = [r for r in ranking if r["combined_impact"] > 0.001]
        neutral = [r for r in ranking if abs(r["combined_impact"]) <= 0.001]

        if helpful:
            print(f"\n  HELPFUL (removing hurts quality):")
            for r in helpful:
                print(f"    {r['config']:25s}  combined: {r['combined_impact']:+.1%}")

        if harmful:
            print(f"\n  HARMFUL (removing improves quality):")
            for r in harmful:
                print(f"    {r['config']:25s}  combined: {r['combined_impact']:+.1%}")

        if neutral:
            print(f"\n  NEUTRAL (removing has no measurable effect):")
            for r in neutral:
                print(f"    {r['config']:25s}")

    print(f"\n{'=' * 80}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    mode = "full"
    flag = None
    group = None
    data_source = "synthetic"

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--quick":
            mode = "quick"
        elif args[i] == "--flag" and i + 1 < len(args):
            mode = "single"
            flag = args[i + 1]
            i += 1
        elif args[i] == "--group" and i + 1 < len(args):
            mode = "group"
            group = args[i + 1]
            i += 1
        elif args[i] == "--live":
            data_source = "live"
        elif args[i] == "--budget-sweep":
            mode = "budget_sweep"
        i += 1

    configs = make_configs(mode=mode, flag=flag, group=group)
    print(f"\n  Running {len(configs)} configurations ({data_source} data)...\n")

    experiment = run_experiment(configs, data_source=data_source)

    # Write JSON report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = REPORT_DIR / f"experiment-{ts}.json"
    with open(report_path, "w") as f:
        json.dump(experiment, f, indent=2, default=str)

    print_experiment(experiment)
    print(f"\n  Report: {report_path}")


if __name__ == "__main__":
    main()
