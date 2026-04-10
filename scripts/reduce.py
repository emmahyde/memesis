#!/usr/bin/env python3
"""
Streaming observation accumulator — reduces session transcripts into a
deduplicated observation store with frequency counts.

Replaces the old consolidate.py approach (per-session batch → flat JSONL)
with a stateful reduce: the LLM sees each session alongside the current
observation store, and either creates new observations or reinforces
existing ones. Frequency is first-class signal.

Uses SQLite as the accumulator — persistent, queryable, natural dedup.

Usage:
    python3 scripts/reduce.py                         # Reduce all summaries
    python3 scripts/reduce.py --limit 10              # First 10 sessions
    python3 scripts/reduce.py --focus "testing"        # Bias toward topic
    python3 scripts/reduce.py --dry-run                # Print prompt, don't call LLM
    python3 scripts/reduce.py --report                 # Print current store state
    python3 scripts/reduce.py --db eval/eval.db --reset  # Isolated eval dataset
    python3 scripts/reduce.py --sample 10 --seed 42      # Deterministic 10% sample

Pipeline:
    scan.py → reduce.py → seed.py
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

OUTPUT_DIR = Path(__file__).parent.parent / "backfill-output"
DB_PATH = OUTPUT_DIR / "observations.db"

# ---------------------------------------------------------------------------
# Observation store (SQLite)
# ---------------------------------------------------------------------------


def init_db(reset: bool = False):
    """Initialize or reset the observation store."""
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            observation_type TEXT,
            tags TEXT DEFAULT '[]',
            count INTEGER DEFAULT 1,
            sources TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_sessions (
            session_id TEXT PRIMARY KEY,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    # Migration: add manifest tracking columns
    try:
        conn.execute("ALTER TABLE processed_sessions ADD COLUMN manifest_hash TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE processed_sessions ADD COLUMN obs_count_at_time INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    _migrate_sources(conn)
    return conn


def _migrate_sources(conn: sqlite3.Connection):
    """Migrate flat string sources to structured format: ["sid"] -> [{"session": "sid"}]."""
    rows = conn.execute("SELECT id, sources FROM observations").fetchall()
    migrated = 0
    for oid, sources_json in rows:
        sources = json.loads(sources_json)
        if not sources or isinstance(sources[0], dict):
            continue
        # Flat strings — migrate
        new_sources = [{"session": s} for s in sources]
        conn.execute(
            "UPDATE observations SET sources = ? WHERE id = ?",
            (json.dumps(new_sources), oid),
        )
        migrated += 1
    if migrated:
        conn.commit()
        print(f"  [migrate] converted {migrated} observations to structured sources", file=sys.stderr)


def get_store_manifest(conn: sqlite3.Connection, limit: int = 50) -> str:
    """Format the current store as a manifest for the LLM prompt."""
    rows = conn.execute(
        "SELECT id, title, observation_type, count FROM observations ORDER BY count DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return "(empty — no observations yet)"
    lines = []
    for row in rows:
        oid, title, otype, count = row
        count_str = f" (x{count})" if count > 1 else ""
        type_str = f" [{otype}]" if otype else ""
        lines.append(f"  #{oid}{type_str} {title}{count_str}")
    return "\n".join(lines)


def _find_near_duplicates(
    conn: sqlite3.Connection,
    new_content: str,
    threshold: float = 0.85,
) -> list:
    """
    Return observation IDs with cosine similarity >= threshold to new_content.

    Uses TF-IDF on title + content. Returns [] if sklearn unavailable or
    the store has < 2 observations (degenerate case for TF-IDF).
    """
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return []

    rows = conn.execute("SELECT id, title, content FROM observations").fetchall()
    if len(rows) < 2:
        return []

    existing_texts = [f"{r[1]} {r[2]}" for r in rows]
    all_texts = existing_texts + [new_content]

    vectorizer = TfidfVectorizer(min_df=1, stop_words='english')
    try:
        tfidf = vectorizer.fit_transform(all_texts)
    except ValueError:
        return []  # Empty vocabulary (e.g., all stop words)

    new_vec = tfidf[-1]
    existing_vecs = tfidf[:-1]
    sims = cosine_similarity(new_vec, existing_vecs).flatten()

    return [rows[i][0] for i, sim in enumerate(sims) if sim >= threshold]


def _source_entry(
    session_id: str,
    source_lines: list | None = None,
    action: str = "reinforce",
    rationale: str | None = None,
    confidence: str | None = None,
) -> dict:
    """Build a structured source entry with full provenance."""
    from datetime import datetime
    entry = {"session": session_id, "action": action, "at": datetime.now().isoformat()}
    if source_lines:
        entry["lines"] = source_lines
    if rationale:
        entry["rationale"] = rationale
    if confidence:
        entry["confidence"] = confidence
    return entry


def _append_source(
    sources: list,
    session_id: str,
    source_lines: list | None = None,
    action: str = "reinforce",
    rationale: str | None = None,
    confidence: str | None = None,
) -> list:
    """Append a source entry, deduplicating by session ID."""
    existing_sessions = {s["session"] for s in sources if isinstance(s, dict)}
    if session_id not in existing_sessions:
        sources.append(_source_entry(session_id, source_lines, action, rationale, confidence))
    return sources


def apply_operations(conn: sqlite3.Connection, result: dict, session_id: str, manifest: str = ""):
    """Apply CREATE and REINFORCE operations from LLM response."""
    creates = result.get("create", [])
    reinforcements = result.get("reinforce", [])

    for obs in creates:
        source_lines = obs.get("source_lines")
        rationale = obs.get("rationale")
        confidence = obs.get("confidence")
        text = f"{obs.get('title', '')} {obs.get('content', '')}"
        dupes = _find_near_duplicates(conn, text)
        if dupes:
            print(
                f"  [tfidf] near-duplicate detected, reinforcing #{dupes[0]} instead",
                file=sys.stderr,
            )
            oid = dupes[0]
            row = conn.execute("SELECT sources FROM observations WHERE id = ?", (oid,)).fetchone()
            if row:
                sources = _append_source(
                    json.loads(row[0]), session_id, source_lines,
                    action="create-deduped", rationale=rationale, confidence=confidence,
                )
                conn.execute(
                    "UPDATE observations SET count = count + 1, sources = ? WHERE id = ?",
                    (json.dumps(sources), oid),
                )
            continue

        conn.execute(
            "INSERT INTO observations (title, content, observation_type, tags, sources) VALUES (?, ?, ?, ?, ?)",
            (
                obs.get("title", "Untitled"),
                obs.get("content", ""),
                obs.get("observation_type", ""),
                json.dumps(obs.get("tags", [])),
                json.dumps([_source_entry(
                    session_id, source_lines,
                    action="create", rationale=rationale, confidence=confidence,
                )]),
            ),
        )

    for ref in reinforcements:
        if isinstance(ref, dict):
            oid = ref.get("id")
            source_lines = ref.get("source_lines")
            rationale = ref.get("rationale")
            confidence = ref.get("confidence")
        elif isinstance(ref, int):
            oid = ref
            source_lines = rationale = confidence = None
        else:
            continue
        if oid is None:
            continue
        row = conn.execute("SELECT sources FROM observations WHERE id = ?", (oid,)).fetchone()
        if row:
            sources = _append_source(
                json.loads(row[0]), session_id, source_lines,
                action="reinforce", rationale=rationale, confidence=confidence,
            )
            conn.execute(
                "UPDATE observations SET count = count + 1, sources = ? WHERE id = ?",
                (json.dumps(sources), oid),
            )

    conn.commit()
    import hashlib
    manifest_hash = hashlib.md5(manifest.encode()).hexdigest()[:12] if manifest else None
    obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO processed_sessions (session_id, manifest_hash, obs_count_at_time) VALUES (?, ?, ?)",
        (session_id, manifest_hash, obs_count),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# LLM reduce prompt
# ---------------------------------------------------------------------------

REDUCE_PROMPT = """You are building an observation store from conversation transcripts. Your goal is to build a rich, textured understanding of the person you collaborate with — not just what they do technically, but who they are, how they think, what they value, and how to be a great collaborator for them specifically.

