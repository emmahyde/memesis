#!/usr/bin/env python3
"""
Interactive observation eval — KEEP / PRUNE / REPHRASE labeling on raw
candidates from one Claude Code session, plus a confusion matrix against
the consolidator's own KEEP / MERGE / PRUNE decisions.

Pipeline:
    1. Locate the session JSONL under ~/.claude/projects/...
    2. Build a session summary via core.transcript.summarize()
    3. Call scripts.reduce.reduce_session() to get raw candidates
       (no DB writes — uses an in-memory observation store)
    4. Walk each candidate; collect user label
    5. Run scripts.consolidate's gate prompt on the same raw set
    6. Emit eval-labels.jsonl + confusion matrix

Usage:
    python3 scripts/eval_protocol.py --session 7b2cabf3
    python3 scripts/eval_protocol.py --session 7b2cabf3 --skip-consolidator
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.transcript import read_transcript, summarize  # noqa: E402
from scripts.reduce import (  # noqa: E402
    get_store_manifest,
    reduce_session,
)
from scripts.consolidate import consolidate  # noqa: E402

OUTPUT_DIR = Path(__file__).parent.parent / "backfill-output"
LABELS_PATH = OUTPUT_DIR / "eval-labels.jsonl"
PROJECTS_DIR = Path.home() / ".claude" / "projects"


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def find_session(prefix: str) -> Path:
    """Find a session JSONL whose stem starts with the given prefix."""
    matches: list[Path] = []
    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for jsonl in proj_dir.glob(f"{prefix}*.jsonl"):
            matches.append(jsonl)
    if not matches:
        raise FileNotFoundError(f"No session found with prefix {prefix!r}")
    if len(matches) > 1:
        joined = "\n  ".join(str(m) for m in matches)
        raise ValueError(f"Ambiguous session prefix {prefix!r}; matches:\n  {joined}")
    return matches[0]


# ---------------------------------------------------------------------------
# In-memory observation store (so reduce.py thinks it has a manifest)
# ---------------------------------------------------------------------------


def _empty_store() -> sqlite3.Connection:
    """Build an in-memory observation store mirroring reduce.py's schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            observation_type TEXT,
            tags TEXT DEFAULT '[]',
            count INTEGER DEFAULT 1,
            sources TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return conn


# ---------------------------------------------------------------------------
# Interactive labeling
# ---------------------------------------------------------------------------


def _read_choice(valid: str) -> str:
    """Block until the user types a single character from `valid`."""
    while True:
        ch = input("  > ").strip().lower()
        if ch and ch[0] in valid:
            return ch[0]
        print(f"    (enter one of: {' / '.join(valid)})")


def label_candidates(candidates: list[dict], session_id: str) -> list[dict]:
    """Walk through each candidate and collect a user label.

    Returns a list of label dicts ready to write as JSONL. Each entry has
    the original observation plus action ("keep" | "prune" | "rephrase" |
    "skip") and an optional rephrase payload.
    """
    labels: list[dict] = []
    n = len(candidates)
    print(f"\nLabeling {n} candidate observation(s) from session {session_id[:8]}.")
    print("Choices: (k)eep / (p)rune / (r)ephrase / (s)kip / (q)uit\n")

    for i, c in enumerate(candidates, start=1):
        print(f"[{i}/{n}] type={c.get('observation_type', '?')} "
              f"confidence={c.get('confidence', '?')}")
        print(f"  Title:    {c.get('title', '(untitled)')}")
        print(f"  Content:  {c.get('content', '')}")
        if c.get("tags"):
            print(f"  Tags:     {c['tags']}")
        if c.get("rationale"):
            print(f"  Why:      {c['rationale']}")

        action_ch = _read_choice("kprsq")
        action = {"k": "keep", "p": "prune", "r": "rephrase",
                  "s": "skip", "q": "quit"}[action_ch]

        if action == "quit":
            print("Aborting; partial labels will still be saved.")
            break

        entry = {
            "session_id": session_id,
            "labeled_at": datetime.now().isoformat(),
            "candidate": c,
            "action": action,
        }
        if action == "rephrase":
            new_title = input("  New title: ").strip() or c.get("title", "")
            new_content = input("  New content: ").strip() or c.get("content", "")
            entry["rephrase"] = {"title": new_title, "content": new_content}
        labels.append(entry)
        print()

    return labels


# ---------------------------------------------------------------------------
# Consolidator gate (re-run on same raw set, no DB write)
# ---------------------------------------------------------------------------


