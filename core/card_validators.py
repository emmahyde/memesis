"""
Validators for issue cards produced by Stage 1.5 synthesis.

Separated from core/validators.py to avoid scope creep — these predicates
operate on card dicts, not raw observations, and have distinct failure modes.
"""

from __future__ import annotations

import re

# First-person / second-person pronouns signal the quote captures direct speech
# or personal instruction, which is load-bearing evidence (not a paraphrase).
_PRONOUN_RE = re.compile(
    r"\b(I|me|my|we|us|our|you|your)\b", re.IGNORECASE
)

# Imperative patterns at sentence start: "don't use X", "prefer Y", etc.
# Must appear at the very start of the stripped quote (case-insensitive).
_IMPERATIVE_RE = re.compile(
    r"^(don't|do not|stop|use|prefer|always|never|must|should|don) \w+",
    re.IGNORECASE,
)

# Tokenizer: split on anything that is not alphanumeric.
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumeric, discard empty strings."""
    return {t for t in _TOKEN_SPLIT_RE.split(text.lower()) if t}


def _has_specific_technical_token(quote: str, card_body_tokens: set[str]) -> bool:
    """Return True if the quote contains at least one word-like chunk absent from
    the card body that looks technical.

    Technical = contains a digit, '/', '.', or ':' in the *raw* chunk.
    We split on whitespace (not all non-alphanumeric) to preserve the special
    chars, then check both absence-from-body (via its lowercased alphanum tokens)
    and the presence of a technical marker in the raw chunk.
    """
    # Body token set is alphanum-lowercased (used for absence check)
    body_tokens = card_body_tokens  # already a set[str] of alphanum-lower tokens

    for raw_chunk in quote.split():
        chunk_lower = raw_chunk.lower()
        # Absence check: all alphanum subtokens of this chunk must be absent from body
        chunk_alphanum_tokens = _tokenize(chunk_lower)
        if not chunk_alphanum_tokens:
            continue
        if chunk_alphanum_tokens <= body_tokens:
            # All subtokens already in body — not new information
            continue
        # At least one subtoken is new; now check if this chunk is technical.
        # '.' and ':' count only when flanked by alphanumeric chars (dotted
        # identifiers / version numbers / namespaces) — not trailing punctuation.
        stripped = raw_chunk.strip(".,;!?'\"-")
        if re.search(r"[a-z0-9][.:][a-z0-9]", stripped, re.IGNORECASE):
            return True
        if "/" in stripped:
            return True
        if any(ch.isdigit() for ch in raw_chunk):
            return True

    # Also catch bare path separators: if '/' appears and any segment is novel
    if "/" in quote:
        for segment in quote.split("/"):
            seg_lower = segment.strip().lower()
            seg_tokens = _tokenize(seg_lower)
            if seg_tokens and not (seg_tokens <= body_tokens):
                return True

    return False


def _card_evidence_indices_valid(card: dict, window_count: int) -> bool:
    """Return True if at least one evidence_obs_indices entry is within [0, window_count).

    Used by synthesize_issue_cards() to demote cards whose indices were ALL stripped as
    out-of-range (hallucinated indices like [0, 3, 999] when only 5 windows exist).
    """
    indices = card.get("evidence_obs_indices") or []
    if not indices:
        return False  # no indices at all = invalid
    for idx in indices:
        if isinstance(idx, int) and 0 <= idx < window_count:
            return True
    return False


def _card_evidence_load_bearing(card: dict) -> bool:
    """Return True if the card's single evidence_quote is non-circular.

    A quote is considered load-bearing if it satisfies at least one of:
      a. Contains a first/second-person pronoun (direct speech signal).
      b. Starts with an imperative verb pattern (instruction/directive).
      c. Contains a specific technical token (digit, path sep, or dot/colon)
         that does not appear in the card's problem + decision_or_outcome + kind.

    Called only for cards with exactly 1 evidence_quote.
    """
    quotes = card.get("evidence_quotes") or []
    if len(quotes) == 0:
        # No quotes — cannot be load-bearing.
        return False
    if len(quotes) > 1:
        # Multi-quote cards are not circular by construction.
        return True

    quote = quotes[0]
    if not quote or not isinstance(quote, str):
        return False

    # Check (a): pronoun presence
    if _PRONOUN_RE.search(quote):
        return True

    # Check (b): imperative at start
    if _IMPERATIVE_RE.match(quote.strip()):
        return True

    # Check (c): specific technical token absent from card body
    body = " ".join(filter(None, [
        card.get("problem", ""),
        card.get("decision_or_outcome", ""),
        card.get("kind", ""),
    ]))
    card_body_tokens = _tokenize(body)
    if _has_specific_technical_token(quote, card_body_tokens):
        return True

    return False