You see one session at a time, alongside what you've already captured.

CURRENT OBSERVATION STORE:
{store_manifest}

SESSION CONTENT:
{session_content}

YOUR TASK: Extract durable observations from this session. For each insight:
- If it's NEW (not already in the store): CREATE it
- If it REINFORCES an existing observation: REINFORCE it by ID
- If it's noise, ephemeral, or re-derivable from code: SKIP it

TWO GATES — an observation passes if it clears EITHER one:

1. THE BEHAVIORAL GATE: "If I didn't have this, would I do something wrong next time?"
   Good for: technical corrections, workflow patterns, domain knowledge.

2. THE COLLABORATOR GATE: "Does this help me understand who this person is and how to work with them?"
   Good for: personality, values, aesthetic sense, communication style, trust dynamics, decision-making patterns.

WHAT TO EXTRACT:

Technical signal:
- Corrections (mistakes and the pattern that caused them)
- Workflow patterns (how this person works in non-obvious ways)
- Decision context (constraints and trade-offs behind choices)

Human signal — EQUALLY IMPORTANT, don't deprioritize these:
- Personality and values (what they care about, how they express opinions, what energizes vs drains them)
- Aesthetic preferences (visual taste, quality standards, design sensibility — "I like the angularness" is gold)
- Communication style (direct? diplomatic? when do they push back vs defer? what tone do they use when frustrated vs excited?)
- Trust and delegation patterns (when do they hand off control? when do they micromanage? what earns their trust?)
- Decision-making style (intuition vs analysis? speed vs thoroughness? when do they want options vs just a decision?)
- Collaboration dynamics (how do they give feedback? what does "good work" look like from them? how do they course-correct?)
- Self-observations (your own tendencies and failure modes working with THIS person)

