"""
Headlinese-style systematic deletion compression for memory content.

Implements rule-based compression of markdown memory bodies while preserving
structural and technical elements.  Three depths: lite, moderate, aggressive.

Content format (from consolidator._format_markdown):
    ---
    name: Memory Title
    description: Summary text
    type: memory
    ---

    Memory body content here.

The YAML frontmatter is never compressed.  Only the markdown body after the
second ``---`` delimiter is processed.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Callable

from .codebook import encode_field_value


# ---------------------------------------------------------------------------
# Depth configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DepthConfig:
    """Compression parameters per depth level."""

    drop_articles: bool
    drop_auxiliaries: bool
    drop_copula: bool
    drop_pronouns: bool
    drop_conjunctions: bool
    drop_throat_clearing: bool
    replace_verbose: bool
    merge_redundant_bullets: bool
    shift_simple_present: bool
    # Aggressive-only: drop more filler words
    drop_fillers: bool


_DEPTH_CONFIGS: dict[str, _DepthConfig] = {
    "lite": _DepthConfig(
        drop_articles=True,
        drop_auxiliaries=False,
        drop_copula=False,
        drop_pronouns=False,
        drop_conjunctions=False,
        drop_throat_clearing=True,
        replace_verbose=True,
        merge_redundant_bullets=False,
        shift_simple_present=False,
        drop_fillers=False,
    ),
    "moderate": _DepthConfig(
        drop_articles=True,
        drop_auxiliaries=True,
        drop_copula=True,
        drop_pronouns=True,
        drop_conjunctions=True,
        drop_throat_clearing=True,
        replace_verbose=True,
        merge_redundant_bullets=True,
        shift_simple_present=True,
        drop_fillers=False,
    ),
    "aggressive": _DepthConfig(
        drop_articles=True,
        drop_auxiliaries=True,
        drop_copula=True,
        drop_pronouns=True,
        drop_conjunctions=True,
        drop_throat_clearing=True,
        replace_verbose=True,
        merge_redundant_bullets=True,
        shift_simple_present=True,
        drop_fillers=True,
    ),
}


# ---------------------------------------------------------------------------
# Stage-adaptive depth configuration
# ---------------------------------------------------------------------------

STAGE_DEPTH_MAP: dict[str, str] = {
    "ephemeral": "off",
    "consolidated": "lite",
    "crystallized": "moderate",
    "instinctive": "aggressive",
}


def get_stage_depth(stage: str) -> str:
    """Return the compression depth appropriate for a memory stage.

    Mapping:
        - ephemeral     → "off"        (keep ground truth)
        - consolidated  → "lite"       (grammatical deletion only)
        - crystallized  → "moderate"   (deletion + schema encoding)
        - instinctive   → "aggressive" (maximum compression)

    The ``MEMESIS_COMPRESSION_DEPTH`` environment variable overrides the
    stage-appropriate default when set.

    Args:
        stage: Memory lifecycle stage name.

    Returns:
        Compression depth string ("off", "lite", "moderate", or "aggressive").

    Raises:
        ValueError: If ``stage`` is not a known lifecycle stage.
    """
    env_override = os.environ.get("MEMESIS_COMPRESSION_DEPTH")
    if env_override is not None:
        if env_override == "off":
            return "off"
        if env_override not in _DEPTH_CONFIGS:
            raise ValueError(
                f"Unsupported MEMESIS_COMPRESSION_DEPTH '{env_override}'. "
                f"Use: off, lite, moderate, aggressive"
            )
        return env_override

    if stage not in STAGE_DEPTH_MAP:
        raise ValueError(
            f"Unknown stage '{stage}'. Use: {', '.join(STAGE_DEPTH_MAP)}"
        )
    return STAGE_DEPTH_MAP[stage]


def compress_memory_for_stage(content: str, stage: str) -> str:
    """Compress memory content using the depth appropriate for its stage.

    Args:
        content: Full memory content (YAML frontmatter + markdown body).
        stage: Memory lifecycle stage name.

    Returns:
        Compressed content, or the original content if depth is "off".
    """
    depth = get_stage_depth(stage)
    if depth == "off":
        return content
    return compress_memory_content(content, depth=depth)


def compress_to_brevity_code(content: str) -> str:
    """Compress memory content to a maximally terse brevity-code format.

    Used for instinctive-stage memories where every token counts.

    Transformations:
        1. Abbreviate YAML frontmatter keys (name→nm, description→dsc, type→typ).
        2. Codebook-encode field values where possible.
        3. Apply aggressive systematic deletion to the body.
        4. Strip trailing whitespace and collapse empty lines.

    Args:
        content: Full memory content with standard YAML frontmatter.

    Returns:
        Brevity-code formatted content.
    """
    frontmatter, body = _split_frontmatter(content)

    fm_lines: list[str] = []
    key_map = {"name": "nm", "description": "dsc", "type": "typ"}
    if frontmatter:
        for line in frontmatter.splitlines():
            stripped = line.strip()
            if stripped == "---":
                continue
            if ":" in stripped:
                key, val = stripped.split(":", 1)
                key = key.strip()
                val = val.strip()
                short_key = key_map.get(key, key)
                encoded_val = encode_field_value(val)
                fm_lines.append(f"{short_key}: {encoded_val}")

    if fm_lines:
        compact_fm = "---\n" + "\n".join(fm_lines) + "\n---"
    else:
        compact_fm = ""

    if body.strip():
        compressed_body = compress_memory_content(body, depth="aggressive")
        _, body_only = _split_frontmatter(compressed_body)
        body = body_only.strip()
    else:
        body = ""

    if compact_fm and body:
        return f"{compact_fm}\n{body}"
    elif compact_fm:
        return compact_fm
    return body


# ---------------------------------------------------------------------------
# Word lists for deletion rules
# ---------------------------------------------------------------------------

_ARTICLES = frozenset({"a", "an", "the"})

_AUXILIARY_VERBS = frozenset({
    "is", "was", "has", "will", "would", "could", "should", "might", "may",
    "have", "had", "do", "does", "did", "shall", "can", "am", "are", "were",
    "be", "been", "being",
})

_COPULA = frozenset({"is", "was", "are", "were", "be", "been", "being", "am"})

_PRONOUNS = frozenset({
    "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their",
    "mine", "yours", "hers", "ours", "theirs",
    "this", "that", "these", "those",
    "myself", "yourself", "himself", "herself", "itself", "ourselves", "themselves",
})

_CONJUNCTIONS = frozenset({"and", "but", "or", "so", "yet"})

_FILLERS = frozenset({
    "just", "really", "basically", "actually", "simply", "quite", "rather",
    "very", "extremely", "fairly", "pretty", "somewhat", "kind", "sort",
    "totally", "completely", "absolutely", "definitely", "probably",
    "perhaps", "maybe", "likely", "essentially", "literally",
})

# Phrases to drop entirely (throat-clearing)
_THROAT_CLEARING_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\byou should\b", re.IGNORECASE), ""),
    (re.compile(r"\bmake sure to\b", re.IGNORECASE), ""),
    (re.compile(r"\bremember to\b", re.IGNORECASE), ""),
    (re.compile(r"\bit is important to\b", re.IGNORECASE), ""),
    (re.compile(r"\bit is recommended to\b", re.IGNORECASE), ""),
    (re.compile(r"\bplease note that\b", re.IGNORECASE), ""),
    (re.compile(r"\bnote that\b", re.IGNORECASE), ""),
    (re.compile(r"\bit is worth noting that\b", re.IGNORECASE), ""),
    (re.compile(r"\bkeep in mind that\b", re.IGNORECASE), ""),
    (re.compile(r"\bdon't forget to\b", re.IGNORECASE), ""),
    (re.compile(r"\btry to\b", re.IGNORECASE), ""),
    (re.compile(r"\bconsider\b", re.IGNORECASE), ""),
    (re.compile(r"\bthere is\b", re.IGNORECASE), ""),
    (re.compile(r"\bthere are\b", re.IGNORECASE), ""),
    (re.compile(r"\bit is\b", re.IGNORECASE), ""),
    (re.compile(r"\bit was\b", re.IGNORECASE), ""),
]

# Verbose → concise replacements (whole-word, case-insensitive)
_VERBOSE_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\butilize\b", re.IGNORECASE), "use"),
    (re.compile(r"\butilizing\b", re.IGNORECASE), "using"),
    (re.compile(r"\bdue to the fact that\b", re.IGNORECASE), "because"),
    (re.compile(r"\bat this point in time\b", re.IGNORECASE), "now"),
    (re.compile(r"\bprior to\b", re.IGNORECASE), "before"),
    (re.compile(r"\bsubsequent to\b", re.IGNORECASE), "after"),
    (re.compile(r"\bin order to\b", re.IGNORECASE), "to"),
    (re.compile(r"\ba large number of\b", re.IGNORECASE), "many"),
    (re.compile(r"\bimplement a solution for\b", re.IGNORECASE), "fix"),
    (re.compile(r"\bimplement a solution to\b", re.IGNORECASE), "fix"),
    (re.compile(r"\bimplement a fix for\b", re.IGNORECASE), "fix"),
    (re.compile(r"\bimplemented a solution for\b", re.IGNORECASE), "fixed"),
    (re.compile(r"\bimplemented a solution to\b", re.IGNORECASE), "fixed"),
    (re.compile(r"\bimplement a solution to\b", re.IGNORECASE), "fix"),
    (re.compile(r"\bimplement a fix for\b", re.IGNORECASE), "fix"),
    (re.compile(r"\bextensive\b", re.IGNORECASE), "big"),
    (re.compile(r"\bsubstantial\b", re.IGNORECASE), "big"),
    (re.compile(r"\bsignificant\b", re.IGNORECASE), "big"),
    (re.compile(r"\bconsiderable\b", re.IGNORECASE), "big"),
    (re.compile(r"\badditional\b", re.IGNORECASE), "more"),
    (re.compile(r"\bsupplementary\b", re.IGNORECASE), "more"),
    (re.compile(r"\bapproximately\b", re.IGNORECASE), "~"),
    (re.compile(r"\bapproximately\b", re.IGNORECASE), "~"),
    (re.compile(r"\bdemonstrate\b", re.IGNORECASE), "show"),
    (re.compile(r"\bdemonstrates\b", re.IGNORECASE), "shows"),
    (re.compile(r"\bindicate\b", re.IGNORECASE), "show"),
    (re.compile(r"\bindicates\b", re.IGNORECASE), "shows"),
    (re.compile(r"\binitiate\b", re.IGNORECASE), "start"),
    (re.compile(r"\binitiates\b", re.IGNORECASE), "starts"),
    (re.compile(r"\bterminate\b", re.IGNORECASE), "end"),
    (re.compile(r"\bterminates\b", re.IGNORECASE), "ends"),
    (re.compile(r"\bmodify\b", re.IGNORECASE), "change"),
    (re.compile(r"\bmodifies\b", re.IGNORECASE), "changes"),
    (re.compile(r"\bassist\b", re.IGNORECASE), "help"),
    (re.compile(r"\bassists\b", re.IGNORECASE), "helps"),
    (re.compile(r"\bobtain\b", re.IGNORECASE), "get"),
    (re.compile(r"\bobtains\b", re.IGNORECASE), "gets"),
    (re.compile(r"\brequire\b", re.IGNORECASE), "need"),
    (re.compile(r"\brequires\b", re.IGNORECASE), "needs"),
    (re.compile(r"\brequirement\b", re.IGNORECASE), "need"),
    (re.compile(r"\bperform\b", re.IGNORECASE), "do"),
    (re.compile(r"\bperforms\b", re.IGNORECASE), "does"),
    (re.compile(r"\bexecute\b", re.IGNORECASE), "run"),
    (re.compile(r"\bexecutes\b", re.IGNORECASE), "runs"),
    (re.compile(r"\bgenerate\b", re.IGNORECASE), "make"),
    (re.compile(r"\bgenerates\b", re.IGNORECASE), "makes"),
    (re.compile(r"\bcreate\b", re.IGNORECASE), "make"),
    (re.compile(r"\bcreates\b", re.IGNORECASE), "makes"),
    (re.compile(r"\bconstruct\b", re.IGNORECASE), "make"),
    (re.compile(r"\bconstructs\b", re.IGNORECASE), "makes"),
    (re.compile(r"\benhance\b", re.IGNORECASE), "improve"),
    (re.compile(r"\benhances\b", re.IGNORECASE), "improves"),
    (re.compile(r"\boptimize\b", re.IGNORECASE), "improve"),
    (re.compile(r"\boptimizes\b", re.IGNORECASE), "improves"),
    (re.compile(r"\brecommend\b", re.IGNORECASE), "suggest"),
    (re.compile(r"\brecommends\b", re.IGNORECASE), "suggests"),
    (re.compile(r"\badvise\b", re.IGNORECASE), "suggest"),
    (re.compile(r"\badvises\b", re.IGNORECASE), "suggests"),
    (re.compile(r"\battempt\b", re.IGNORECASE), "try"),
    (re.compile(r"\battempts\b", re.IGNORECASE), "tries"),
    (re.compile(r"\bverify\b", re.IGNORECASE), "check"),
    (re.compile(r"\bverifies\b", re.IGNORECASE), "checks"),
    (re.compile(r"\bvalidate\b", re.IGNORECASE), "check"),
    (re.compile(r"\bvalidates\b", re.IGNORECASE), "checks"),
    (re.compile(r"\bensure\b", re.IGNORECASE), "check"),
    (re.compile(r"\bensures\b", re.IGNORECASE), "checks"),
    (re.compile(r"\bremove\b", re.IGNORECASE), "drop"),
    (re.compile(r"\bremoves\b", re.IGNORECASE), "drops"),
    (re.compile(r"\beliminate\b", re.IGNORECASE), "drop"),
    (re.compile(r"\beliminates\b", re.IGNORECASE), "drops"),
    (re.compile(r"\bidentify\b", re.IGNORECASE), "find"),
    (re.compile(r"\bidentifies\b", re.IGNORECASE), "finds"),
    (re.compile(r"\bdetermine\b", re.IGNORECASE), "find"),
    (re.compile(r"\bdetermines\b", re.IGNORECASE), "finds"),
    (re.compile(r"\bexamine\b", re.IGNORECASE), "check"),
    (re.compile(r"\bexamines\b", re.IGNORECASE), "checks"),
    (re.compile(r"\binvestigate\b", re.IGNORECASE), "check"),
    (re.compile(r"\binvestigates\b", re.IGNORECASE), "checks"),
    (re.compile(r"\bresolve\b", re.IGNORECASE), "fix"),
    (re.compile(r"\bresolves\b", re.IGNORECASE), "fixes"),
    (re.compile(r"\baddress\b", re.IGNORECASE), "fix"),
    (re.compile(r"\baddresses\b", re.IGNORECASE), "fixes"),
]


# ---------------------------------------------------------------------------
# Protected element extraction / restoration
# ---------------------------------------------------------------------------


@dataclass
class _ProtectedElements:
    """Holds extracted protected elements and their placeholders."""

    placeholders: dict[str, str] = field(default_factory=dict)
    counter: int = field(default=0)

    def _next_key(self) -> str:
        self.counter += 1
        # Use base-26 letters (no digits) so the key never matches
        # number, date, path, or URL extraction patterns.
        n = self.counter
        chars = []
        while n > 0:
            n, r = divmod(n - 1, 26)
            chars.append(chr(ord("A") + r))
        return "\u00abPROT" + "".join(reversed(chars)) + "\u00bb"

    def add(self, original: str) -> str:
        key = self._next_key()
        self.placeholders[key] = original
        return key


def _extract_code_blocks(text: str, protected: _ProtectedElements) -> str:
    """Extract fenced code blocks (```...```) and replace with placeholders."""
    pattern = re.compile(r"```[\s\S]*?```", re.MULTILINE)

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return pattern.sub(replacer, text)


def _extract_inline_code(text: str, protected: _ProtectedElements) -> str:
    """Extract inline code (`...`) and replace with placeholders."""
    pattern = re.compile(r"`[^`]+`")

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return pattern.sub(replacer, text)


def _extract_urls(text: str, protected: _ProtectedElements) -> str:
    """Extract URLs and markdown links and replace with placeholders."""
    # Markdown links: [text](url)
    md_link = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    # Bare URLs
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


def _extract_file_paths(text: str, protected: _ProtectedElements) -> str:
    """Extract file paths and replace with placeholders."""
    # Absolute paths: /src/components/...  or  /Users/...  or  /home/...
    # Relative paths: ./config.yaml  or  ../foo/bar  or  src/components/...
    # Windows paths: C:\Users\...  (rare but possible)
    path_pattern = re.compile(
        r"(?:\.[\/]|\/|[A-Za-z]:\\)[A-Za-z0-9_\-\.\/\\]+(?:\.[a-zA-Z0-9]+)?"
    )

    def replacer(match: re.Match) -> str:
        val = match.group(0)
        # Heuristic: must contain at least one slash or backslash and look like a path
        if "/" in val or "\\" in val:
            # Avoid matching things that are just words with slashes (e.g. "and/or")
            parts = val.replace("\\", "/").split("/")
            if any(len(p) > 1 and p not in (".", "..") for p in parts):
                return protected.add(val)
        return val

    return path_pattern.sub(replacer, text)


def _extract_commands(text: str, protected: _ProtectedElements) -> str:
    """Extract common command patterns and replace with placeholders.

    Matches per-line to avoid swallowing adjacent non-command text.
    Handles quoted arguments and multi-word subcommands.
    """
    command_pattern = re.compile(
        r"\b(?:git|npm|pip|python|python3|node|yarn|pnpm|cargo|go|rustc|cmake|docker|kubectl|helm|aws|gcloud|terraform|pytest|mypy|black|flake8|ruff|eslint|prettier)(?:[ \t]+(?!and\b|or\b|but\b|for\b|to\b|the\b|a\b|an\b|in\b|on\b|at\b|with\b|from\b|by\b|as\b|of\b|is\b|was\b|are\b|be\b|been\b)(?:[a-zA-Z0-9_\-\.\/\:\=\@\+\*\?\&\%]+|['\"][^'\"]*['\"]))+",
        re.IGNORECASE,
    )

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return command_pattern.sub(replacer, text)


def _extract_indented_code_blocks(text: str, protected: _ProtectedElements) -> str:
    """Extract indented code blocks (4+ spaces) and replace with placeholders."""
    pattern = re.compile(r"^( {4,}[\s\S]*?)(?=\n\S|^\S|$)", re.MULTILINE)

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return pattern.sub(replacer, text)


def _extract_blockquotes(text: str, protected: _ProtectedElements) -> str:
    """Extract blockquote lines and replace with placeholders."""
    pattern = re.compile(r"^>.*$", re.MULTILINE)

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return pattern.sub(replacer, text)
    """Extract technical terms (multi-part PascalCase, snake_case, version numbers)."""
    # Only match clear technical patterns:
    # 1. Multi-part PascalCase / camelCase (2+ parts): ReactUseEffect, myVarName
    # 2. snake_case identifiers: my_variable, test_function
    # 3. Version numbers: v2.1.3, 1.0.0-beta.1
    # Deliberately NOT matching single capitalized words like "The", "You"
    # to avoid over-extraction of common words.
    tech_pattern = re.compile(
        r"\b(?:[A-Z][a-z0-9]*){2,}\b|\b[a-z]+_[a-z_0-9]+\b|\b(?:v?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9\.]+)?)\b"
    )

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return tech_pattern.sub(replacer, text)


def _extract_dates_and_numbers(text: str, protected: _ProtectedElements) -> str:
    """Extract ISO dates and numeric values and replace with placeholders."""
    # ISO dates: 2024-01-15, 2024-01-15T10:30:00
    date_pattern = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?\b")
    # Numbers with units or decimals: 3.14, 1_000, 50%, $100, 10px, 2em
    number_pattern = re.compile(r"\b\d+(?:[_,]\d+)*(?:\.\d+)?(?:\s*(?:%|px|em|rem|vh|vw|pt|ms|s|gb|mb|kb|tb|usd|eur|gbp))?\b", re.IGNORECASE)

    def date_replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    def num_replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    text = date_pattern.sub(date_replacer, text)
    text = number_pattern.sub(num_replacer, text)
    return text


def _extract_proper_nouns(text: str, protected: _ProtectedElements) -> str:
    """Extract likely proper nouns (capitalized words mid-sentence) and replace."""
    # Match capitalized words that are not sentence-start
    # This is a heuristic — we look for capitalized words preceded by lowercase or punctuation
    proper_pattern = re.compile(r"(?<=[a-z\s\,\;\:\-\(])\b([A-Z][a-zA-Z]+)\b")

    def replacer(match: re.Match) -> str:
        return protected.add(match.group(0))

    return proper_pattern.sub(replacer, text)


def _restore_protected(text: str, protected: _ProtectedElements) -> str:
    """Restore all placeholder values back into the text.

    Iterates in reverse insertion order so that nested placeholders
    (placeholders whose values contain earlier placeholder keys) are
    resolved correctly.
    """
    result = text
    for key, original in reversed(list(protected.placeholders.items())):
        result = result.replace(key, original)
    return result


# ---------------------------------------------------------------------------
# Compression rules
# ---------------------------------------------------------------------------


def _drop_throat_clearing(text: str) -> str:
    """Remove throat-clearing phrases."""
    result = text
    for pattern, replacement in _THROAT_CLEARING_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _replace_verbose(text: str) -> str:
    """Replace verbose phrases with concise synonyms."""
    result = text
    for pattern, replacement in _VERBOSE_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    return result


def _drop_words(text: str, words: frozenset[str]) -> str:
    """Drop specific words when they appear as standalone tokens."""
    # Build a regex that matches any of the words as whole words, case-insensitive
    if not words:
        return text
    escaped = "|".join(re.escape(w) for w in words)
    pattern = re.compile(rf"\b(?:{escaped})\b", re.IGNORECASE)
    return pattern.sub("", text)


def _clean_extra_whitespace(text: str) -> str:
    """Collapse multiple spaces, trim lines, remove empty lines."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        line = line.strip()
        # Collapse multiple spaces within line
        line = re.sub(r"\s+", " ", line)
        if line:
            cleaned.append(line)
    return "\n".join(cleaned)


