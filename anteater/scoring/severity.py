"""Severity levels and how scores map onto them."""

from __future__ import annotations

from enum import IntEnum


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, value: str | int | Severity) -> Severity:
        if isinstance(value, Severity):
            return value
        if isinstance(value, int):
            return cls(max(0, min(4, value)))
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown severity {value!r}") from None

    @classmethod
    def from_score(cls, score: float) -> Severity:
        """Map a 0..1 confidence score to a level."""
        if score >= 0.9:
            return cls.CRITICAL
        if score >= 0.7:
            return cls.HIGH
        if score >= 0.45:
            return cls.MEDIUM
        if score >= 0.2:
            return cls.LOW
        return cls.INFO

    @property
    def label(self) -> str:
        return self.name.lower()


def escalate(base: Severity, repeats: int, cap: Severity = Severity.HIGH) -> Severity:
    """Raise a level by one for every third repeat of the same finding, up to ``cap``.

    Never lowers a level, and never raises past ``cap`` (unless ``base`` already is).
    """
    if repeats < 3 or base >= cap:
        return base
    return Severity(min(int(cap), int(base) + repeats // 3))


def combine(scores: list[float]) -> float:
    """Combine independent evidence: 1 - prod(1 - s). Order-independent, stays in [0, 1]."""
    out = 1.0
    for s in scores:
        out *= 1.0 - max(0.0, min(1.0, s))
    return 1.0 - out