WHAT TO SKIP:
- Facts derivable from code, git, or docs
- Tool output, file paths, one-time task mechanics
- Generic engineering truths that apply to any engineer
- Anything already captured (REINFORCE instead of duplicating)

IMPORTANT: Don't strip the humanity out of observations. "User prefers rebase" is worse than "Emma rebases even when it's painful — she values linear history enough to eat the conflict resolution cost, including generated lockfile churn." The texture matters.

LINE REFERENCES: Each message is tagged with its transcript line (e.g., [USER:L7]). Cite the 1-3 lines that MOST directly evidence each observation in "source_lines". Pick the single strongest moment — the line where the person *said* or *did* the thing, not every line that's tangentially related. If one line is the smoking gun, cite just that one.

DISPATCH STUBS: If a session consists primarily of agent dispatch instructions (e.g., "Read the file ... instructions.md ... and follow all the instructions"), these are routing messages with no behavioral signal. Emit no operations for them.

{focus_block}

PROVENANCE: For every operation, include:
- "source_lines": 1-3 transcript line numbers — the strongest evidence only, not an exhaustive list
- "rationale": one sentence — why this observation matters or why this session reinforces it
- "confidence": "high" (unambiguous signal), "medium" (reasonable inference), or "low" (borderline, could be noise)

Respond ONLY with valid JSON:
{{
  "create": [
    {{
      "title": "Short pattern-level title",
      "content": "The observation — dense, textured, capturing the person not just the pattern. 1-3 sentences.",
      "observation_type": "correction|preference_signal|workflow_pattern|self_observation|decision_context|personality|aesthetic|collaboration_dynamic",
      "tags": ["tag1", "tag2"],
      "source_lines": [7, 42],
      "rationale": "Why this matters — what would go wrong without it",
      "confidence": "high|medium|low"
    }}
  ],
  "reinforce": [
    {{"id": 7, "source_lines": [15], "rationale": "Session confirms same pattern in new context", "confidence": "high"}}
  ]
}}

