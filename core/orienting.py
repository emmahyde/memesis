"""
OrientingDetector — rule-based high-signal moment detection.

Identifies moments that biological memory systems flag via the orienting response:
user corrections, explicit emphasis, error spikes, and pacing breaks.

No LLM calls — pure regex and arithmetic. The detector is stateless; callers
are responsible for tracking message history.

Usage:
    from core.orienting import OrientingDetector

    detector = OrientingDetector()
    result = detector.detect(text, message_lengths=[200, 180, 220, 15])
    if result.has_signals:
        # boost importance of this observation
        importance += result.importance_boost
"""

import re
from dataclasses import dataclass, field

# RISK-11: experimental flag scaffold.
# orienting is production-validated (rule-based correction/emphasis detection confirmed stable across sessions).
# Opt-in override: include "orienting" in MEMESIS_EXPERIMENTAL_MODULES env var to force-exclude from scoring.
experimental: bool = False


@dataclass
class OrientingSignal:
    """A single detected orienting signal."""

    signal_type: str        # "correction", "emphasis", "error_spike", "pacing_break"
    confidence: float       # 0.0 to 1.0
    matched_text: str       # the text fragment (or description) that triggered
    importance_boost: float # how much to boost importance for this signal


@dataclass
class OrientingResult:
    """Aggregated result from OrientingDetector.detect()."""

    signals: list[OrientingSignal] = field(default_factory=list)
    importance_boost: float = 0.0  # max boost across all signals (not sum)

    @property
    def has_signals(self) -> bool:
        """True if any orienting signals were detected."""
        return len(self.signals) > 0


class OrientingDetector:
    """
    Detects high-signal orienting moments in text.

    Four signal categories:
    - correction: user correcting Claude ("no, that's wrong", "actually", "I said")
    - emphasis: user stressing something important ("remember this", "always", "never")
    - error_spike: multiple error indicators in a short window (3+ triggers spike)
    - pacing_break: message significantly shorter than recent average

    Design principles:
    - Stateless: detect() takes text + optional message_lengths, maintains no state
    - Case insensitive: all pattern matching uses re.IGNORECASE
    - Word boundaries: \\b prevents false positives ("factually" != "actually")
    - Max not sum for importance_boost: correction+emphasis gives 0.3, not 0.5
    - Break after first match per category: no double-counting
    - No LLM calls anywhere
    """

    # Compiled regex patterns for correction language
    CORRECTION_PATTERNS = [
        r"\bno[,.]?\s+that'?s?\s+(wrong|not|incorrect)",  # "no, that's wrong"
        r"\bactually[,\s]",                                  # "actually," or "actually "
        r"\bnot that[,\s]",                                  # "not that,"
        r"\bi said\b",                                        # "I said"
        r"\bthat'?s\s+not\s+(right|correct|what)\b",        # "that's not right"
        r"\bwrong[,.\s]",                                    # "wrong," "wrong."
        r"\bincorrect\b",                                    # "incorrect"
    ]

    # Compiled regex patterns for emphasis language
    EMPHASIS_PATTERNS = [
        r"\bremember\s+this\b",   # "remember this"
        r"\balways\s+\w+",         # "always use", "always run"
        r"\bnever\s+\w+",          # "never use", "never skip"
        r"\bimportant\b",          # "important"
        r"\bcritical\b",           # "critical"
        r"\bdon'?t\s+forget\b",   # "don't forget"
        r"\bmake\s+sure\b",        # "make sure"
    ]

    # Patterns that indicate an error/failure in text
    ERROR_INDICATORS = [
        r"(?i)(error:)",
        r"(?i)(traceback\s*\(most recent)",
        r"(?i)(failed:)",
        r"(?i)(exception:)",
        r"(?i)(ModuleNotFoundError|ImportError|TypeError|ValueError|KeyError|AttributeError|NameError|RuntimeError|AssertionError)",
    ]

    # Minimum number of error indicators to trigger an error_spike signal
    ERROR_SPIKE_THRESHOLD = 3

    # Importance boost per signal type
    IMPORTANCE_BOOSTS = {
        "correction": 0.3,
        "emphasis": 0.2,
        "error_spike": 0.2,
        "pacing_break": 0.1,
    }

    # Current message must be below this fraction of recent average to trigger
    PACING_BREAK_RATIO = 0.4  # current < 40% of average → pacing break

    def detect(
        self,
        text: str | None,
        message_lengths: list[int] | None = None,
    ) -> OrientingResult:
        """
        Detect orienting signals in the given text.

        Args:
            text: The message text to analyze. None or empty returns no signals.
            message_lengths: Optional list of recent message lengths (chars), including
                the current message as the last element. Used for pacing break detection.

        Returns:
            OrientingResult with signals list and overall importance_boost.
            Returns empty result if the orienting_detector feature flag is disabled.
        """
        from .flags import get_flag

        if not get_flag("orienting_detector"):
            return OrientingResult()

        if not text:
            return OrientingResult()

        signals: list[OrientingSignal] = []

        # --- Correction signal ---
        for pattern in self.CORRECTION_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                signals.append(OrientingSignal(
                    signal_type="correction",
                    confidence=0.8,
                    matched_text=match.group(0),
                    importance_boost=self.IMPORTANCE_BOOSTS["correction"],
                ))
                break  # one correction signal per text — no double-counting

        # --- Emphasis signal ---
        for pattern in self.EMPHASIS_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                signals.append(OrientingSignal(
                    signal_type="emphasis",
                    confidence=0.7,
                    matched_text=match.group(0),
                    importance_boost=self.IMPORTANCE_BOOSTS["emphasis"],
                ))
                break  # one emphasis signal per text

        # --- Error spike signal ---
        error_count = sum(
            1 for pattern in self.ERROR_INDICATORS
            if re.search(pattern, text)
        )
        if error_count >= self.ERROR_SPIKE_THRESHOLD:
            # confidence scales with count: 3 → 0.6, 5 → 1.0
            confidence = min(1.0, error_count / 5.0)
            signals.append(OrientingSignal(
                signal_type="error_spike",
                confidence=confidence,
                matched_text=f"{error_count} error indicators",
                importance_boost=self.IMPORTANCE_BOOSTS["error_spike"],
            ))

        # --- Pacing break signal ---
        if message_lengths and len(message_lengths) >= 2:
            recent = message_lengths[:-1]
            current = message_lengths[-1]
            avg = sum(recent) / len(recent)
            if avg > 0 and current < avg * self.PACING_BREAK_RATIO:
                signals.append(OrientingSignal(
                    signal_type="pacing_break",
                    confidence=0.6,
                    matched_text=f"message length {current} vs avg {avg:.0f}",
                    importance_boost=self.IMPORTANCE_BOOSTS["pacing_break"],
                ))

        # Overall importance boost is the max across signals, not the sum.
        # This keeps importance within a reasonable range even when multiple
        # signals fire simultaneously.
        importance_boost = max((s.importance_boost for s in signals), default=0.0)

        return OrientingResult(signals=signals, importance_boost=importance_boost)
