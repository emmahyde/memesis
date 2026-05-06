"""
scripts/autoresearch_config.py — Config writer for --autoresearch in evolve.py.

Provides write_autoresearch_config() which writes autoresearch.yaml to the
session directory under ~/.claude/memesis/evolve/<session_id>/.

Atomic write via tempfile.mkstemp + shutil.move per project convention.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# D-14 mutation surface (relative file paths from project root)
# ---------------------------------------------------------------------------

_MUTATION_SURFACE = [
    "core/prompts.py",
    "core/issue_cards.py",
    "core/rule_registry.py",
    "core/consolidator.py",
    "core/crystallizer.py",
]

# ---------------------------------------------------------------------------
# D-15 guard suite commands
# ---------------------------------------------------------------------------

_GUARD_SUITE = [
    "python3 -m pytest tests/",
    (
        "python3 -m pytest tests/ -k "
        "\"TestCardImportance or TestAllIndicesInvalidDemotion or "
        "TestRule3KensingerRemoved or TestEvidenceIndicesValidation\""
    ),
    "python3 -m pytest eval/recall/",
    "python3 -m pytest tests/ -k test_manifest",
]

# ---------------------------------------------------------------------------
# Base path
# ---------------------------------------------------------------------------

_EVOLVE_BASE = Path.home() / ".claude" / "memesis" / "evolve"


def write_autoresearch_config(
    session_id: str,
    *,
    max_iterations: int = 10,
    token_budget: int = 100000,
    mutation_surface: list[str] | None = None,
    guard_suite: list[str] | None = None,
) -> Path:
    """Write autoresearch.yaml for the given evolve session.

    Parameters
    ----------
    session_id:
        The evolve session identifier (used as directory name under
        ~/.claude/memesis/evolve/).
    max_iterations:
        Max mutation loop iterations (D-16 default: 10).
    token_budget:
        Hard token halt budget (D-16). Default: 100000.
    mutation_surface:
        Override the D-14 file list. Defaults to _MUTATION_SURFACE.
    guard_suite:
        Override the D-15 command list. Defaults to _GUARD_SUITE.

    Returns
    -------
    Path
        Absolute path to the written autoresearch.yaml.
    """
    session_dir = _EVOLVE_BASE / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    surface = mutation_surface if mutation_surface is not None else _MUTATION_SURFACE
    guards = guard_suite if guard_suite is not None else _GUARD_SUITE

    # Build YAML content manually — PyYAML is an optional dep.
    # We produce a human-readable minimal YAML that core/autoresearch.py's
    # _load_yaml() can parse with either PyYAML or the fallback line-parser.
    lines: list[str] = []
    lines.append(f"max_iterations: {max_iterations}")
    lines.append(f"token_budget: {token_budget}")
    lines.append("iteration_count: 0")
    lines.append("token_spend: 0")

    # mutation_surface as YAML list
    lines.append("mutation_surface:")
    for item in surface:
        lines.append(f"  - {item}")

    # guard_suite as YAML list (items may contain spaces/quotes — wrap in double quotes)
    lines.append("guard_suite:")
    for cmd in guards:
        # Escape any double quotes in the command string
        escaped = cmd.replace('"', '\\"')
        lines.append(f'  - "{escaped}"')

    text = "\n".join(lines) + "\n"

    # Atomic write
    config_path = session_dir / "autoresearch.yaml"
    fd, tmp_path = tempfile.mkstemp(dir=str(session_dir), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        shutil.move(tmp_path, str(config_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return config_path
