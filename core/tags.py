"""Two-tier tag vocabulary loader and classifier for memesis.

Tier-1: namespaced tags whose namespace AND value are both in core/tag_vocabulary.yaml.
Tier-2: tags with an off-list value in a canonical namespace, or a namespace not in the
        vocabulary at all. Tier-2 tags are accepted but returned in review_entries so
        they can be written to the tag_review_log table for periodic curation.

Tag format rule: ``<namespace>:<value>`` where both sides are lowercase and value
matches ``[a-z0-9][a-z0-9-]*``.  Anything that fails this pattern is ``malformed``
and is rejected outright (not returned in accepted_tags).
"""

from __future__ import annotations

import os
import re
import textwrap
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VOCAB_PATH = Path(__file__).parent / "tag_vocabulary.yaml"
_TAG_RE = re.compile(r"^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9-]*$")

# Env override for the project-local extension file.
_PROJECT_TAGS_ENV = "MEMESIS_PROJECT_TAGS"

_vocab_cache: dict | None = None


def _project_overlay_path(project: str | None) -> Path | None:
    """Resolve the per-project tag vocabulary path, if any.

    Order of precedence:
      1. ``MEMESIS_PROJECT_TAGS`` env var (absolute path).
      2. ``~/.claude/memory/projects/<project>/tag_vocabulary.yaml`` when
         *project* is given.

    Returns ``None`` when no override is configured. Does not check the
    file exists — the caller decides whether a missing overlay is an
    error.
    """
    env_path = os.environ.get(_PROJECT_TAGS_ENV)
    if env_path:
        return Path(env_path).expanduser()
    if project:
        return (
            Path.home()
            / ".claude" / "memory" / "projects" / project / "tag_vocabulary.yaml"
        )
    return None