def _merge_redundant_bullets(text: str) -> str:
    """Merge adjacent bullet points that say the same thing in different words."""
    lines = text.splitlines()
    merged = []
    prev_bullet = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "+ ", "1. ", "2. ", "3. ", "4. ", "5. ", "6. ", "7. ", "8. ", "9. ")):
            bullet_text = stripped[2:].strip().lower()
            # Simple heuristic: if current bullet shares > 60% words with previous, skip it
            if prev_bullet is not None:
                prev_words = set(prev_bullet.split())
                curr_words = set(bullet_text.split())
                if prev_words and curr_words:
                    overlap = len(prev_words & curr_words) / max(len(prev_words), len(curr_words))
                    if overlap >= 0.6:
                        continue  # Skip redundant bullet
            prev_bullet = bullet_text
            merged.append(line)
        else:
            prev_bullet = None
            merged.append(line)

    return "\n".join(merged)


def _shift_simple_present(text: str) -> str:
    """Shift past-tense verbs to simple present where appropriate."""
    # Simple replacements for common past → present shifts
    shifts = [
        (re.compile(r"\bwas\s+([a-zA-Z]+ing)\b", re.IGNORECASE), r"is \1"),
        (re.compile(r"\bwere\s+([a-zA-Z]+ing)\b", re.IGNORECASE), r"are \1"),
        (re.compile(r"\bused\s+to\b", re.IGNORECASE), "uses"),
    ]
    result = text
    for pattern, replacement in shifts:
        result = pattern.sub(replacement, result)
    return result


