"""
Code reference extractor for memesis memory rows (task #18).

Provides ``extract_code_refs(content) -> list[dict]`` — a pure regex
baseline that parses structured code references from observation or memory
content.  No LLM, no DB.  Callers in the write paths use this as the
default; the consolidation LLM can override with a higher-quality list.

Each returned dict has the shape::

    {
        "symbol": str,              # dotted name or bare identifier
        "file":   str | None,       # relative path, e.g. "core/models.py"
        "lang":   str | None,       # inferred language: "py", "ts", "js", "rb", "cs"
        "line":   int | None,       # line number from "file:line" citations
    }

Extraction pipeline
-------------------
Three independent passes, results merged and deduplicated by symbol+file:

1. **File-path pass** — matches repo-relative file paths with known extensions
   (.py, .ts, .tsx, .js, .rb, .cs).  ``lang`` is inferred from extension.
   Examples: ``core/models.py``, ``src/App.tsx``, ``lib/client.rb``

2. **File-path:line pass** — ``<path>:<line>`` citations.
   Examples: ``lifecycle.py:221``, ``core/consolidator.py:993``

3. **Dotted-symbol pass** — Python-ish qualified names with at least one dot,
   or PascalCase / snake_case bare symbols preceded by backtick or space.
   Examples: ``Memory.create``, ``core.llm.call_llm``, ``ConsolidationDecision``

All three passes are applied to every line of ``content``; backtick-wrapped
tokens are preferred over plain-text tokens (less false-positive rate).
"""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Extension → language tag
# ---------------------------------------------------------------------------

_EXT_LANG: dict[str, str] = {
    ".py": "py",
    ".ts": "ts",
    ".tsx": "ts",
    ".js": "js",
    ".jsx": "js",
    ".rb": "rb",
    ".cs": "cs",
}

_KNOWN_EXTS_RE = r"(?:\.py|\.ts|\.tsx|\.js|\.jsx|\.rb|\.cs)"

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# File path: optional path prefix + filename + known extension
# Captures paths like core/models.py, src/components/App.tsx, lib/client.rb
_RE_FILE_PATH = re.compile(
    r"(?<![/\w])"                         # not preceded by a word char or /
    r"((?:[\w.-]+/)*[\w.-]+" + _KNOWN_EXTS_RE + r")"
    r"(?![\w/])",                          # not followed by a word char or /
    re.ASCII,
)

# File path + line: <path>:<integer>
_RE_FILE_LINE = re.compile(
    r"(?<![/\w])"
    r"((?:[\w.-]+/)*[\w.-]+" + _KNOWN_EXTS_RE + r")"
    r":(\d+)"
    r"(?!\d)",
    re.ASCII,
)

# Dotted qualified name: at least two dotted components, each a valid Python
# identifier (allows leading underscore).
# Examples: core.llm.call_llm, Memory.create, LifecycleManager._execute_promote
_RE_DOTTED_SYMBOL = re.compile(
    r"(?<![.\w])"
    r"([_A-Za-z]\w*(?:\.[_A-Za-z]\w*)+)"
    r"(?![.\w])",
    re.ASCII,
)

# PascalCase bare symbol (class-like names without a dot): ConsolidationDecision
_RE_PASCAL = re.compile(
    r"(?<![.\w])"
    r"([A-Z][a-z]+(?:[A-Z][a-zA-Z0-9]+)+)"
    r"(?![.\w])",
    re.ASCII,
)

