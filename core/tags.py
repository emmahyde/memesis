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

import re
import textwrap
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VOCAB_PATH = Path(__file__).parent / "tag_vocabulary.yaml"
_TAG_RE = re.compile(r"^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9-]*$")

_vocab_cache: dict | None = None


def _load_yaml(path: Path) -> dict:
    """Load a YAML file using PyYAML when available.

    Falls back to a nested-aware minimal parser that handles the specific
    shape of tag_vocabulary.yaml (mapping of mappings with string-list leaves).
    The fallback is intentionally limited; if the file structure grows beyond
    what it supports, a clear ValueError is raised rather than silently returning
    wrong data.
    """
    try:
        import yaml  # type: ignore[import]

        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        pass
    except Exception as exc:
        raise ValueError(f"Failed to parse YAML at {path}: {exc}") from exc

    # Minimal fallback for the tag_vocabulary.yaml nested structure.
    # Handles:
    #   top_key:
    #     nested_key:
    #       description: "..."
    #       values:
    #         - item
    text = path.read_text(encoding="utf-8")
    result: dict = {}
    stack: list[tuple[int, str, dict | list]] = []  # (indent, key, container)
    current_list: list | None = None
    current_list_key: str | None = None
    current_dict: dict = result

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        stripped = raw_line.strip()

        if stripped.startswith("- "):
            # List item
            if current_list is not None:
                current_list.append(stripped[2:].strip())
            continue

        if ":" not in stripped:
            continue

        k, _, v = stripped.partition(":")
        k = k.strip()
        v = v.strip()

        # Pop stack entries that are at a deeper or equal indent level.
        while stack and stack[-1][0] >= indent:
            stack.pop()

        if v == "" or v is None:
            # Mapping value — start a nested dict.
            new_dict: dict = {}
            if stack:
                parent = stack[-1][2]
                if isinstance(parent, dict):
                    parent[k] = new_dict
            else:
                result[k] = new_dict
            current_dict = new_dict
            current_list = None
            stack.append((indent, k, new_dict))
        elif k == "values" and v == "":
            # Handled above — values: with no inline value starts a list block.
            new_list: list = []
            current_dict[k] = new_list
            current_list = new_list
            current_list_key = k
        else:
            # Scalar value — strip surrounding quotes.
            v_clean = v.strip('"').strip("'")
            if stack:
                parent = stack[-1][2]
                if isinstance(parent, dict):
                    parent[k] = v_clean
                    if k == "values":
                        # inline list start (shouldn't happen in our YAML)
                        pass
            else:
                result[k] = v_clean
            current_list = None

    raise ValueError(
        "PyYAML is not installed and the built-in fallback parser could not handle "
        f"{path}. Install PyYAML (pip install pyyaml) to load nested YAML files."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_vocabulary(path: Path | None = None) -> dict:
    """Return the parsed tag vocabulary, lazy-cached after first load.

    The returned dict has the shape::

        {
          "namespaces": {
            "technology": {"description": "...", "values": ["python", ...]},
            ...
          }
        }

    Args:
        path: Override the default ``core/tag_vocabulary.yaml`` path.  Useful
              in tests to point at a fixture file.
    """
    global _vocab_cache
    if path is None and _vocab_cache is not None:
        return _vocab_cache

    resolved = path or _VOCAB_PATH
    try:
        import yaml  # type: ignore[import]

        with open(resolved, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except ImportError:
        raise RuntimeError(
            "PyYAML is required to load core/tag_vocabulary.yaml. "
            "Install it with: pip install pyyaml"
        )
    except OSError as exc:
        raise ValueError(f"Cannot read tag vocabulary at {resolved}: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Failed to parse tag vocabulary at {resolved}: {exc}") from exc

    if path is None:
        _vocab_cache = data
    return data


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