def _merge_vocab(base: dict, overlay: dict) -> dict:
    """Merge an overlay vocabulary on top of *base*.

    Rules:
      * Overlay namespaces that don't exist in base are added wholesale.
      * Overlay namespaces that exist in base extend the base's value list
        (de-duplicated, base order preserved, new items appended).
      * Overlay descriptions only fill in when base has none.
    """
    if not overlay:
        return base
    merged_ns = dict(base.get("namespaces", {}))
    for ns, meta in overlay.get("namespaces", {}).items():
        if not isinstance(meta, dict):
            continue
        new_values = list(meta.get("values", []))
        if ns not in merged_ns:
            merged_ns[ns] = {
                "description": meta.get("description", ""),
                "values": new_values,
            }
            continue
        existing = merged_ns[ns]
        if not isinstance(existing, dict):
            continue
        existing_values = list(existing.get("values", []))
        for v in new_values:
            if v not in existing_values:
                existing_values.append(v)
        existing["values"] = existing_values
        if not existing.get("description") and meta.get("description"):
            existing["description"] = meta["description"]
    merged = dict(base)
    merged["namespaces"] = merged_ns
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict:
    """Read a single YAML file. Returns ``{}`` for a missing file."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "PyYAML is required to load tag vocabulary files. "
            "Install it with: pip install pyyaml"
        )
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except OSError as exc:
        raise ValueError(f"Cannot read tag vocabulary at {path}: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Failed to parse tag vocabulary at {path}: {exc}") from exc


def load_vocabulary(
    path: Path | None = None,
    project: str | None = None,
) -> dict:
    """Return the parsed tag vocabulary, lazy-cached after first load.

    The returned dict has the shape::

        {
          "namespaces": {
            "technology": {"description": "...", "values": ["python", ...]},
            ...
          }
        }

    Args:
        path:    Override the default ``core/tag_vocabulary.yaml`` path.
                 Useful in tests to point at a fixture file.
        project: When set, also layer the per-project overlay file at
                 ``~/.claude/memory/projects/<project>/tag_vocabulary.yaml``
                 (or the ``MEMESIS_PROJECT_TAGS`` env override) on top of
                 the canonical vocabulary.

    Caching only applies to the default-path, no-project case. Any
    explicit *path* or *project* skips the cache for test isolation and
    correctness when multiple projects share a process.
    """
    global _vocab_cache
    use_cache = path is None and project is None
    if use_cache and _vocab_cache is not None:
        return _vocab_cache

    base = _read_yaml(path or _VOCAB_PATH)
    overlay_path = _project_overlay_path(project)
    overlay = _read_yaml(overlay_path) if overlay_path else {}
    merged = _merge_vocab(base, overlay)

    if use_cache:
        _vocab_cache = merged
    return merged


def _canonical_namespaces(vocab: dict | None = None) -> dict[str, set[str]]:
    """Return {namespace: frozenset-of-values} for all tier-1 namespaces."""
    v = vocab or load_vocabulary()
    namespaces = v.get("namespaces", {})
    return {
        ns: set(meta.get("values", []))
        for ns, meta in namespaces.items()
        if isinstance(meta, dict)
    }


def is_tier1(tag: str, vocab: dict | None = None) -> bool:
    """Return True iff *tag* is a valid tier-1 tag.

    A tier-1 tag must:
    - Pass the format check (``namespace:value``, both lowercase).
    - Have a namespace that appears in the canonical vocabulary.
    - Have a value that appears in the closed list for that namespace.
    """
    if not _TAG_RE.match(tag):
        return False
    ns, _, val = tag.partition(":")
    canonical = _canonical_namespaces(vocab)
    return ns in canonical and val in canonical[ns]


TagClassification = Literal["tier1", "tier2_off_value", "tier2_off_namespace", "malformed"]


def classify_tag(tag: str, vocab: dict | None = None) -> TagClassification:
    """Classify a single tag string into one of four categories.

    Returns:
        ``"tier1"``               — canonical namespace + value in closed list.
        ``"tier2_off_value"``     — canonical namespace, but value not in closed list.
        ``"tier2_off_namespace"`` — namespace not in canonical vocabulary (any value).
        ``"malformed"``           — does not match ``<ns>:<value>`` format rules.
    """
    if not _TAG_RE.match(tag):
        return "malformed"
    ns, _, val = tag.partition(":")
    canonical = _canonical_namespaces(vocab)
    if ns not in canonical:
        return "tier2_off_namespace"
    if val in canonical[ns]:
        return "tier1"
    return "tier2_off_value"


def validate_tags(
    tags: list[str],
    vocab: dict | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Validate a list of tag strings against the two-tier vocabulary.

    Args:
        tags:  Raw tag strings to validate.
        vocab: Optional pre-loaded vocabulary dict (for testing / caching).

    Returns:
        A tuple ``(accepted_tags, review_entries)`` where:

        - ``accepted_tags`` — all non-malformed tags (tier1 + tier2).
          Malformed tags are silently dropped.
        - ``review_entries`` — ``(tag, classification)`` pairs for every tier-2
          tag.  These should be written to ``tag_review_log`` for curation.
    """
    accepted: list[str] = []
    review: list[tuple[str, str]] = []

    for tag in tags:
        classification = classify_tag(tag, vocab)
        if classification == "malformed":
            continue  # rejected — not included in accepted_tags
        accepted.append(tag)
        if classification != "tier1":
            review.append((tag, classification))

    return accepted, review


def render_for_prompt(vocab: dict | None = None) -> str:
    """Render the tier-1 vocabulary as a markdown block for embedding in LLM prompts.

    Example output::

        **Tag vocabulary (use namespaced `prefix:value`):**

        - `technology:` python, peewee, sqlite, tree-sitter, ...
        - `domain:` database, retrieval, extraction, ...
        - `scope:` project-local, global, plugin, session
        - `severity:` data-loss, performance, correctness, usability

        You may introduce new tags outside these lists if needed; they'll be reviewed later.
    """
    v = vocab or load_vocabulary()
    namespaces = v.get("namespaces", {})

    ns_lines: list[str] = []
    for ns, meta in namespaces.items():
        if not isinstance(meta, dict):
            continue
        values = meta.get("values", [])
        values_str = ", ".join(str(val) for val in values)
        ns_lines.append(f"- `{ns}:` {values_str}")

    ns_block = "\n".join(ns_lines)
    return textwrap.dedent(f"""\
        **Tag vocabulary (use namespaced `prefix:value`):**

        {ns_block}

        You may introduce new tags outside these lists if needed; they'll be reviewed later.
    """)
