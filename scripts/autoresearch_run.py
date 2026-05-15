#!/usr/bin/env python3
"""Orchestrate the autoresearch mutation loop for a diagnostic delta.

Subclasses `core.autoresearch.Autoresearcher` to:
  * Pin the mutation target to `core/prompts.py`
  * Use `core.llm.call_llm` to propose a short rule to insert at a named
    anchor in the Stage-1 extraction prompt, given the failing eval
  * Run the standard D-15 guard suite (unit tests + eval/recall/)

Usage:
    python3 scripts/autoresearch_run.py --session-path <dir> --eval-slug <slug>
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from core.autoresearch import Autoresearcher  # noqa: E402
from core.llm import call_llm  # noqa: E402


ANCHOR = "FACTS ATTRIBUTION:\n"

PROPOSE_PROMPT = """You are improving a Stage-1 fact-extraction prompt used by a memory pipeline. \
A regression eval is failing: the pipeline's extracted facts do not contain the identifiers/entities \
the eval expects to find.

=== FAILING EVAL SOURCE ===
{eval_src}

=== CURRENT EXTRACTION PROMPT (full file) ===
{prompt_src}

Propose ONE short rule (≤400 characters, plain prose, no markdown headers, \
no code fences, no bullet lists) to insert at the named anchor \
"{anchor}" inside the prompt. The rule must:
  - Address the specific failure mode visible in the eval (e.g. missing literal \
identifier, missing entity, wrong abstraction level)
  - Not contradict existing rules in the prompt
  - Be self-contained (no references like "see above")
  - Begin with an UPPERCASE TITLE: a short ALL-CAPS label, then a colon, then \
the rule body. Example: "PRESERVE LITERAL TOKENS: when ..."

Output ONLY the rule text. No preamble, no trailing commentary, no quotes \
around the output. The first non-whitespace characters must be the UPPERCASE \
TITLE."""


logger = logging.getLogger(__name__)


class PromptsPatcher(Autoresearcher):
    def __init__(self, *args, replay_store: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._replay_store = replay_store

    def _select_mutation_target(self) -> Path:
        return self._project_root / "core" / "prompts.py"

    def _guard_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._replay_store:
            env["MEMESIS_REPLAY_STORE"] = self._replay_store
        return env

    def _run_guard_suite(self) -> bool:
        cwd = str(self._project_root)
        env = self._guard_env()
        unit = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "--exitfirst", "-q"],
            capture_output=True, text=True, cwd=cwd, check=False, env=env,
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
            capture_output=True, text=True, cwd=cwd, check=False, env=env,
        )
        if ev.returncode != 0:
            logger.warning("target eval FAILED\n%s", ev.stdout[-2000:])
            return False
        return True

    def _eval_source(self) -> str:
        eval_path = self._project_root / "eval" / "recall" / f"{self._eval_slug}.py"
        if not eval_path.exists():
            raise RuntimeError(f"failing eval not found: {eval_path}")
        return eval_path.read_text(encoding="utf-8")

    def _propose_mutation(self, target_file: Path) -> str:
        current = target_file.read_text(encoding="utf-8")
        if ANCHOR not in current:
            raise RuntimeError(f"Anchor {ANCHOR!r} not found in {target_file}")

        eval_src = self._eval_source()
        prompt = PROPOSE_PROMPT.format(
            eval_src=eval_src,
            prompt_src=current,
            anchor=ANCHOR.rstrip(),
        )
        logger.info("autoresearch: requesting LLM proposal (eval=%s)", self._eval_slug)
        rule = call_llm(prompt, max_tokens=600, temperature=0).strip()

        if not rule:
            raise RuntimeError("LLM proposed an empty rule")
        if not re.match(r"^[A-Z][A-Z0-9 _\-]{2,}:", rule):
            raise RuntimeError(f"LLM proposal lacks required UPPERCASE TITLE prefix: {rule[:80]!r}")
        if len(rule) > 600:
            raise RuntimeError(f"LLM proposal exceeds 600 chars ({len(rule)})")
        title = rule.split(":", 1)[0].strip()
        if title in current:
            raise RuntimeError(f"proposed title {title!r} already present in target — nothing new to insert")

        logger.info("autoresearch: applying rule %r (%d chars)", title, len(rule))
        block = "\n" + rule + "\n\n"
        return current.replace(ANCHOR, ANCHOR.rstrip() + "\n" + block, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-path", required=True, type=Path)
    parser.add_argument("--eval-slug", required=True)
    parser.add_argument(
        "--replay-store",
        default=None,
        help="Path to a kept ReplayDB tempdir (from `evolve --keep-store`). "
             "Exported as MEMESIS_REPLAY_STORE for the guard suite.",
    )
    args = parser.parse_args()

    researcher = PromptsPatcher(
        session_path=args.session_path,
        eval_slug=args.eval_slug,
        replay_store=args.replay_store,
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
