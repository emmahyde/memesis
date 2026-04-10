#!/usr/bin/env python3
"""
Compare reduce output for a single session across models.

Usage:
    python3 scripts/compare.py --session abc123              # opus vs haiku (default)
    python3 scripts/compare.py --session abc123 --models haiku sonnet opus
    python3 scripts/compare.py --list                        # list available sessions
    python3 scripts/compare.py --session abc123 --empty-store # ignore existing observations
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.reduce import (
    OUTPUT_DIR,
    get_store_manifest,
    init_db,
    reduce_session,
)

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


def c(text, *styles):
    codes = "".join(COLORS.get(s, "") for s in styles)
    return f"{codes}{text}{COLORS['reset']}"


def load_sessions(source_dir: Path) -> list[dict]:
    sessions = []
    for sf in sorted(source_dir.glob("summaries-*.jsonl")):
        with open(sf) as f:
            for line in f:
                sessions.append(json.loads(line))
    return sessions


def find_session(sessions: list[dict], query: str) -> dict | None:
    for s in sessions:
        if query in s["session_id"]:
            return s
    return None


def print_result(model: str, result: dict, elapsed: float):
    creates = result.get("create", [])
    reinforces = result.get("reinforce", [])

    print(c(f"\n{'━' * 70}", "dim"))
    print(c(f"  {model.upper()}", "bold", "cyan"), c(f"({elapsed:.1f}s)", "dim"))
    print(c(f"{'━' * 70}", "dim"))

    if not result.get("ok"):
        print(c(f"  ERROR: {result.get('error', '?')}", "yellow"))
        if result.get("_raw"):
            print(c(f"  RAW: {result['_raw'][:200]}", "dim"))
        return

    if creates:
        print(c(f"\n  CREATE ({len(creates)})", "bold", "green"))
        for obs in creates:
            conf = obs.get("confidence", "?")
            conf_color = {"high": "green", "medium": "yellow", "low": "magenta"}.get(conf, "dim")
            lines = obs.get("source_lines", [])
            lines_str = f" L{','.join(str(l) for l in lines)}" if lines else ""

            print(f"  {c('●', 'green')} {c(obs.get('title', '?'), 'bold')}")
            print(f"    {obs.get('content', '')}")
            print(
                f"    {c(obs.get('observation_type', '?'), 'dim')}"
                f"  {c(conf, conf_color)}"
                f"  {c(lines_str, 'dim')}"
            )
            if obs.get("rationale"):
                print(f"    {c('why:', 'dim')} {obs['rationale']}")
    else:
        print(c("\n  CREATE: (none)", "dim"))

    if reinforces:
        print(c(f"\n  REINFORCE ({len(reinforces)})", "bold", "yellow"))
        for ref in reinforces:
            if isinstance(ref, int):
                print(f"  {c('↑', 'yellow')} #{ref}")
                continue
            oid = ref.get("id", "?")
            lines = ref.get("source_lines", [])
            lines_str = f" L{','.join(str(l) for l in lines)}" if lines else ""
            print(f"  {c('↑', 'yellow')} #{oid}{c(lines_str, 'dim')}", end="")
            if ref.get("rationale"):
                print(f"  {c(ref['rationale'], 'dim')}", end="")
            print()
    else:
        print(c("\n  REINFORCE: (none)", "dim"))


def print_diff(results: dict[str, dict]):
    models = list(results.keys())
    if len(models) < 2:
        return

    all_titles = {}
    for model, (result, _) in results.items():
        for obs in result.get("create", []):
            title = obs.get("title", "?")
            all_titles.setdefault(title, set()).add(model)

    shared = {t for t, ms in all_titles.items() if len(ms) == len(models)}
    unique = {t: ms for t, ms in all_titles.items() if len(ms) < len(models)}

    print(c(f"\n{'━' * 70}", "dim"))
    print(c("  DIFF", "bold", "magenta"))
    print(c(f"{'━' * 70}", "dim"))

    if shared:
        print(c(f"\n  Both created ({len(shared)}):", "bold"))
        for t in shared:
            print(f"    {t}")

    if unique:
        print(c(f"\n  Unique ({len(unique)}):", "bold"))
        for t, ms in sorted(unique.items(), key=lambda x: sorted(x[1])):
            model_str = ", ".join(sorted(ms))
            print(f"    {c(model_str, 'cyan')}: {t}")

    # Reinforcement overlap
    all_reinforce_ids = {}
    for model, (result, _) in results.items():
        ids = set()
        for ref in result.get("reinforce", []):
            if isinstance(ref, int):
                ids.add(ref)
            elif isinstance(ref, dict):
                ids.add(ref.get("id"))
        all_reinforce_ids[model] = ids

    if any(all_reinforce_ids.values()):
        all_ids = set().union(*all_reinforce_ids.values())
        shared_ids = all_ids.copy()
        for ids in all_reinforce_ids.values():
            shared_ids &= ids
        print(f"\n  Reinforcements: {len(all_ids)} total, {len(shared_ids)} shared")
        for model, ids in all_reinforce_ids.items():
            only = ids - shared_ids
            if only:
                print(f"    {c(model, 'cyan')} only: #{', #'.join(str(i) for i in sorted(only))}")


def main():
    models_arg = None
    session_query = None
    empty_store = False
    list_sessions = False
    project = None
    focuses = []

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--session" and i + 1 < len(args):
            session_query = args[i + 1]; i += 2
        elif args[i] == "--models" and i + 1 < len(args):
            models_arg = []
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                models_arg.append(args[i]); i += 1
        elif args[i] == "--focuses" and i + 1 < len(args):
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                focuses.append(args[i]); i += 1
        elif args[i] == "--empty-store":
            empty_store = True; i += 1
        elif args[i] == "--list":
            list_sessions = True; i += 1
        elif args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]; i += 2
        else:
            print(f"Unknown: {args[i]}", file=sys.stderr); sys.exit(1)

    models = models_arg or ["haiku", "opus"]

    sessions = load_sessions(OUTPUT_DIR)
    if project:
        sessions = [s for s in sessions if project in s.get("project", "")]

    if list_sessions:
        for s in sorted(sessions, key=lambda x: x.get("modified", ""), reverse=True)[:30]:
            chars = len(s.get("summary", ""))
            msgs = s.get("message_count", "?")
            proj = s.get("project", "?")[:30]
            print(f"  {s['session_id'][:12]}  {chars:>6,}ch  {msgs:>3} msgs  {proj}")
        print(f"\n  {len(sessions)} total sessions", file=sys.stderr)
        return

    if not session_query:
        print("Usage: python3 scripts/compare.py --session <id> [--models haiku opus]", file=sys.stderr)
        sys.exit(1)

    session = find_session(sessions, session_query)
    if not session:
        print(f"No session matching '{session_query}'", file=sys.stderr)
        sys.exit(1)

    print(c(f"Session: {session['session_id'][:16]}...", "bold"))
    print(c(f"  {len(session['summary']):,} chars, {session.get('message_count', '?')} messages", "dim"))
    print(c(f"  Models: {', '.join(models)}", "dim"))

    if empty_store:
        manifest = "(empty — no observations yet)"
    else:
        conn = init_db()
        manifest = get_store_manifest(conn)
        conn.close()
        obs_count = manifest.count("\n") + 1 if manifest.startswith("  #") else 0
        print(c(f"  Store: {obs_count} observations", "dim"))

    # Build run variants: either model × focus grid, or just models
    variants = []
    if focuses:
        for model in models:
            for focus in focuses:
                label = f"{model}:{focus[:20]}"
                variants.append((label, model, focus))
    else:
        for model in models:
            variants.append((model, model, None))

    results = {}
    for label, model, focus in variants:
        focus_str = f" [{focus[:25]}]" if focus else ""
        print(f"\n  Running {c(label, 'bold')}{focus_str}...", end="", flush=True, file=sys.stderr)
        t0 = time.time()
        result = reduce_session(session["summary"], manifest, model=model, focus=focus)
        elapsed = time.time() - t0
        print(f" {elapsed:.1f}s", file=sys.stderr)
        results[label] = (result, elapsed)

    for label in results:
        result, elapsed = results[label]
        print_result(label, result, elapsed)

    print_diff(results)
    print()


if __name__ == "__main__":
    main()
