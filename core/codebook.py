"""
Codebook-style vocabulary compression for memory content.

Maps predictable, high-frequency developer terms to short tokens via a shared
vocabulary.  Inspired by telegraph codebooks / brevity codes: the encoder and
decoder share the same mapping, so long-form values can be replaced with short
tokens and restored later.

The vocabulary is bounded and hand-curated — only terms that appear frequently
in developer-memory content are included.  Proper nouns (project names, person
names) are deliberately excluded.

Integration points:
- core/retrieval.py  :: prepend codebook summary to MEMORY CONTEXT block
- core/crystallizer.py :: encode known values during crystallization
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


LANG_SHORT: dict[str, str] = {
    "python": "py",
    "typescript": "ts",
    "javascript": "js",
    "rust": "rs",
    "go": "go",
    "ruby": "rb",
    "java": "java",
    "c++": "cpp",
    "c#": "cs",
    "swift": "swift",
    "kotlin": "kt",
    "sql": "sql",
    "bash": "sh",
    "r": "r",
}

TOOL_SHORT: dict[str, str] = {
    "docker": "docker",
    "git": "git",
    "npm": "npm",
    "yarn": "yarn",
    "pnpm": "pnpm",
    "pip": "pip",
    "cargo": "cargo",
    "make": "make",
    "cmake": "cmake",
    "terraform": "tf",
    "kubernetes": "k8s",
    "ansible": "ansible",
    "vim": "vim",
    "vscode": "vscode",
    "jetbrains": "jb",
    "pytest": "pytest",
    "jest": "jest",
    "vitest": "vitest",
    "eslint": "eslint",
    "prettier": "prettier",
}

FRAMEWORK_SHORT: dict[str, str] = {
    "react": "react",
    "vue": "vue",
    "angular": "ng",
    "django": "django",
    "flask": "flask",
    "fastapi": "fastapi",
    "express": "express",
    "next.js": "nextjs",
    "svelte": "svelte",
    "tailwind": "tailwind",
    "prisma": "prisma",
    "sqlalchemy": "sqla",
    "postgresql": "pg",
    "mysql": "mysql",
    "sqlite": "sqlite",
    "mongodb": "mongo",
    "redis": "redis",
    "rabbitmq": "rabbit",
}

FILE_SHORT: dict[str, str] = {
    "requirements.txt": "requires",
    "package.json": "pkg.json",
    "dockerfile": "Dockerfile",
    "docker-compose.yml": "dc.yml",
    "makefile": "Makefile",
    ".env": ".env",
    ".gitignore": ".gitignore",
    "tsconfig.json": "tsconfig",
    "pyproject.toml": "pyproject",
}

ERROR_SHORT: dict[str, str] = {
    "import error": "import_err",
    "type error": "type_err",
    "syntax error": "syntax_err",
    "runtime error": "runtime_err",
    "segmentation fault": "segfault",
    "out of memory": "oom",
    "timeout": "timeout",
    "connection refused": "conn_refused",
    "permission denied": "perm_denied",
    "file not found": "file404",
}

PREF_SHORT: dict[str, str] = {
    "spacing": "spc",
    "indentation": "indent",
    "naming convention": "naming",
    "error handling": "errhand",
    "testing": "test",
    "logging": "log",
    "documentation": "docs",
    "code organization": "codeorg",
}

_VOCABULARY_CATEGORIES: list[tuple[str, dict[str, str]]] = [
    ("error", ERROR_SHORT),
    ("pref", PREF_SHORT),
    ("file", FILE_SHORT),
    ("framework", FRAMEWORK_SHORT),
    ("tool", TOOL_SHORT),
    ("lang", LANG_SHORT),
]

_ENCODE_MAP: dict[str, str] = {}
for _cat_name, _cat_map in _VOCABULARY_CATEGORIES:
    _ENCODE_MAP.update(_cat_map)

_DECODE_MAP: dict[str, str] = {v: k for k, v in _ENCODE_MAP.items()}
_SORTED_ENCODE_KEYS: list[str] = sorted(_ENCODE_MAP.keys(), key=len, reverse=True)


@dataclass
class _ProtectedElements:
    placeholders: dict[str, str] = field(default_factory=dict)
    counter: int = field(default=0)

    def _next_key(self) -> str:
        self.counter += 1
        n = self.counter
        chars = []
        while n > 0:
            n, r = divmod(n - 1, 26)
            chars.append(chr(ord("A") + r))
        return "\u00abCB" + "".join(reversed(chars)) + "\u00bb"

    def add(self, original: str) -> str:
        key = self._next_key()
        self.placeholders[key] = original
        return key


def _extract_code_blocks(text: str, protected: _ProtectedElements) -> str:
    pattern = re.compile(r"```[\s\S]*?```", re.MULTILINE)

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return pattern.sub(replacer, text)


def _extract_inline_code(text: str, protected: _ProtectedElements) -> str:
    pattern = re.compile(r"`[^`]+`")

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return pattern.sub(replacer, text)


def _extract_urls(text: str, protected: _ProtectedElements) -> str:
    md_link = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    bare_url = re.compile(
        r"https?://[^\s\)\]\>\"\'\`]+",
        re.IGNORECASE,
    )

    def md_replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    def url_replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    text = md_link.sub(md_replacer, text)
    text = bare_url.sub(url_replacer, text)
    return text


def _restore_protected(text: str, protected: _ProtectedElements) -> str:
    result = text
    for key, original in reversed(list(protected.placeholders.items())):
        result = result.replace(key, original)
    return result


def _build_encode_pattern() -> re.Pattern:
    escaped = "|".join(re.escape(k) for k in _SORTED_ENCODE_KEYS)
    return re.compile(
        rf"(?<![a-zA-Z0-9])(?:{escaped})(?![a-zA-Z0-9])",
        re.IGNORECASE,
    )


_ENCODE_PATTERN: re.Pattern = _build_encode_pattern()


def encode_field_value(text: str, field_name: str = "content") -> str:
    """Replace known long-form values with short tokens in text.

    Uses vocabulary matching — replaces whole-word matches to avoid partial
    replacements inside other words.  Only applies to content, not code blocks
    or URLs.

    Args:
        text: The text to encode.
        field_name: Name of the field being encoded (for future per-field
            vocabularies; currently unused).

    Returns:
        Text with known values replaced by short tokens.
    """
    if not text or not text.strip():
        return text

    protected = _ProtectedElements()

    text = _extract_code_blocks(text, protected)
    text = _extract_inline_code(text, protected)
    text = _extract_urls(text, protected)

    def _replacer(match: re.Match) -> str:
        original = match.group(0)
        lower = original.lower()
        short = _ENCODE_MAP.get(lower)
        if short is None:
            return original
        if original.isupper():
            return short.upper()
        if original[0].isupper():
            return short.capitalize()
        return short

    text = _ENCODE_PATTERN.sub(_replacer, text)
    text = _restore_protected(text, protected)

    return text


def decode_field_value(text: str) -> str:
    """Expand short tokens back to long-form values.

    Primarily for LLM to decode in context — the codebook summary included in
    the MEMORY CONTEXT block gives the LLM the mapping it needs.

    Args:
        text: Text containing short tokens.

    Returns:
        Text with short tokens expanded to long-form values.
    """
    if not text or not text.strip():
        return text

    sorted_shorts = sorted(_DECODE_MAP.keys(), key=len, reverse=True)
    escaped = "|".join(re.escape(s) for s in sorted_shorts)
    pattern = re.compile(
        rf"(?<![a-zA-Z0-9])(?:{escaped})(?![a-zA-Z0-9])",
        re.IGNORECASE,
    )

    def _replacer(match: re.Match) -> str:
        original = match.group(0)
        lower = original.lower()
        long = _DECODE_MAP.get(lower)
        if long is None:
            return original
        if original.isupper():
            return long.upper()
        if original[0].isupper():
            return long.capitalize()
        return long

    return pattern.sub(_replacer, text)


def get_codebook_summary() -> str:
    """Return a compact codebook definition for inclusion in MEMORY CONTEXT.

    Format::

        CODEBOOK: lang=py:python,ts:typescript; tool=tf:terraform,k8s:kubernetes; ...

    Categories are abbreviated to keep the summary under the 500-char budget.
    """
    parts: list[str] = []

    def _fmt(cat_code: str, mapping: dict[str, str]) -> str:
        items = [f"{v}:{k}" for k, v in mapping.items() if v != k]
        if not items:
            return ""
        return f"{cat_code}={','.join(items)}"

    for cat_name, cat_map in _VOCABULARY_CATEGORIES:
        formatted = _fmt(cat_name, cat_map)
        if formatted:
            parts.append(formatted)

    summary = "CODEBOOK: " + "; ".join(parts)
    if len(summary) > 480:
        summary = summary[:477] + "..."
    return summary


def get_codebook_token_overhead() -> int:
    """Estimate token cost of including codebook in context.

    Uses a rough heuristic of ~4 chars per token.  The actual overhead depends
    on the tokenizer, but this gives a conservative upper bound.
    """
    summary = get_codebook_summary()
    return len(summary) // 4 + 1


def is_codebook_enabled() -> bool:
    """Return True if codebook compression is enabled via environment variable.

    Controlled by ``MEMESIS_CODEBOOK_ENABLED``.  Default is ``True``.
    Disabled when set to ``"0"`` or ``"false"`` (case-insensitive).
    """
    val = os.environ.get("MEMESIS_CODEBOOK_ENABLED", "1")
    return val.lower() not in ("0", "false", "no", "off")


def contains_codebook_tokens(text: str) -> bool:
    """Return True if the text contains any encoded short tokens.

    Used by retrieval to decide whether to prepend the codebook summary.
    """
    if not text:
        return False
    sorted_shorts = sorted(_DECODE_MAP.keys(), key=len, reverse=True)
    escaped = "|".join(re.escape(s) for s in sorted_shorts)
    pattern = re.compile(
        rf"(?<![a-zA-Z0-9])(?:{escaped})(?![a-zA-Z0-9])",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))