# Backtick-wrapped tokens — highest confidence signal
_RE_BACKTICK = re.compile(r"`([^`\n]{1,120})`")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_code_refs(content: str) -> list[dict]:
    """Extract structured code references from *content*.

    Pure function — no I/O, no LLM, no DB.  Safe to call on any string.

    Returns a deduplicated list of dicts with keys ``symbol``, ``file``,
    ``lang``, ``line``.  ``file``, ``lang``, and ``line`` may be None when
    not determinable.
    """
    if not content or not isinstance(content, str):
        return []

    seen: set[tuple] = set()
    results: list[dict] = []

    def _add(symbol: str, file: Optional[str], lang: Optional[str], line: Optional[int]) -> None:
        key = (symbol, file)
        if key in seen:
            return
        seen.add(key)
        results.append({"symbol": symbol, "file": file, "lang": lang, "line": line})

    # ------------------------------------------------------------------
    # Pass 1 + 2: file paths (with and without line numbers)
    # ------------------------------------------------------------------
    for m in _RE_FILE_LINE.finditer(content):
        path = m.group(1)
        line = int(m.group(2))
        ext = _file_ext(path)
        lang = _EXT_LANG.get(ext)
        _add(path, path, lang, line)

    for m in _RE_FILE_PATH.finditer(content):
        path = m.group(1)
        # Skip if we already have a file:line entry for this path
        ext = _file_ext(path)
        lang = _EXT_LANG.get(ext)
        _add(path, path, lang, None)

    # ------------------------------------------------------------------
    # Pass 3: dotted symbols and PascalCase names
    # ------------------------------------------------------------------
    # Give backtick tokens higher priority by processing them first.
    backtick_tokens: set[str] = set()
    for m in _RE_BACKTICK.finditer(content):
        token = m.group(1).strip()
        backtick_tokens.add(token)
        # Check if the backtick token is itself a file path
        if _RE_FILE_LINE.fullmatch(token):
            fm = _RE_FILE_LINE.fullmatch(token)
            if fm:
                path = fm.group(1)
                line = int(fm.group(2))
                ext = _file_ext(path)
                _add(path, path, _EXT_LANG.get(ext), line)
                continue
        if _RE_FILE_PATH.fullmatch(token):
            ext = _file_ext(token)
            _add(token, token, _EXT_LANG.get(ext), None)
            continue
        if _RE_DOTTED_SYMBOL.fullmatch(token):
            file = _infer_file(token)
            lang = _infer_lang(token, file)
            _add(token, file, lang, None)
            continue
        if _RE_PASCAL.fullmatch(token):
            _add(token, None, None, None)

    # Plain-text dotted symbols not already captured via backticks
    for m in _RE_DOTTED_SYMBOL.finditer(content):
        token = m.group(1)
        if token in backtick_tokens:
            continue
        file = _infer_file(token)
        lang = _infer_lang(token, file)
        _add(token, file, lang, None)

    # PascalCase bare symbols
    for m in _RE_PASCAL.finditer(content):
        token = m.group(1)
        if token in backtick_tokens:
            continue
        _add(token, None, None, None)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _file_ext(path: str) -> str:
    """Return the lowercased file extension including the dot, or empty string."""
    if "." not in path.rsplit("/", 1)[-1]:
        return ""
    return "." + path.rsplit(".", 1)[-1].lower()


def _infer_file(dotted: str) -> Optional[str]:
    """Heuristically infer a relative file path from a dotted symbol name.

    ``core.llm.call_llm`` → ``core/llm.py``
    ``Memory.create``     → None  (single-component module ambiguous)
    """
    parts = dotted.split(".")
    # Only infer when the first part looks like a package (all lower-snake)
    if len(parts) >= 2 and re.match(r"^[a-z_][a-z0-9_]*$", parts[0]):
        # Module path: all but the last component (which is the attribute/method)
        module_parts = parts[:-1]
        return "/".join(module_parts) + ".py"
    return None


def _infer_lang(symbol: str, file: Optional[str]) -> Optional[str]:
    """Return language tag if determinable from file path, else None."""
    if file:
        ext = _file_ext(file)
        return _EXT_LANG.get(ext)
    return None


# ---------------------------------------------------------------------------
# LLM-override merge
# ---------------------------------------------------------------------------

def merge_code_refs(
    regex_refs: list[dict],
    llm_refs: list[dict] | None,
) -> list[dict]:
    """Return the winning code_refs list.

    If *llm_refs* is a non-empty list of dicts each containing at least a
    ``"symbol"`` key, it is returned as-is (LLM wins).  Otherwise the
    regex baseline is returned.

    This is intentionally permissive: any list from the LLM with valid
    shape replaces the regex output.  Callers should validate LLM output
    before passing it here.
    """
    if llm_refs and isinstance(llm_refs, list) and _refs_valid(llm_refs):
        return llm_refs
    return regex_refs


def _refs_valid(refs: list) -> bool:
    """Return True if *refs* is a non-empty list of dicts with a ``symbol`` key."""
    if not refs:
        return False
    return all(isinstance(r, dict) and "symbol" in r for r in refs)
