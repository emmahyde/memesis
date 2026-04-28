#!/usr/bin/env python3
"""
Consolidation gate — reviews the reduced observation store and produces
final keep/prune decisions with whole-session synthesis.

Reads from the SQLite observation store (reduce.py output), sends the
full observation set to the LLM for quality review, and writes final
decisions to consolidation-results.jsonl for seed.py.

The LLM sees ALL observations with their frequency counts and decides:
- Which are truly worth seeding (behavioral gate)
- Which can be merged (near-duplicates → single stronger observation)
- What the final clean set looks like

Usage:
    python3 scripts/consolidate.py                    # Consolidate reduced store
    python3 scripts/consolidate.py --dry-run           # Print prompt only
    python3 scripts/consolidate.py --focus "testing"   # Bias toward topic

Pipeline:
    scan.py → reduce.py → consolidate.py → seed.py
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

OUTPUT_DIR = Path(__file__).parent.parent / "backfill-output"
DB_PATH = OUTPUT_DIR / "observations.db"

CONSOLIDATION_GATE_PROMPT = """You are reviewing a set of observations extracted from conversation transcripts. These observations have already been deduplicated and counted — the frequency shows how many independent sessions produced each one.

OBSERVATION STORE ({total} observations, {total_sightings} total sightings):
{observations}

YOUR TASK: Apply the final quality gate. For each observation, decide:

- **KEEP**: Passes the behavioral gate AND frequency supports it. Output the final, clean version.
- **MERGE**: Two or more observations are about the same underlying pattern. Combine into one stronger observation.
- **PRUNE**: Fails the behavioral gate, is too generic, or is derivable from code/docs.

THE BEHAVIORAL GATE: "If I didn't have this, would I do something wrong next time?"

FREQUENCY SIGNAL:
- High frequency (3+) = strong independent signal. Still prune if it fails the gate, but lean toward keeping.
- Low frequency (1) = weaker signal. Apply the gate strictly.
- Frequency is evidence, not proof. A bad observation seen 10 times is still bad.

FREQUENCY FLOOR:
- Observations seen only once (freq=1) must pass a strict gate: "Would I actively do
  something wrong next session if I didn't have this?" If the answer is not a clear yes,
  PRUNE. Single-session observations are hypotheses, not patterns.
- Do not keep freq=1 observations for completeness, hedging, or "might be useful" reasons.

MERGE GUIDANCE:
- If multiple observations describe the same underlying pattern, MERGE them into one.
- The merged observation should be denser and more general than any source.
- Include the combined frequency count.

{focus_block}