Empty arrays are fine. Most sessions should mostly reinforce, not create."""


def reduce_session(session_summary: str, store_manifest: str, focus: str = None, debug: bool = False, model: str = "haiku") -> dict:
    """Process one session through the reduce prompt."""
    import asyncio
    from claude_agent_sdk import query, ClaudeAgentOptions

    t0 = time.time()

    focus_block = ""
    if focus:
        focus_block = (
            f"FOCUS: Pay special attention to observations related to: {focus}. "
            f"This doesn't override the behavioral gate but biases borderline decisions."
        )

    prompt = REDUCE_PROMPT.format(
        store_manifest=store_manifest,
        session_content=session_summary,
        focus_block=focus_block,
    )

    t_prompt = time.time()

    try:
        async def _run():
            result_text = ""
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    model=model,
                    max_turns=1,
                    allowed_tools=[],
                ),
            ):
                if hasattr(message, "result") and message.result:
                    result_text = message.result
            return result_text

        text = asyncio.run(_run()).strip()
        t_llm = time.time()

        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        # Haiku sometimes emits trailing text after the JSON — find the closing brace
        brace_depth = 0
        json_end = None
        for ci, ch in enumerate(text):
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    json_end = ci + 1
                    break
        if json_end and json_end < len(text):
            text = text[:json_end]
        parsed = json.loads(text)

        t_parse = time.time()

        result = {"ok": True, **parsed}
        if debug:
            result["_bench"] = {
                "prompt_chars": len(prompt),
                "summary_chars": len(session_summary),
                "manifest_chars": len(store_manifest),
                "prompt_build_s": round(t_prompt - t0, 3),
                "llm_call_s": round(t_llm - t_prompt, 3),
                "parse_s": round(t_parse - t_llm, 3),
                "total_s": round(t_parse - t0, 3),
            }
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "_raw": text[:500] if 'text' in dir() else "(no response)"}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def print_report(conn: sqlite3.Connection):
    """Print the current observation store."""
    rows = conn.execute(
        "SELECT id, title, observation_type, count, sources FROM observations ORDER BY count DESC"
    ).fetchall()

    total = len(rows)
    total_count = sum(r[3] for r in rows)
    types = {}
    for r in rows:
        t = r[2] or "(untyped)"
        types[t] = types.get(t, 0) + 1

    print(f"{'=' * 60}")
    print(f"  OBSERVATION STORE")
    print(f"{'=' * 60}")
    print(f"  Unique observations: {total}")
    print(f"  Total sightings: {total_count}")
    print(f"  Avg frequency: {total_count / max(total, 1):.1f}")

    print(f"\n  Types:")
    for t, c in sorted(types.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * c
        print(f"    {t:25s} {c:3d}  {bar}")

    print(f"\n  Top observations (by frequency):")
    for r in rows[:20]:
        oid, title, otype, count, sources = r
        sources_list = json.loads(sources)
        sessions = len(sources_list)
        # Count how many source entries have line references
        with_lines = sum(1 for s in sources_list if isinstance(s, dict) and s.get("lines"))
        lines_str = f", {with_lines} with line refs" if with_lines else ""
        type_str = f"[{otype[:12]}]" if otype else "[?]"
        print(f"    #{oid:3d} x{count:2d} ({sessions} sessions{lines_str}) {type_str:14s} {title}")

    if len(rows) > 20:
        print(f"\n  ... and {len(rows) - 20} more")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():

    limit, focus, dry_run, report_only, reset, project = None, None, False, False, False, None
    db_path, summaries_dir = None, None
    sample_pct, sample_seed = None, 42
    debug, target_obs = False, None
    model, session_id_filter = "haiku", None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--focus" and i + 1 < len(args):
            focus = args[i + 1]; i += 2
        elif args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]; i += 2
        elif args[i] == "--db" and i + 1 < len(args):
            db_path = Path(args[i + 1]); i += 2
        elif args[i] == "--summaries-dir" and i + 1 < len(args):
            summaries_dir = Path(args[i + 1]); i += 2
        elif args[i] == "--sample" and i + 1 < len(args):
            sample_pct = float(args[i + 1]); i += 2
        elif args[i] == "--seed" and i + 1 < len(args):
            sample_seed = int(args[i + 1]); i += 2
        elif args[i] == "--dry-run":
            dry_run = True; i += 1
        elif args[i] == "--report":
            report_only = True; i += 1
        elif args[i] == "--reset":
            reset = True; i += 1
        elif args[i] == "--debug":
            debug = True; i += 1
        elif args[i] == "--target" and i + 1 < len(args):
            target_obs = int(args[i + 1]); i += 2
        elif args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]; i += 2
        elif args[i] == "--session" and i + 1 < len(args):
            session_id_filter = args[i + 1]; i += 2
        else:
            print(f"Unknown: {args[i]}", file=sys.stderr); sys.exit(1)

    # Override globals if custom paths provided
    if db_path:
        global DB_PATH
        DB_PATH = db_path
    source_dir = summaries_dir or OUTPUT_DIR
    summary_files = sorted(source_dir.glob("summaries-*.jsonl"))

    if not summary_files:
        print(f"No summaries-*.jsonl found in {source_dir}. Run scripts/scan.py first.", file=sys.stderr)
        sys.exit(1)

    if project:
        summary_files = [f for f in summary_files if project in f.stem]

    conn = init_db(reset=reset)

    if report_only:
        print_report(conn)
        conn.close()
        return

    summaries = []
    for sf in summary_files:
        with open(sf) as f:
            for line in f:
                summaries.append(json.loads(line))

    if sample_pct is not None:
        import random
        rng = random.Random(sample_seed)
        total = len(summaries)
        n = max(1, int(total * sample_pct / 100))
        summaries = rng.sample(summaries, n)
        print(f"Sampled {n}/{total} sessions ({sample_pct}%, seed={sample_seed})", file=sys.stderr)
    else:
        # Default: shuffle for random selection
        import random
        rng = random.Random(sample_seed)
        rng.shuffle(summaries)

    if limit:
        summaries = summaries[:limit]

    # Skip already-processed sessions
    try:
        processed = set(
            row[0] for row in conn.execute("SELECT session_id FROM processed_sessions").fetchall()
        )
    except sqlite3.OperationalError:
        processed = set()
        for r in conn.execute("SELECT sources FROM observations").fetchall():
            for s in json.loads(r[0]):
                processed.add(s["session"])

    if session_id_filter:
        summaries = [s for s in summaries if session_id_filter in s["session_id"]]
        if not summaries:
            print(f"No session matching '{session_id_filter}' found.", file=sys.stderr)
            conn.close()
            return

    remaining = [s for s in summaries if s["session_id"] not in processed]
    if not remaining:
        print("All sessions already processed.", file=sys.stderr)
        print_report(conn)
        conn.close()
        return

    current_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    target_str = f", target={target_obs} obs" if target_obs else ""
    print(f"Reducing {len(remaining)} sessions ({len(processed)} already processed, {current_obs} obs{target_str})...", file=sys.stderr)
    if focus:
        print(f"Focus: {focus}", file=sys.stderr)

    if dry_run:
        manifest = get_store_manifest(conn)
        prompt = REDUCE_PROMPT.format(
            store_manifest=manifest,
            session_content=remaining[0]["summary"][:500] + "...",
            focus_block=f"FOCUS: {focus}" if focus else "",
        )
        print(prompt[:3000])
        conn.close()
        return

    total_time = 0
    for i, sess in enumerate(remaining):
        # Check target
        if target_obs:
            current_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            if current_obs >= target_obs:
                print(f"\n  Target reached: {current_obs} observations.", file=sys.stderr)
                break

        summary_chars = len(sess["summary"])
        print(f"  [{i+1}/{len(remaining)}] {sess['session_id'][:8]} ({summary_chars:,}ch)... ",
              end="", file=sys.stderr, flush=True)

        t_start = time.time()
        manifest = get_store_manifest(conn)
        t_manifest = time.time()

        result = reduce_session(sess["summary"], manifest, focus=focus, debug=debug, model=model)
        t_end = time.time()
        elapsed = t_end - t_start
        total_time += elapsed

        if result.get("ok"):
            created = len(result.get("create", []))
            reinforced = len(result.get("reinforce", []))
            apply_operations(conn, result, sess["session_id"], manifest=manifest)

            t_apply = time.time()
            obs_now = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

            parts = []
            if created: parts.append(f"+{created} new")
            if reinforced: parts.append(f"↑{reinforced} reinforced")
            parts.append(f"[{obs_now} total]")
            parts.append(f"{elapsed:.1f}s")

            if debug and "_bench" in result:
                b = result["_bench"]
                parts.append(f"(llm={b['llm_call_s']:.1f}s, in={b['input_tokens']}tok, out={b['output_tokens']}tok, apply={t_apply - t_end:.2f}s)")

            print(" ".join(parts), file=sys.stderr)
        else:
            print(f"ERROR: {result.get('error', '?')[:50]} ({elapsed:.1f}s)", file=sys.stderr)

        time.sleep(0.5)

    sessions_done = min(i + 1, len(remaining))
    avg = total_time / max(sessions_done, 1)
    print(f"\nDone. {sessions_done} sessions in {total_time:.0f}s (avg {avg:.1f}s/session).", file=sys.stderr)
    print_report(conn)
    conn.close()


if __name__ == "__main__":
    main()
