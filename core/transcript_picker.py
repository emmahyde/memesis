"""
core/transcript_picker.py — pick a "good" transcript to eval against.

Combines deterministic prefilter (recency, length, friction density, decision
density, already-traced) with LLM analysis of user-message content
(evalability score + rationale + themes). Used by `evolve --pick`.

LLM calls go through `core.llm_cache.cached_call_llm` so re-running the
picker is cheap.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from core.llm_cache import cached_call_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PROJECTS_BASE = Path.home() / ".claude" / "projects"
_DEFAULT_TRACES_DIR = Path.home() / ".claude" / "memesis" / "traces"

_MIN_LINES = 20
_MAX_AGE_DAYS = 30
_LLM_TOP_K = 15
_USER_MSG_EXCERPT_CHARS = 8000
_DET_WEIGHT = 0.4
_LLM_WEIGHT = 0.6

# Reuses the regex fallback from core.somatic (kept inline so the picker is
# self-contained and avoids importing nltk/VADER at module load time).
_FRICTION_RE = re.compile(
    r"\b(frustrat|wrong|broken|fail(ed|ure|ing|s)?|error|bug"
    r"|crash(ed|es|ing)?|stuck|ugh|cancel\s+(that|this|it)"
    r"|start\s+over|not\s+(that|this|right|correct))\b",
    re.IGNORECASE,
)

# "let's" / "lets" reads as a decision/commitment marker per project domain
# experience — added alongside the more conventional decision verbs.
_DECISION_RE = re.compile(
    r"\b(let'?s|decide(d)?|going\s+with|instead\s+of|switch(ed|ing)?\s+to"
    r"|plan\s+is|we'?ll\s+(do|use|try)|gonna|will\s+(use|try|do)"
    r"|chose|chosen|pick(ed)?|use\s+(this|that)\s+approach)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    path: Path
    line_count: int
    age_days: float
    friction_density: float
    decision_density: float
    already_traced: bool

    det_score: float = 0.0
    llm_score: float = 0.0
    combined_score: float = 0.0

    rationale: str = ""
    themes: list[str] = field(default_factory=list)
    expected_capture_density: str = "unknown"  # low|med|high

    def breakdown(self) -> str:
        traced = " [traced]" if self.already_traced else ""
        return (
            f"det={self.det_score:.2f} "
            f"llm={self.llm_score:.2f} "
            f"combined={self.combined_score:.2f} "
            f"({self.line_count} lines, {self.age_days:.1f}d old, "
            f"friction={self.friction_density:.3f}, "
            f"decision={self.decision_density:.3f}){traced}"
        )


# ---------------------------------------------------------------------------
# Discovery + prefilter
# ---------------------------------------------------------------------------

def discover(base_dir: Path | None = None) -> list[Path]:
    """Return all `.jsonl` transcripts under `base_dir/<project>/*.jsonl`."""
    base = base_dir if base_dir is not None else _DEFAULT_PROJECTS_BASE
    if not base.is_dir():
        return []
    return sorted(base.glob("*/*.jsonl"))


def prefilter(
    paths: Iterable[Path],
    min_lines: int = _MIN_LINES,
    max_age_days: int = _MAX_AGE_DAYS,
) -> list[Path]:
    """Apply cheap filesystem-level cuts: recency + minimum line count."""
    now = time.time()
    cutoff_seconds = max_age_days * 86400.0
    keep: list[Path] = []
    for p in paths:
        try:
            stat = p.stat()
        except OSError:
            continue
        if (now - stat.st_mtime) > cutoff_seconds:
            continue
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                count = sum(1 for _ in fh)
        except OSError:
            continue
        if count < min_lines:
            continue
        keep.append(p)
    return keep


# ---------------------------------------------------------------------------
# User-message extraction
# ---------------------------------------------------------------------------

def extract_user_messages(path: Path, max_chars: int = _USER_MSG_EXCERPT_CHARS) -> str:
    """Concatenate user-message content from a Claude Code .jsonl transcript.

    The transcript schema varies — fields commonly present include `type`,
    `role`, and a `message.content` array. Extract conservatively.
    """
    chunks: list[str] = []
    total = 0
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return ""
    with fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not _is_user_record(obj):
                continue

            text = _extract_text(obj)
            if not text:
                continue

            chunks.append(text)
            total += len(text)
            if total >= max_chars:
                break

    joined = "\n".join(chunks)
    if len(joined) > max_chars:
        joined = joined[:max_chars]
    return joined


def _is_user_record(obj: dict) -> bool:
    if obj.get("type") == "user":
        return True
    msg = obj.get("message") or {}
    return isinstance(msg, dict) and msg.get("role") == "user"


def _extract_text(obj: dict) -> str:
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
    if isinstance(obj.get("content"), str):
        return obj["content"]
    return ""


# ---------------------------------------------------------------------------
# Deterministic scoring
# ---------------------------------------------------------------------------

def _density(text: str, pattern: re.Pattern[str]) -> float:
    if not text:
        return 0.0
    matches = len(pattern.findall(text))
    # Normalize by 1k chars — keeps the score scale independent of length.
    return matches / max(1, len(text) / 1000.0)


def _already_traced(orig_session_id: str, traces_dir: Path = _DEFAULT_TRACES_DIR) -> bool:
    if not traces_dir.is_dir():
        return False
    # Match any trace whose name contains this session id
    return any(traces_dir.glob(f"*{orig_session_id}*.jsonl"))


def deterministic_score(path: Path, traces_dir: Path | None = None) -> Candidate:
    """Score a single transcript on cheap signals."""
    stat = path.stat()
    age_days = (time.time() - stat.st_mtime) / 86400.0

    # Read full file once — for line count + friction/decision density.
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)

    user_text = extract_user_messages(path)
    friction_density = _density(user_text, _FRICTION_RE)
    decision_density = _density(user_text, _DECISION_RE)

    orig = re.sub(r"[^A-Za-z0-9_\-]", "-", path.stem).lstrip("-.")
    if not orig:
        orig = "session"
    traced = _already_traced(orig, traces_dir if traces_dir is not None else _DEFAULT_TRACES_DIR)

    # Compose deterministic score in [0, 1].
    # Recency: 1.0 fresh → ~0 at 30 days (linear cut at MAX_AGE_DAYS).
    recency = max(0.0, 1.0 - (age_days / _MAX_AGE_DAYS))
    # Length: saturates at 200 lines — beyond that we don't reward more.
    length = min(1.0, line_count / 200.0)
    # Friction / decision: normalized densities, capped.
    friction = min(1.0, friction_density / 5.0)
    decision = min(1.0, decision_density / 5.0)
    traced_penalty = -0.3 if traced else 0.0

    det = max(
        0.0,
        min(
            1.0,
            (0.30 * recency)
            + (0.20 * length)
            + (0.25 * friction)
            + (0.25 * decision)
            + traced_penalty,
        ),
    )

    return Candidate(
        path=path,
        line_count=line_count,
        age_days=age_days,
        friction_density=friction_density,
        decision_density=decision_density,
        already_traced=traced,
        det_score=det,
    )


# ---------------------------------------------------------------------------
# LLM scoring (analyzes user messages)
# ---------------------------------------------------------------------------

_LLM_PROMPT = """You are evaluating a Claude Code session transcript for whether \
it makes a GOOD eval target for a memory-pipeline reverse-engineering tool.