Respond ONLY with valid JSON:
{{
  "decisions": [
    {{
      "action": "keep",
      "source_ids": [1],
      "title": "Clean title",
      "observation": "Final observation text — dense, behavioral, pattern-level",
      "observation_type": "correction|preference_signal|workflow_pattern|self_observation|decision_context|system_change",
      "tags": ["tag1"],
      "concept_tags": ["how-it-works|why-it-exists|what-changed|problem-solution|gotcha|pattern|trade-off"],
      "files_modified": ["relative/path.py"],
      "frequency": 3,
      "rationale": "Why this passes the gate"
    }},
    {{
      "action": "merge",
      "source_ids": [4, 7, 12],
      "title": "Merged pattern title",
      "observation": "Synthesized observation from the merged sources",
      "observation_type": "workflow_pattern",
      "tags": ["tag1"],
      "frequency": 8,
      "rationale": "These three observations describe the same pattern"
    }},
    {{
      "action": "prune",
      "source_ids": [2],
      "rationale": "Why this fails the gate"
    }}
  ]
}}"""


def _cluster_by_tfidf(observations: list, threshold: float = 0.70) -> dict:
    """
    Cluster observations by TF-IDF cosine similarity.

    Returns {obs_id: cluster_id}. Observations below threshold form singleton
    clusters. Returns {} if sklearn is unavailable or fewer than 2 observations.
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return {}

    if len(observations) < 2:
        return {}

    texts = [f"{o['title']} {o['content']}" for o in observations]
    vectorizer = TfidfVectorizer(min_df=1, stop_words='english')
    try:
        tfidf = vectorizer.fit_transform(texts)
    except ValueError:
        return {}

    sims = cosine_similarity(tfidf)

    # Simple union-find clustering
    parent = list(range(len(observations)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(observations)):
        for j in range(i + 1, len(observations)):
            if sims[i, j] >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    return {observations[i]['id']: find(i) for i in range(len(observations))}


def load_observations(conn: sqlite3.Connection) -> list[dict]:
    """Load all observations from the store."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, content, observation_type, tags, count, sources FROM observations ORDER BY count DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def format_observations(observations: list, clusters: dict = None) -> str:
    """Format observations for the LLM prompt.

    Args:
        observations: List of observation dicts from the store.
        clusters: Optional {obs_id: cluster_id} mapping from _cluster_by_tfidf.
            When provided, appends [cluster:N] hint to observations that share
            a cluster with at least one other observation.
    """
    lines = []
    for obs in observations:
        count_str = f" (x{obs['count']})" if obs['count'] > 1 else ""
        sources = json.loads(obs['sources'])
        type_str = f"[{obs['observation_type']}]" if obs['observation_type'] else ""
        cluster_hint = ""
        if clusters and obs['id'] in clusters:
            cid = clusters[obs['id']]
            cluster_hint = f" [cluster:{cid}]"
        lines.append(
            f"  #{obs['id']} {type_str} {obs['title']}{count_str} ({len(sources)} sessions){cluster_hint}\n"
            f"    {obs['content']}"
        )
    return "\n\n".join(lines)


def consolidate(observations: list, focus: str = None) -> dict:
    """Run all observations through the consolidation gate."""
    import anthropic

    total_sightings = sum(o["count"] for o in observations)
    clusters = _cluster_by_tfidf(observations)
    obs_text = format_observations(observations, clusters=clusters)

    focus_block = ""
    if focus:
        focus_block = f"FOCUS: Lean toward keeping observations related to: {focus}."

    prompt = CONSOLIDATION_GATE_PROMPT.format(
        total=len(observations),
        total_sightings=total_sightings,
        observations=obs_text,
        focus_block=focus_block,
    )

    if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
        client = anthropic.AnthropicBedrock()
        model = "us.anthropic.claude-sonnet-4-6"
    else:
        client = anthropic.Anthropic()
        model = "claude-sonnet-4-6"

    try:
        response = client.messages.create(
            model=model,
            max_tokens=16384,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return {"ok": True, **json.loads(text)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    if not DB_PATH.exists():
        print("No observations.db found. Run scripts/reduce.py first.", file=sys.stderr)
        sys.exit(1)

    focus, dry_run = None, False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--focus" and i + 1 < len(args):
            focus = args[i + 1]; i += 2
        elif args[i] == "--dry-run":
            dry_run = True; i += 1
        else:
            print(f"Unknown: {args[i]}", file=sys.stderr); sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    observations = load_observations(conn)
    conn.close()

    if not observations:
        print("No observations in store. Run scripts/reduce.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Consolidating {len(observations)} observations...", file=sys.stderr)

    if dry_run:
        total_sightings = sum(o["count"] for o in observations)
        prompt = CONSOLIDATION_GATE_PROMPT.format(
            total=len(observations),
            total_sightings=total_sightings,
            observations=format_observations(observations[:10]) + "\n  ... (truncated)",
            focus_block=f"FOCUS: {focus}" if focus else "",
        )
        print(prompt[:3000])
        return

    # Batch if too many observations (LLM struggles with 100+ in one call)
    BATCH_SIZE = 50
    all_decisions = []

    if len(observations) <= BATCH_SIZE:
        batches = [observations]
    else:
        batches = [observations[i:i+BATCH_SIZE] for i in range(0, len(observations), BATCH_SIZE)]
        print(f"  Processing in {len(batches)} batches of ~{BATCH_SIZE}...", file=sys.stderr)

    for bi, batch in enumerate(batches):
        if len(batches) > 1:
            print(f"  Batch {bi+1}/{len(batches)} ({len(batch)} observations)...", file=sys.stderr)

        result = consolidate(batch, focus=focus)

        if not result.get("ok"):
            print(f"  Batch {bi+1} ERROR: {result.get('error', '?')}", file=sys.stderr)
            continue

        all_decisions.extend(result.get("decisions", []))
        import time
        time.sleep(1)

    decisions = all_decisions
    kept = [d for d in decisions if d.get("action") in ("keep", "merge")]
    pruned = [d for d in decisions if d.get("action") == "prune"]
    merged = [d for d in decisions if d.get("action") == "merge"]

    print(f"\nResults:", file=sys.stderr)
    print(f"  Kept:   {len(kept)} ({len(merged)} merged)", file=sys.stderr)
    print(f"  Pruned: {len(pruned)}", file=sys.stderr)

    # Write results in the format seed.py expects
    output_path = OUTPUT_DIR / "consolidation-results.jsonl"
    with open(output_path, "w") as f:
        # Write as a single result block
        out = {
            "ok": True,
            "decisions": [],
            "session_id": "consolidation-gate",
            "project": "backfill",
        }
        for d in kept:
            out["decisions"].append({
                "action": "keep",
                "title": d.get("title", "?"),
                "observation": d.get("observation", ""),
                "observation_type": d.get("observation_type", ""),
                "tags": d.get("tags", []),
                "rationale": d.get("rationale", ""),
                "summary": d.get("observation", "")[:150],
                "frequency": d.get("frequency", 1),
                "source_ids": d.get("source_ids", []),
            })
        for d in pruned:
            out["decisions"].append({
                "action": "prune",
                "rationale": d.get("rationale", ""),
                "source_ids": d.get("source_ids", []),
            })
        f.write(json.dumps(out) + "\n")

    # Write reinforcements sidecar for seed.py
    reinforcement_path = OUTPUT_DIR / "reinforcements.json"
    reinforcements = {}
    for d in kept:
        freq = d.get("frequency", 1)
        if freq > 1:
            reinforcements[d.get("title", "?")] = freq - 1
    reinforcement_path.write_text(json.dumps(reinforcements, indent=2))

    print(f"\n  Results → {output_path}", file=sys.stderr)

    # Print kept observations
    print(f"\n  Observations to seed:", file=sys.stderr)
    for d in kept:
        freq = d.get("frequency", 1)
        freq_str = f" (x{freq})" if freq > 1 else ""
        t = d.get("observation_type", "?")[:12]
        print(f"    [{t:12s}] {d.get('title', '?')}{freq_str}", file=sys.stderr)


if __name__ == "__main__":
    main()
