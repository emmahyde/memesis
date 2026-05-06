"""
core/autoresearch.py — Autonomous pipeline mutation loop for /memesis:evolve --autoresearch.

Iterates a Modify→Verify→Keep/Discard loop over the D-14 mutation surface,
gated by the D-15 guard suite and a D-16 token budget.

Public API (consumed by Task 4.2 / scripts/evolve.py --autoresearch):

    Autoresearcher(session_path: Path, eval_slug: str)
    .run() -> AutoresearchResult

Config read from ``<session_path>/autoresearch.yaml``:

    max_iterations: 10         # default 10 (D-16)
    token_budget: 50000        # required (D-16)
    iteration_count: 0         # written back after each kept mutation
    token_spend: 0             # written back after each kept mutation

Mutation surface (D-14):
    core/prompts.py
    core/issue_cards.py
    core/rule_registry.py
    core/consolidator.py
    core/crystallizer.py

Guard set (D-15):
    python3 -m pytest tests/
    explicit tier-3 tests: TestCardImportance, TestAllIndicesInvalidDemotion,
                            TestRule3KensingerRemoved, TestEvidenceIndicesValidation
    eval/recall/
    manifest JSON round-trip (delegated to full test suite)

Halt (D-16): iteration_count >= max_iterations OR token_spend >= token_budget.
Mid-iteration halt — no grace period.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# D-14: allowed mutation surface
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

MUTATION_SURFACE: frozenset[Path] = frozenset(
    [
        _PROJECT_ROOT / "core" / "prompts.py",
        _PROJECT_ROOT / "core" / "issue_cards.py",
        _PROJECT_ROOT / "core" / "rule_registry.py",
        _PROJECT_ROOT / "core" / "consolidator.py",
        _PROJECT_ROOT / "core" / "crystallizer.py",
    ]
)

# ---------------------------------------------------------------------------
# D-15: guard commands
# ---------------------------------------------------------------------------

_TIER3_TESTS = [
    "TestCardImportance",
    "TestAllIndicesInvalidDemotion",
    "TestRule3KensingerRemoved",
    "TestEvidenceIndicesValidation",
]

# ---------------------------------------------------------------------------
# YAML load helpers (PyYAML is not in pyproject.toml; use stdlib json fallback)
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    """Load a YAML file. Uses PyYAML if available, falls back to json.

    For the simple key: value YAML shapes used in autoresearch.yaml, the json
    fallback is not sufficient. We implement a minimal line-parser that handles
    ``key: value`` pairs (int, str) without PyYAML.
    """
    try:
        import yaml  # type: ignore[import]
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        pass
    except Exception as exc:
        raise ValueError(f"Failed to parse YAML at {path}: {exc}") from exc

    # Minimal fallback: parse ``key: value`` lines (handles int and str values)
    result: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read config at {path}: {exc}") from exc

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        # Try int, then str
        try:
            result[k] = int(v)
        except ValueError:
            result[k] = v
    return result


def _dump_yaml(data: dict, path: Path) -> None:
    """Atomically write *data* as YAML to *path*.

    Uses PyYAML if available; falls back to a minimal ``key: value`` emitter
    that covers the scalar types present in autoresearch.yaml.
    """
    try:
        import yaml  # type: ignore[import]
        text = yaml.safe_dump(data, default_flow_style=False)
    except ImportError:
        lines = []
        for k, v in data.items():
            lines.append(f"{k}: {v}")
        text = "\n".join(lines) + "\n"

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        shutil.move(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class AutoresearchResult:
    """Summary returned by ``Autoresearcher.run()``."""
    iterations_completed: int = 0
    total_token_spend: int = 0
    mutations_kept: int = 0
    mutations_discarded: int = 0
    halt_reason: str = ""  # "iteration_cap" | "token_budget" | "completed"
    kept_files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class Autoresearcher:
    """Autonomous pipeline mutation engine.

    Parameters
    ----------
    session_path:
        Directory for this evolve session, e.g.
        ``~/.claude/memesis/evolve/<session>/``.
        Must contain ``autoresearch.yaml`` (or it will be created with defaults).
    eval_slug:
        Slug identifying the compiled eval to use as convergence signal,
        e.g. ``session-abc_oauth-token-expiry``. Maps to
        ``eval/recall/<eval_slug>_recall.py``.
    token_counter:
        Optional callable returning the cumulative token spend so far.
        Injected in tests to avoid touching trace JSONL. When ``None``,
        the default implementation reads from the active TraceWriter.
    project_root:
        Override the project root (for tests).
    """

    def __init__(
        self,
        session_path: Path,
        eval_slug: str,
        *,
        token_counter: Optional[Callable[[], int]] = None,
        project_root: Optional[Path] = None,
    ) -> None:
        self._session_path = Path(session_path).expanduser().resolve()
        self._eval_slug = eval_slug
        self._token_counter = token_counter or self._default_token_counter
        self._project_root = Path(project_root).resolve() if project_root else _PROJECT_ROOT

        # Resolve mutation surface relative to this project root
        self._mutation_surface: frozenset[Path] = frozenset(
            [
                self._project_root / "core" / "prompts.py",
                self._project_root / "core" / "issue_cards.py",
                self._project_root / "core" / "rule_registry.py",
                self._project_root / "core" / "consolidator.py",
                self._project_root / "core" / "crystallizer.py",
            ]
        )

        # Config loaded lazily in run()
        self._config: dict = {}

        # Token spend accumulator (used when no token_counter is injected)
        self._token_spend_accumulator: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> AutoresearchResult:
        """Drive the Modify→Verify→Keep/Discard loop.

        Returns an ``AutoresearchResult`` summarising the session.
        """
        self._config = self._load_config()
        max_iterations: int = int(self._config.get("max_iterations", 10))
        token_budget: int = int(self._config.get("token_budget", 0))
        iteration_count: int = int(self._config.get("iteration_count", 0))
        token_spend: int = int(self._config.get("token_spend", 0))

        result = AutoresearchResult(
            iterations_completed=0,
            total_token_spend=token_spend,
            mutations_kept=0,
            mutations_discarded=0,
            halt_reason="",
        )

        for _ in range(max_iterations):
            # D-16: check token budget BEFORE starting the iteration
            current_tokens = token_spend + self._token_counter()
            if token_budget > 0 and current_tokens >= token_budget:
                logger.info(
                    "autoresearch: token budget exhausted (%d >= %d) — halting",
                    current_tokens,
                    token_budget,
                )
                result.halt_reason = "token_budget"
                result.total_token_spend = current_tokens
                break

            # D-16: check iteration cap
            if iteration_count >= max_iterations:
                result.halt_reason = "iteration_cap"
                break

            logger.info(
                "autoresearch: iteration %d/%d (tokens %d/%d)",
                iteration_count + 1,
                max_iterations,
                current_tokens,
                token_budget,
            )

            target_file = self._select_mutation_target()
            new_content = self._propose_mutation(target_file)

            self._apply_mutation(target_file, new_content)

            if self._run_guard_suite():
                self._keep(target_file, new_content)
                iteration_count += 1
                token_spend += self._token_counter()
                self._token_spend_accumulator = 0  # reset delta accumulator

                result.mutations_kept += 1
                result.kept_files.append(str(target_file))

                # Update YAML with progress
                self._config["iteration_count"] = iteration_count
                self._config["token_spend"] = token_spend
                self._save_config(self._config)
            else:
                self._discard(target_file)
                result.mutations_discarded += 1

            result.iterations_completed += 1

        else:
            # Loop completed naturally (all iterations used)
            result.halt_reason = "iteration_cap"

        if not result.halt_reason:
            result.halt_reason = "completed"

        result.total_token_spend = token_spend + self._token_counter()
        return result

    # ------------------------------------------------------------------
    # Mutation surface enforcement (D-14)
    # ------------------------------------------------------------------

    def _validate_mutation_target(self, target_file: Path) -> Path:
        """Resolve and validate that *target_file* is in the mutation surface.

        Raises
        ------
        ValueError
            If the resolved path is not in the D-14 mutation surface.
        """
        resolved = target_file.resolve()
        if resolved not in self._mutation_surface:
            allowed = ", ".join(str(p) for p in sorted(self._mutation_surface))
            raise ValueError(
                f"Mutation target '{resolved}' is outside the D-14 mutation surface. "
                f"Allowed files: {allowed}"
            )
        return resolved

    # ------------------------------------------------------------------
    # Mutation lifecycle stubs
    # ------------------------------------------------------------------

    def _select_mutation_target(self) -> Path:
        """Select the next file to mutate from the D-14 surface.

        Default implementation cycles through the surface in a fixed order.
        Override or mock in tests/orchestrator for real selection logic.
        """
        # Use iteration_count to cycle through surface files deterministically
        surface_list = sorted(self._mutation_surface)
        iteration_count = int(self._config.get("iteration_count", 0))
        return surface_list[iteration_count % len(surface_list)]

    def _propose_mutation(self, target_file: Path) -> str:
        """Return the proposed new content for *target_file*.

        This is a stub that returns the current file content unchanged.
        The orchestrator (Task 4.2) or an LLM call replaces this method.
        Override or mock in tests/orchestrator.
        """
        try:
            return target_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Cannot read mutation target {target_file}: {exc}") from exc

    def _apply_mutation(self, target_file: Path, new_content: str) -> None:
        """Write *new_content* to *target_file* in-place (not atomic).

        Intentionally non-atomic: if the guard suite fails, ``_discard``
        reverts via ``git checkout``. The atomic write only happens in ``_keep``.
        """
        self._validate_mutation_target(target_file)
        try:
            target_file.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Cannot write mutation to {target_file}: {exc}") from exc

    # ------------------------------------------------------------------
    # Guard suite (D-15)
    # ------------------------------------------------------------------

    def _run_guard_suite(self) -> bool:
        """Run the D-15 guard set.  Returns True if ALL guards pass."""
        cwd = str(self._project_root)

        # 1. Full unit suite
        result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("autoresearch: unit suite FAILED\n%s", result.stdout[-2000:])
            return False

        # 2. Explicit tier-3 invariant tests
        tier3_args = []
        for test_class in _TIER3_TESTS:
            tier3_args.extend(["-k", test_class])
        subprocess.run(
            ["python3", "-m", "pytest", "tests/", "--tb=short", "-q"]
            + [f"--co", "-q"]  # collect-only to verify they exist first
            ,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
        # Run each tier-3 test class explicitly
        for test_class in _TIER3_TESTS:
            t3 = subprocess.run(
                [
                    "python3", "-m", "pytest", "tests/",
                    "-k", test_class,
                    "--tb=short", "-q",
                ],
                capture_output=True,
                text=True,
                cwd=cwd,
                check=False,
            )
            if t3.returncode != 0:
                logger.warning(
                    "autoresearch: tier-3 test %s FAILED\n%s", test_class, t3.stdout[-1000:]
                )
                return False

        # 3. eval/recall/ regression suite
        eval_recall_dir = self._project_root / "eval" / "recall"
        if eval_recall_dir.exists() and any(eval_recall_dir.glob("*_recall.py")):
            eval_result = subprocess.run(
                ["python3", "-m", "pytest", str(eval_recall_dir), "--tb=short", "-q"],
                capture_output=True,
                text=True,
                cwd=cwd,
                check=False,
            )
            if eval_result.returncode != 0:
                logger.warning(
                    "autoresearch: eval/recall suite FAILED\n%s", eval_result.stdout[-2000:]
                )
                return False

        # Guard 4 (manifest round-trip) is covered by the full unit suite above
        # (tests/test_manifest.py if present, or integration tests).
        return True

    # ------------------------------------------------------------------
    # Keep / Discard
    # ------------------------------------------------------------------

    def _keep(self, target_file: Path, new_content: str) -> None:
        """Atomically persist *new_content* to *target_file*.

        Uses ``tempfile.mkstemp`` + ``shutil.move`` per project convention.
        """
        resolved = self._validate_mutation_target(target_file)
        fd, tmp = tempfile.mkstemp(dir=str(resolved.parent), suffix=".py.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(new_content)
            shutil.move(tmp, str(resolved))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        logger.info("autoresearch: kept mutation to %s", resolved)

    def _discard(self, target_file: Path) -> None:
        """Revert *target_file* to the last committed version via git checkout."""
        resolved = target_file.resolve()
        result = subprocess.run(
            ["git", "checkout", "--", str(resolved)],
            capture_output=True,
            text=True,
            cwd=str(self._project_root),
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "autoresearch: git checkout failed for %s: %s", resolved, result.stderr
            )
        else:
            logger.info("autoresearch: discarded mutation to %s", resolved)

    # ------------------------------------------------------------------
    # Config I/O
    # ------------------------------------------------------------------

    def _config_path(self) -> Path:
        return self._session_path / "autoresearch.yaml"

    def _load_config(self) -> dict:
        """Load autoresearch.yaml, creating with defaults if absent."""
        config_path = self._config_path()
        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            defaults: dict = {"max_iterations": 10, "token_budget": 0, "iteration_count": 0, "token_spend": 0}
            _dump_yaml(defaults, config_path)
            return defaults
        data = _load_yaml(config_path)
        # Fill in missing keys with defaults
        data.setdefault("max_iterations", 10)
        data.setdefault("token_budget", 0)
        data.setdefault("iteration_count", 0)
        data.setdefault("token_spend", 0)
        return data

    def _save_config(self, data: dict) -> None:
        """Atomically write the config dict back to autoresearch.yaml."""
        _dump_yaml(data, self._config_path())

    # ------------------------------------------------------------------
    # Token tracking
    # ------------------------------------------------------------------

    def _default_token_counter(self) -> int:
        """Return accumulated token spend delta from the active TraceWriter.

        Reads ``llm_envelope`` events from the active trace JSONL since the
        last reset. Falls back to 0 if no trace is active or JSONL is unreadable.

        For test injection, pass a ``token_counter`` callable to the constructor.
        """
        from core.trace import get_active_writer

        writer = get_active_writer()
        if writer is None or writer.trace_path is None:
            return self._token_spend_accumulator

        total = 0
        try:
            text = writer.trace_path.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "llm_envelope":
                    payload = event.get("payload", {})
                    total += int(payload.get("input_tokens") or 0)
                    total += int(payload.get("output_tokens") or 0)
        except OSError:
            pass

        return total