A GOOD target has:
- Concrete decisions (architectural, naming, implementation choices)
- User friction signals (frustration, retries, scope changes, "let's try X instead")
- Distinct memorable events the user would later expect to be captured
- Technical depth (specific files, errors, mechanisms)

A POOR target has:
- Mostly small talk, status pings, or repetitive "continue"
- No discernible decisions or friction
- Generic Q&A with no memorable specifics

Here are user messages from the session (truncated):

```
{excerpt}
```

Respond with ONLY a JSON object, no prose:
{{
  "score": 0.0..1.0,
  "rationale": "1-2 sentence justification",
  "themes": ["short", "topic", "labels"],
  "expected_capture_density": "low" | "med" | "high"
}}
"""


def llm_score(excerpt: str, force_live: bool = False) -> dict:
    """LLM evaluation of a user-message excerpt. Returns parsed JSON dict."""
    if not excerpt.strip():
        return {
            "score": 0.0,
            "rationale": "empty excerpt",
            "themes": [],
            "expected_capture_density": "low",
        }
    prompt = _LLM_PROMPT.format(excerpt=excerpt)
    raw = cached_call_llm(prompt, force_live=force_live, max_tokens=512)
    return _parse_llm_response(raw)


def _parse_llm_response(raw: str) -> dict:
    """Tolerant JSON parse — strips fences, defaults on failure."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip a single fenced block
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("transcript_picker: LLM JSON parse failed: %s", exc)
        return {
            "score": 0.0,
            "rationale": f"LLM response unparseable: {exc}",
            "themes": [],
            "expected_capture_density": "low",
        }
    score = obj.get("score", 0.0)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "rationale": str(obj.get("rationale", ""))[:500],
        "themes": [str(t) for t in obj.get("themes", []) if t][:8],
        "expected_capture_density": obj.get("expected_capture_density", "unknown"),
    }


# ---------------------------------------------------------------------------
# Combined ranking
# ---------------------------------------------------------------------------

def rank(
    paths: Iterable[Path],
    *,
    top_k: int = _LLM_TOP_K,
    force_live: bool = False,
    traces_dir: Path | None = None,
) -> list[Candidate]:
    """Score + rank candidates. LLM is only invoked on the top_k by det score."""
    paths_list = list(paths)
    if not paths_list:
        return []

    # Deterministic scoring on every prefiltered candidate
    scored = [deterministic_score(p, traces_dir=traces_dir) for p in paths_list]
    scored.sort(key=lambda c: c.det_score, reverse=True)

    head = scored[:top_k]
    tail = scored[top_k:]

    # LLM-score the top_k only
    for cand in head:
        excerpt = extract_user_messages(cand.path)
        try:
            result = llm_score(excerpt, force_live=force_live)
        except Exception as exc:
            logger.warning("transcript_picker: LLM scoring failed for %s: %s", cand.path.name, exc)
            result = {"score": 0.0, "rationale": f"llm error: {exc}", "themes": [], "expected_capture_density": "unknown"}
        cand.llm_score = float(result["score"])
        cand.rationale = result["rationale"]
        cand.themes = result["themes"]
        cand.expected_capture_density = result["expected_capture_density"]
        cand.combined_score = (_DET_WEIGHT * cand.det_score) + (_LLM_WEIGHT * cand.llm_score)

    # Tail keeps det-only score (no LLM call)
    for cand in tail:
        cand.combined_score = _DET_WEIGHT * cand.det_score

    out = head + tail
    out.sort(key=lambda c: c.combined_score, reverse=True)
    return out


def pick(
    base_dir: Path | None = None,
    *,
    top_k: int = _LLM_TOP_K,
    min_lines: int = _MIN_LINES,
    max_age_days: int = _MAX_AGE_DAYS,
    force_live: bool = False,
    traces_dir: Path | None = None,
) -> list[Candidate]:
    """End-to-end pipeline: discover → prefilter → rank."""
    found = discover(base_dir)
    filtered = prefilter(found, min_lines=min_lines, max_age_days=max_age_days)
    return rank(filtered, top_k=top_k, force_live=force_live, traces_dir=traces_dir)
