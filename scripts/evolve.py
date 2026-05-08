#!/usr/bin/env python3
"""
scripts/evolve.py — /memesis:evolve driver.

Replays a session transcript through the full memesis pipeline against an
isolated tempfile database, elicits expected-memory descriptions from the user,
compiles them into pytest evals under eval/recall/, runs the guard suite, and
emits a structured diagnostic delta.

Usage:
    python3 scripts/evolve.py --transcript /path/to/transcript.jsonl [--live]

Arguments:
    --transcript PATH   Path to the .jsonl transcript file to replay.
    --live              Force live LLM calls; bypass the replay LLM cache.
    --autoresearch      (Stub) Trigger the autoresearch mutation loop after
                        eval compilation. Wires actual logic in Task 4.2.

LLM Cache Monkey-patch (D-08, CONTEXT-evolve-skill.md)
--------------------------------------------------------
The transcript_ingest pipeline (and all pipeline modules it calls) imports
`call_llm` directly from `core.llm`.  We want replay sessions to route through
`cached_call_llm` without modifying any call site.

We use option (a) from the task spec: a context manager that temporarily replaces
`core.llm.call_llm` with a wrapper that calls `cached_call_llm`.  On entry the
original function is saved; on exit it is restored regardless of exceptions.

The patch is applied at the `core.llm` module level so all in-process imports
that do `from core.llm import call_llm` are NOT affected — only modules that
resolve `core.llm.call_llm` at call time (i.e. import the module, not the name)
benefit.  In practice, all internal pipeline modules import the function name,
so we also patch the per-module `call_llm` attribute on `core.transcript_ingest`,
`core.issue_cards`, `core.consolidator`, `core.crystallizer`, etc.

This approach:
  - Requires zero changes to existing pipeline code.
  - Is clearly reversible: __exit__ always restores the original.
  - Is well-tested: tests can verify the swap by inspecting the patched attribute.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator

# Ensure project root is on sys.path when run as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.replay_db import ReplayDB
from core.trace import TraceWriter, set_active_writer
from core.eval_compile import extract_spec_from_text, compile_to_pytest, EvalSpec
import core.llm as _llm_module

# ---------------------------------------------------------------------------
# --autoresearch implementation (Task 4.2)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Replay counter — persisted at ~/.claude/memesis/evolve/<session>/replay_count.json
# ---------------------------------------------------------------------------

_EVOLVE_BASE = Path.home() / ".claude" / "memesis" / "evolve"


def _replay_count_path(orig_session_id: str) -> Path:
    return _EVOLVE_BASE / orig_session_id / "replay_count.json"


def _next_replay_n(orig_session_id: str) -> int:
    """Read the current replay counter, increment it, persist atomically, return new value."""
    count_path = _replay_count_path(orig_session_id)
    count_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing count
    current = 0
    if count_path.exists():
        try:
            data = json.loads(count_path.read_text(encoding="utf-8"))
            current = int(data.get("n", 0))
        except (json.JSONDecodeError, ValueError, OSError):
            current = 0

    new_n = current + 1

    # Atomic write
    payload = json.dumps({"n": new_n})
    fd, tmp_path = tempfile.mkstemp(dir=str(count_path.parent), suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        shutil.move(tmp_path, str(count_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return new_n


# ---------------------------------------------------------------------------
# LLM cache monkey-patch context manager (see module docstring)
# ---------------------------------------------------------------------------

# Modules whose module-level `call_llm` attribute we patch during replay.
_PATCH_MODULES = [
    "core.transcript_ingest",
    "core.issue_cards",
    "core.consolidator",
    "core.crystallizer",
    "core.self_reflection",
    "core.reconsolidation",
]


@contextlib.contextmanager
def _patched_llm(force_live: bool) -> Iterator[None]:
    """Context manager: swap `core.llm.call_llm` for `cached_call_llm` during replay.

    Why: all pipeline modules import `call_llm` by name at module load time.
    Patching their per-module attribute redirects calls from code already
    loaded.  We also patch `core.llm.call_llm` itself for any late imports.

    The `force_live` flag is forwarded to `cached_call_llm` via a closure.
    """
    from core.llm_cache import cached_call_llm as _cached

    def _wrapper(prompt: str, **kwargs) -> str:
        return _cached(prompt, force_live=force_live, **kwargs)

    # Collect originals
    originals: dict[str, object] = {}
    originals["core.llm"] = _llm_module.call_llm

    import importlib
    extra_mods = {}
    for mod_name in _PATCH_MODULES:
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "call_llm"):
                extra_mods[mod_name] = (mod, mod.call_llm)
        except ImportError:
            pass

    # Apply patches
    _llm_module.call_llm = _wrapper  # type: ignore[attr-defined]
    for _, (mod, _) in extra_mods.items():
        mod.call_llm = _wrapper  # type: ignore[attr-defined]

    try:
        yield
    finally:
        # Restore always
        _llm_module.call_llm = originals["core.llm"]  # type: ignore[attr-defined]
        for mod_name, (mod, orig) in extra_mods.items():
            mod.call_llm = orig  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Slug derivation
# ---------------------------------------------------------------------------

def _slug_from_path(transcript_path: Path) -> str:
    """Derive a filesystem-safe slug from the transcript filename.

    E.g. transcripts/foo_2026-05-01.md → foo_2026-05-01
         /path/to/session-abc.jsonl    → session-abc
    """
    stem = transcript_path.stem  # filename without extension
    slug = stem.lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "session"


# ---------------------------------------------------------------------------
# Replay pipeline
# ---------------------------------------------------------------------------

def _run_replay(transcript_path: Path, base_dir: str, _session_id: str, _force_live: bool) -> None:
    """Run transcript_ingest against the replay tempfile store."""
    from core.transcript import read_transcript_from, summarize
    from core.session_detector import detect_session_type
    from core.transcript_ingest import extract_observations, append_to_ephemeral

    # Read the full transcript (from byte 0 — full replay)
    entries, _, _ = read_transcript_from(transcript_path, 0)
    if not entries:
        print(f"[evolve] Warning: no entries found in transcript {transcript_path}")
        return

    rendered = summarize(entries)

    # Detect session type
    session_cwd: str | None = None
    tool_uses: list[dict] = []
    for entry in entries:
        msg = entry.get("message") or {}
        if not session_cwd:
            session_cwd = entry.get("cwd") or msg.get("cwd")
        if entry.get("type") == "tool_use" or msg.get("type") == "tool_use":
            tool_name = entry.get("tool_name") or msg.get("name") or ""
            file_path = entry.get("input", {}).get("file_path") or ""
            if tool_name:
                tool_uses.append({"tool_name": tool_name, "file_path": file_path})

    session_type = detect_session_type(session_cwd, tool_uses or None)

    obs_list = extract_observations(rendered, session_type=session_type)
    for obs in obs_list:
        obs.setdefault("session_type", session_type)

    mem_dir = Path(base_dir)
    n = append_to_ephemeral(mem_dir, obs_list)
    print(f"[evolve] Extracted {n} observation(s) from transcript.")


# ---------------------------------------------------------------------------
# Expected-memory elicitation
# ---------------------------------------------------------------------------

def _elicit_expected_memories() -> list[str]:
    """Prompt user for expected-memory descriptions via stdin.

    Returns a list of non-empty description strings.
    """
    print()
    print("=== EXPECTED MEMORIES ===")
    print("Describe each memory you expect the pipeline to have retained.")
    print("Enter one description per line. Blank line or Ctrl-D to finish.")
    print()

    descriptions = []
    try:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if not line:
                break
            descriptions.append(line)
    except KeyboardInterrupt:
        pass

    return descriptions


# ---------------------------------------------------------------------------
# Eval compilation + guard suite
# ---------------------------------------------------------------------------

def _compile_evals(
    descriptions: list[str],
    replay_store_path: str,
    slug: str,
) -> tuple[list[EvalSpec], list[Path]]:
    """Extract specs + compile pytest files. Returns (specs, eval_paths)."""
    eval_dir = _PROJECT_ROOT / "eval" / "recall"
    eval_dir.mkdir(parents=True, exist_ok=True)

    specs: list[EvalSpec] = []
    eval_paths: list[Path] = []

    for i, description in enumerate(descriptions):
        print(f"[evolve] Compiling eval {i + 1}/{len(descriptions)}: {description[:60]}...")
        try:
            spec = extract_spec_from_text(description)
            source = compile_to_pytest(spec, replay_store_path)

            # Use session slug + spec slug to avoid collisions
            eval_slug = f"{slug}_{spec.slug}" if slug != spec.slug else spec.slug
            eval_path = eval_dir / f"{eval_slug}_recall.py"
            eval_path.write_text(source, encoding="utf-8")

            specs.append(spec)
            eval_paths.append(eval_path)
            print(f"[evolve]   → {eval_path.relative_to(_PROJECT_ROOT)} ({spec.match_mode})")
        except Exception as exc:
            print(f"[evolve]   WARNING: failed to compile eval for description {i + 1}: {exc}")

    return specs, eval_paths


def _run_guard_suite(_slug: str, eval_paths: list[Path]) -> dict[str, bool]:
    """Run pytest for tests/ + each compiled eval file. Returns {eval_slug: pass}."""
    results: dict[str, bool] = {}

    for eval_path in eval_paths:
        eval_slug = eval_path.stem  # e.g. foo_2026-05-01_oauth-token-expiry_recall
        cmd = [
            "python3", "-m", "pytest",
            "tests/",
            str(eval_path),
            "-x",
            "--tb=short",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_ROOT),
        )
        passed = proc.returncode == 0
        results[eval_slug] = passed
        status = "PASS" if passed else "FAIL"
        print(f"[evolve] {status}  {eval_slug}")

    return results


# ---------------------------------------------------------------------------
# Diagnostic delta
# ---------------------------------------------------------------------------

def _read_trace_events(session_id: str) -> list[dict]:
    """Load trace events for the given session_id from the JSONL file."""
    trace_path = Path.home() / ".claude" / "memesis" / "traces" / f"{session_id}.jsonl"
    events: list[dict] = []
    if not trace_path.exists():
        return events
    try:
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return events


def _find_loss_stage(events: list[dict], _spec: EvalSpec) -> str:
    """
    Heuristic: find the last stage boundary event before which the expected
    entities were still present (or first where they disappear).

    Returns a stage name string or 'unknown'.
    """
    # Look for stage_boundary end events in pipeline order
    stage_order = [
        "stage1_extract_end",
        "stage15_synthesis_end",
        "consolidation_end",
        "crystallization_end",
    ]

    # Find which boundary events appear in the trace
    emitted_stages = {
        e["event"]
        for e in events
        if e.get("stage") == "pipeline" and "end" in e.get("event", "")
    }

    # Report the first stage boundary present (conservative: we can't
    # definitively locate the loss without querying the replay DB at each
    # boundary, which would require capturing intermediate state — out of scope
    # for Wave 3. Return the last stage that emitted an end event as the
    # "loss candidate").
    for stage_name in reversed(stage_order):
        if stage_name in emitted_stages:
            return stage_name

    return "unknown"


def _emit_diagnostic_delta(
    specs: list[EvalSpec],
    eval_paths: list[Path],
    results: dict[str, bool],
    session_id: str,
) -> None:
    """Print a structured diagnostic delta to stdout."""
    events = _read_trace_events(session_id)

    print()
    print("=== DIAGNOSTIC DELTA ===")
    print()

    for spec, eval_path in zip(specs, eval_paths):
        eval_slug = eval_path.stem
        passed = results.get(eval_slug, False)
        status = "PASS" if passed else "FAIL"
        print(f"{status}  {spec.slug:<40} ({spec.match_mode})")
        if not passed:
            loss_stage = _find_loss_stage(events, spec)
            print(f"      Lost at: {loss_stage}")
            print(f"      Match mode failed: {spec.match_mode}")
            if spec.expected_entities:
                print(f"      Expected entities: {', '.join(spec.expected_entities)}")
            print()

    print()


# ---------------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------------

def _run_picker(top_n: int = 10, force_live: bool = False) -> Path | None:
    """Run the LLM-ranked transcript picker. Returns chosen path or None on cancel."""
    from core.transcript_picker import pick

    print("[evolve] Discovering transcripts and ranking via LLM...")
    print("[evolve] (deterministic prefilter on recency/length/friction/decisions,")
    print("[evolve]  then LLM evaluates user-message excerpts for evalability)")
    print()

    candidates = pick(force_live=force_live)
    if not candidates:
        print("[evolve] No transcripts found that match prefilter criteria.", file=sys.stderr)
        print("[evolve] (looking under ~/.claude/projects/*/*.jsonl)", file=sys.stderr)
        return None

    top = candidates[:top_n]
    print(f"=== Top {len(top)} candidates ===\n")
    for i, c in enumerate(top, start=1):
        print(f"[{i}] {c.path.name}")
        print(f"    {c.breakdown()}")
        if c.themes:
            print(f"    themes: {', '.join(c.themes)}")
        if c.rationale:
            print(f"    why: {c.rationale}")
        print(f"    capture density: {c.expected_capture_density}")
        print()

    while True:
        try:
            raw = input(f"Pick [1-{len(top)}] or 'q' to cancel: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if raw.lower() in ("q", "quit", "exit", ""):
            return None
        try:
            idx = int(raw)
        except ValueError:
            print(f"  invalid: '{raw}' — enter a number 1..{len(top)} or 'q'")
            continue
        if not (1 <= idx <= len(top)):
            print(f"  out of range: {idx} — enter 1..{len(top)}")
            continue
        chosen = top[idx - 1].path
        print(f"[evolve] Selected: {chosen}")
        return chosen


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a transcript through the memesis pipeline and compile evals."
    )
    parser.add_argument(
        "--transcript",
        required=False,
        metavar="PATH",
        help="Path to the .jsonl transcript file to replay. Mutually exclusive with --pick.",
    )
    parser.add_argument(
        "--pick",
        action="store_true",
        default=False,
        help="Interactively select a transcript via LLM-ranked picker.",
    )
    parser.add_argument(
        "--pick-top",
        type=int,
        default=10,
        metavar="N",
        help="Number of top candidates to display in the picker (default: 10).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Force live LLM calls; bypass the replay LLM cache.",
    )
    parser.add_argument(
        "--autoresearch",
        action="store_true",
        default=False,
        help="Trigger the autoresearch mutation loop after eval compilation.",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=100000,
        metavar="N",
        help="Hard token halt budget for autoresearch (default: 100000).",
    )

    args = parser.parse_args(argv)

    if args.pick and args.transcript:
        print("[evolve] ERROR: --pick and --transcript are mutually exclusive", file=sys.stderr)
        return 2
    if not args.pick and not args.transcript:
        print("[evolve] ERROR: provide --transcript PATH or --pick", file=sys.stderr)
        return 2

    if args.pick:
        picked = _run_picker(top_n=args.pick_top, force_live=args.live)
        if picked is None:
            return 0
        args.transcript = str(picked)

    transcript_path = Path(args.transcript).resolve()
    if not transcript_path.exists():
        print(f"[evolve] ERROR: transcript not found: {transcript_path}", file=sys.stderr)
        return 1

    # Derive session_id and replay counter (HI-002: sanitize path stem)
    _raw_stem = transcript_path.stem
    orig_session_id = re.sub(r"[^A-Za-z0-9_\-]", "-", _raw_stem).lstrip("-.")
    if not orig_session_id:
        orig_session_id = "session"
    n = _next_replay_n(orig_session_id)
    replay_session_id = f"replay-{orig_session_id}-{n}"
    slug = _slug_from_path(transcript_path)

    print(f"[evolve] Transcript:       {transcript_path}")
    print(f"[evolve] Replay session:   {replay_session_id}")
    print(f"[evolve] Slug:             {slug}")
    print(f"[evolve] Force live LLM:   {args.live}")
    print()

    # TraceWriter for this replay session
    trace_writer = TraceWriter(session_id=replay_session_id)
    set_active_writer(trace_writer)

    specs: list[EvalSpec] = []
    eval_paths: list[Path] = []
    results: dict[str, bool] = {}

    try:
        with ReplayDB() as base_dir:
            print(f"[evolve] Replay DB:        {base_dir}")
            print()

            # Emit trace stage boundary
            trace_writer.emit("pipeline", "replay_start", {
                "orig_session_id": orig_session_id,
                "replay_session_id": replay_session_id,
                "transcript": str(transcript_path),
            })

            # Run the pipeline with LLM cache patch
            with _patched_llm(force_live=args.live):
                _run_replay(transcript_path, base_dir, replay_session_id, args.live)

            trace_writer.emit("pipeline", "replay_end", {"base_dir": base_dir})

            # Elicit expected memories and compile evals
            descriptions = _elicit_expected_memories()
            if not descriptions:
                print("[evolve] No expected memories provided — skipping eval compilation.")
                return 0

            # Compile evals (LLM calls for spec extraction also go through cache)
            with _patched_llm(force_live=args.live):
                specs, eval_paths = _compile_evals(descriptions, base_dir, slug)

            if not eval_paths:
                print("[evolve] No evals compiled — nothing to run.")
                return 0

            # Run guard suite
            print()
            print("=== GUARD SUITE ===")
            results = _run_guard_suite(slug, eval_paths)

        # Diagnostic delta (after ReplayDB cleanup — eval files still exist on disk)
        _emit_diagnostic_delta(specs, eval_paths, results, replay_session_id)

        # --autoresearch: trigger mutation loop if any evals failed
        if args.autoresearch:
            failing_eval_paths = [
                p for p in eval_paths if not results.get(p.stem, True)
            ]
            if not failing_eval_paths:
                print("[evolve] No failing evals — autoresearch not triggered")
            else:
                # Pick the first failing eval slug as the convergence signal
                compiled_eval_slug = failing_eval_paths[0].stem

                from scripts.autoresearch_config import write_autoresearch_config
                from core.autoresearch import Autoresearcher

                session_dir = _EVOLVE_BASE / replay_session_id
                config_path = write_autoresearch_config(
                    replay_session_id,
                    max_iterations=10,
                    token_budget=args.token_budget,
                )
                print(f"[evolve] autoresearch config: {config_path}")

                researcher = Autoresearcher(
                    session_path=session_dir,
                    eval_slug=compiled_eval_slug,
                )
                ar_result = researcher.run()

                print()
                print("=== AUTORESEARCH RESULT ===")
                print(f"  iterations_completed: {ar_result.iterations_completed}")
                print(f"  mutations_kept:       {ar_result.mutations_kept}")
                print(f"  mutations_discarded:  {ar_result.mutations_discarded}")
                print(f"  total_token_spend:    {ar_result.total_token_spend}")
                print(f"  halt_reason:          {ar_result.halt_reason}")
                if ar_result.kept_files:
                    print("  kept_files:")
                    for f in ar_result.kept_files:
                        print(f"    {f}")
                print()

    except Exception as exc:
        set_active_writer(None)
        print(f"[evolve] ERROR: {exc}", file=sys.stderr)
        raise
    finally:
        set_active_writer(None)

    # Return non-zero if any eval failed
    all_passed = all(results.values()) if results else True
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