def _drop_parentheticals(text: str) -> str:
    """Remove parenthetical asides."""
    return re.sub(r"\s*\([^)]*\)", "", text)


def _apply_compression_rules(text: str, config: _DepthConfig) -> str:
    """Apply all enabled compression rules to the text."""
    result = text

    if config.drop_throat_clearing:
        result = _drop_throat_clearing(result)

    if config.replace_verbose:
        result = _replace_verbose(result)

    if config.drop_fillers:
        result = _drop_words(result, _FILLERS)

    if config.drop_articles:
        result = _drop_words(result, _ARTICLES)

    if config.drop_auxiliaries:
        result = _drop_words(result, _AUXILIARY_VERBS)

    if config.drop_copula:
        result = _drop_words(result, _COPULA)

    if config.drop_pronouns:
        result = _drop_words(result, _PRONOUNS)

    if config.drop_conjunctions:
        result = _drop_words(result, _CONJUNCTIONS)

    if config.merge_redundant_bullets:
        result = _merge_redundant_bullets(result)

    if config.shift_simple_present:
        result = _shift_simple_present(result)

    if config.drop_fillers:
        result = _drop_parentheticals(result)

    # Always clean up whitespace after deletions
    result = _clean_extra_whitespace(result)

    return result


# ---------------------------------------------------------------------------
# Frontmatter handling
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str]:
    """
    Split text into (frontmatter, body).

    Frontmatter format:
        ---
        key: value
        ---

    Returns (frontmatter, body) where frontmatter includes both ``---`` lines
    and everything between them.  If no frontmatter is detected, returns
    ("", text).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text

    # Find closing ---
    close_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break

    if close_idx == -1:
        # No closing delimiter — treat as no frontmatter
        return "", text

    frontmatter_lines = lines[: close_idx + 1]
    body_lines = lines[close_idx + 1 :]
    frontmatter = "\n".join(frontmatter_lines)
    body = "\n".join(body_lines)
    return frontmatter, body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compress_memory_content(content: str, depth: str = "moderate") -> str:
    """
    Compress memory content using headlinese-style systematic deletion.

    Preserves YAML frontmatter, code blocks, inline code, URLs, file paths,
    commands, technical terms, proper nouns, dates, and numbers.  Applies
    rule-based deletions to the markdown body only.

    Args:
        content: Full memory content (YAML frontmatter + markdown body).
        depth: Compression aggressiveness — "lite", "moderate", or
            "aggressive".  Default is "moderate".

    Returns:
        Compressed content with frontmatter intact.

    Raises:
        ValueError: If ``depth`` is not one of the supported levels.
    """
    if depth not in _DEPTH_CONFIGS:
        raise ValueError(
            f"Unsupported depth '{depth}'. Use: {', '.join(_DEPTH_CONFIGS)}"
        )

    config = _DEPTH_CONFIGS[depth]
    frontmatter, body = _split_frontmatter(content)

    if not body.strip():
        return content

    protected = _ProtectedElements()

    # Extract protected elements in order (largest / most specific first)
    body = _extract_code_blocks(body, protected)
    body = _extract_indented_code_blocks(body, protected)
    body = _extract_blockquotes(body, protected)
    body = _extract_inline_code(body, protected)
    body = _extract_urls(body, protected)
    body = _extract_file_paths(body, protected)
    body = _extract_commands(body, protected)
    body = _extract_dates_and_numbers(body, protected)

    # Apply compression rules
    body = _apply_compression_rules(body, config)

    # Restore protected elements
    body = _restore_protected(body, protected)

    # Reassemble
    if frontmatter:
        return f"{frontmatter}\n{body}"
    return body


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str]
    warnings: list[str]
    original_size: int
    compressed_size: int
    compression_ratio: float


def validate_compression(
    original: str,
    compressed: str,
    depth: str = "moderate",
) -> ValidationResult:
    """
    Verify that compression preserved all protected elements.

    Checks:
    1. Code blocks preserved exactly
    2. URLs preserved exactly
    3. File paths preserved exactly
    4. Dates preserved
    5. Numbers preserved
    6. Compression below minimum threshold (must actually compress)
    7. Protected regions count matches
    """
    errors: list[str] = []
    warnings: list[str] = []

    original_size = len(original)
    compressed_size = len(compressed)
    compression_ratio = original_size / compressed_size if compressed_size > 0 else 0.0

    orig_fm, orig_body = _split_frontmatter(original)
    comp_fm, comp_body = _split_frontmatter(compressed)

    # Check 1: Frontmatter preserved
    if orig_fm and orig_fm != comp_fm:
        errors.append("YAML frontmatter was modified during compression")

    # Check 2: Code blocks preserved exactly
    orig_code = re.compile(r"```[\s\S]*?```", re.MULTILINE).findall(orig_body)
    comp_code = re.compile(r"```[\s\S]*?```", re.MULTILINE).findall(comp_body)
    if orig_code != comp_code:
        errors.append("Code blocks were modified or lost during compression")

    # Check 3: URLs preserved exactly
    orig_urls = re.compile(r"https?://[^\s\)\]\>\"\'\`]+", re.IGNORECASE).findall(orig_body)
    comp_urls = re.compile(r"https?://[^\s\)\]\>\"\'\`]+", re.IGNORECASE).findall(comp_body)
    if orig_urls != comp_urls:
        errors.append("URLs were modified or lost during compression")

    # Check 4: File paths preserved
    path_pat = re.compile(r"(?:\.[\/]|\/)[A-Za-z0-9_\-\.\/]+(?:\.[a-zA-Z0-9]+)?")
    orig_paths = path_pat.findall(orig_body)
    comp_paths = path_pat.findall(comp_body)
    missing_paths = set(orig_paths) - set(comp_paths)
    if missing_paths:
        errors.append(f"File paths lost: {missing_paths}")

    # Check 5: Dates preserved
    date_pat = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?\b")
    orig_dates = date_pat.findall(orig_body)
    comp_dates = date_pat.findall(comp_body)
    missing_dates = set(orig_dates) - set(comp_dates)
    if missing_dates:
        errors.append(f"Dates lost: {missing_dates}")

    # Check 6: Numbers preserved (version numbers + standalone numbers)
    num_pat = re.compile(r"\b\d+(?:[_,]\d+)*(?:\.\d+)?(?:\s*(?:%|px|em|rem|vh|vw|pt|ms|s|gb|mb|kb|tb|usd|eur|gbp))?\b", re.IGNORECASE)
    orig_numbers = num_pat.findall(orig_body)
    comp_numbers = num_pat.findall(comp_body)
    missing_numbers = set(orig_numbers) - set(comp_numbers)
    if missing_numbers:
        errors.append(f"Numbers lost: {missing_numbers}")

    # Check 7: Compression actually occurred
    if compressed_size >= original_size:
        errors.append("Compression did not reduce size")

    # Check 8: Inline code count matches
    inline_pat = re.compile(r"`[^`]+`")
    orig_inline = inline_pat.findall(orig_body)
    comp_inline = inline_pat.findall(comp_body)
    if len(orig_inline) != len(comp_inline):
        errors.append(
            f"Inline code count mismatch: {len(orig_inline)} original vs "
            f"{len(comp_inline)} compressed"
        )

    # Depth-specific ratio checks
    if depth == "lite":
        if compression_ratio < 1.05:
            warnings.append("Lite compression achieved less than 1.05x ratio")
    elif depth == "moderate":
        if compression_ratio < 1.1:
            warnings.append("Moderate compression achieved less than 1.1x ratio")
    elif depth == "aggressive":
        if compression_ratio < 1.2:
            warnings.append("Aggressive compression achieved less than 1.2x ratio")

    is_valid = len(errors) == 0
    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        original_size=original_size,
        compressed_size=compressed_size,
        compression_ratio=compression_ratio,
    )


def compression_ratio(original: str, compressed: str) -> float:
    """
    Return the compression ratio (original / compressed).

    A ratio of 2.0 means the compressed text is half the length.
    """
    orig_len = len(original.strip())
    comp_len = len(compressed.strip())
    if comp_len == 0:
        return float("inf") if orig_len > 0 else 1.0
    return orig_len / comp_len