def consolidator_decisions(candidates: list[dict]) -> dict[str, str]:
    """Run the consolidation gate prompt on the raw candidate set.

    Returns a mapping {title -> "keep" | "merge" | "prune"} so we can compare
    against user labels by title.
    """
    if not candidates:
        return {}

    # Wrap each candidate as the consolidate() function expects: list of
    # dicts with title / content / observation_type / tags / count / sources.
    observations = []
    for idx, c in enumerate(candidates, start=1):
        observations.append({
            "id": idx,
            "title": c.get("title", f"obs-{idx}"),
            "content": c.get("content", ""),
            "observation_type": c.get("observation_type", ""),
            "tags": c.get("tags") or [],
            "count": 1,
            "sources": [],
        })
    result = consolidate(observations)
    if not result.get("ok"):
        print(f"  (consolidator gate failed: {result.get('error')})")
        return {}

    title_to_action: dict[str, str] = {}
    for d in result.get("decisions", []):
        title = (d.get("title") or "").strip()
        action = (d.get("action") or "").lower()
        if title and action:
            title_to_action[title] = action
    return title_to_action


# ---------------------------------------------------------------------------
# Confusion matrix
# ---------------------------------------------------------------------------


def confusion(labels: list[dict], gate: dict[str, str]) -> None:
    """Print a 2x2 confusion matrix comparing user labels to gate decisions.

    Rephrase counts as keep on the user side. Merge counts as keep on the
    gate side.
    """
    user_keeps = {
        (label["candidate"].get("title") or "").strip()
        for label in labels
        if label["action"] in ("keep", "rephrase")
    }
    user_prunes = {
        (label["candidate"].get("title") or "").strip()
        for label in labels
        if label["action"] == "prune"
    }
    gate_keeps = {t for t, a in gate.items() if a in ("keep", "merge")}
    gate_prunes = {t for t, a in gate.items() if a == "prune"}

    titles = user_keeps | user_prunes
    tp = len(titles & user_keeps & gate_keeps)
    fn = len(titles & user_keeps - gate_keeps)
    fp = len(titles & user_prunes & gate_keeps)
    tn = len(titles & user_prunes & gate_prunes)

    total = tp + fn + fp + tn
    agreement = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    print()
    print("=" * 60)
    print("  Confusion: user vs consolidator gate")
    print("=" * 60)
    print(f"                 gate KEEP   gate PRUNE")
    print(f"  user KEEP        {tp:4d}        {fn:4d}")
    print(f"  user PRUNE       {fp:4d}        {tn:4d}")
    print()
    print(f"  Agreement: {agreement:.0%}  Precision: {precision:.0%}  Recall: {recall:.0%}")
    print(f"  (gate kept {len(gate_keeps)}, pruned {len(gate_prunes)}; "
          f"user kept {len(user_keeps)}, pruned {len(user_prunes)})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True,
                    help="Session ID prefix, e.g. 7b2cabf3")
    ap.add_argument("--model", default="haiku",
                    help="Model for reduce step (haiku|sonnet)")
    ap.add_argument("--skip-consolidator", action="store_true",
                    help="Don't run the gate or print confusion matrix.")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] Locating session {args.session}...")
    session_path = find_session(args.session)
    print(f"      {session_path}")

    print(f"[2/5] Reading + summarizing transcript...")
    messages = read_transcript(session_path)
    summary = summarize(messages)
    if not summary:
        print("      (empty summary, aborting)")
        return 1
    print(f"      {len(messages)} messages -> {len(summary):,} chars")

    print(f"[3/5] Calling reduce_session() (no DB write)...")
    conn = _empty_store()
    manifest = get_store_manifest(conn)
    result = reduce_session(summary, manifest, model=args.model)
    if not result.get("ok"):
        print(f"      reduce failed: {result.get('error')}")
        return 1
    candidates = result.get("create", [])
    print(f"      {len(candidates)} candidate observation(s)")

    print(f"[4/5] Interactive labeling...")
    labels = label_candidates(candidates, session_path.stem)

    # Persist labels (append, not overwrite — keeps the eval dataset growing)
    with open(LABELS_PATH, "a", encoding="utf-8") as f:
        for entry in labels:
            f.write(json.dumps(entry) + "\n")
    print(f"\nWrote {len(labels)} label(s) to {LABELS_PATH}")

    if args.skip_consolidator:
        return 0

    print(f"[5/5] Running consolidator gate for comparison...")
    gate = consolidator_decisions(candidates)
    if gate:
        confusion(labels, gate)
    else:
        print("      (no gate decisions to compare)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
