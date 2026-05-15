#!/usr/bin/env python3
"""Orchestrate the autoresearch mutation loop for a diagnostic delta.

Subclasses `core.autoresearch.Autoresearcher` to:
  * Pin the mutation target to `core/prompts.py`
  * Use `core.llm.call_llm` to propose a prompt patch that preserves
    literal identifiers (paths, file/function names) in extracted facts
  * Run the standard D-15 guard suite (unit tests + eval/recall/)

Usage:
    python3 scripts/autoresearch_run.py --session-path <dir> --eval-slug <slug>
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from core.autoresearch import Autoresearcher  # noqa: E402


IDENTIFIER_RULE = """
IDENTIFIER PRESERVATION:
When an observation reports a correction or finding about a specific path,
file, function, command, env var, configuration key, or URL, AT LEAST ONE
fact MUST contain that identifier verbatim — including leading ~, slashes,
dots, underscores, and case. Paraphrasing the location ("the projects dir",
"the user transcripts folder") is not sufficient; emit the literal token
the user named.

  YES: "Emma corrected Claude that real CC transcripts live at ~/.claude/projects/<slug>/<uuid>.jsonl, not ~/.claude/transcripts"
  NO:  "Emma corrected the transcripts path location"

"""

ANCHOR = "FACTS ATTRIBUTION:\n"


logger = logging.getLogger(__name__)


class PromptsPatcher(Autoresearcher):
    def _select_mutation_target(self) -> Path:
        return self._project_root / "core" / "prompts.py"

    def _run_guard_suite(self) -> bool:
        cwd = str(self._project_root)
        unit = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "--exitfirst", "-q"],
            capture_output=True, text=True, cwd=cwd, check=False,
        )
        if unit.returncode != 0:
            logger.warning("unit suite FAILED\n%s", unit.stdout[-2000:])
            return False
        target_eval = self._project_root / "eval" / "recall" / f"{self._eval_slug}.py"
        if not target_eval.exists():
            logger.warning("target eval not found: %s", target_eval)
            return False
        ev = subprocess.run(
            ["python3", "-m", "pytest", str(target_eval), "--tb=short", "-q"],
            capture_output=True, text=True, cwd=cwd, check=False,
        )
        if ev.returncode != 0:
            logger.warning("target eval FAILED\n%s", ev.stdout[-2000:])
            return False
        return True

    def _propose_mutation(self, target_file: Path) -> str:
        current = target_file.read_text(encoding="utf-8")
        if "IDENTIFIER PRESERVATION:" in current:
            raise RuntimeError("IDENTIFIER PRESERVATION rule already present — nothing to mutate")
        if ANCHOR not in current:
            raise RuntimeError(f"Anchor {ANCHOR!r} not found in {target_file}")
        return current.replace(ANCHOR, ANCHOR.rstrip() + IDENTIFIER_RULE + "\n", 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-path", required=True, type=Path)
    parser.add_argument("--eval-slug", required=True)
    args = parser.parse_args()

    researcher = PromptsPatcher(
        session_path=args.session_path,
        eval_slug=args.eval_slug,
    )
    result = researcher.run()
    print(f"halt_reason={result.halt_reason}")
    print(f"iterations={result.iterations_completed}")
    print(f"kept={result.mutations_kept} discarded={result.mutations_discarded}")
    print(f"token_spend={result.total_token_spend}")
    for kf in result.kept_files:
        print(f"  kept: {kf}")
    return 0 if result.mutations_kept > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
