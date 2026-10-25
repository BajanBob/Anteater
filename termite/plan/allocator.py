"""Band plans and the greedy dwell allocator.

A sweep has a time budget. Each band needs ``ceil(span / usable_bw)`` tuning
steps, and every step needs at least ``min_dwell_s`` to see bursty emitters.
Spare time goes to higher-priority bands first, in proportion to priority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from anteater.paths import data_dir

USABLE_FRACTION = 0.8  # edges of the passband roll off; overlap steps


@dataclass(frozen=True, slots=True)
class Band:
    name: str
    lo_mhz: float
    hi_mhz: float
    priority: float = 1.0
    why: str = ""

    @property
    def span_hz(self) -> float:
        return (self.hi_mhz - self.lo_mhz) * 1e6

    def steps(self, sample_rate: float) -> list[float]:
        """Centre frequencies (Hz) covering the band."""
        usable = sample_rate * USABLE_FRACTION
        n = max(1, math.ceil(self.span_hz / usable))
        lo = self.lo_mhz * 1e6
        if n == 1:
            return [lo + self.span_hz / 2]
        width = self.span_hz / n
        return [lo + width * (i + 0.5) for i in range(n)]


@dataclass(slots=True)
class Dwell:
    band: Band
    centres_hz: list[float]
    seconds_per_step: float

    @property
    def seconds(self) -> float:
        return self.seconds_per_step * len(self.centres_hz)


@dataclass(slots=True)
class Plan:
    dwells: list[Dwell] = field(default_factory=list)
    skipped: list[tuple[Band, str]] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(d.seconds for d in self.dwells)


def load_bands(region: int, path: Path | None = None) -> list[Band]:
    file = path or data_dir("spectrum") / f"itu_r{region}.yaml"
    doc = yaml.safe_load(Path(file).read_text(encoding="utf-8")) or {}
    return [
        Band(
            str(b["name"]),
            float(b["lo_mhz"]),
            float(b["hi_mhz"]),
            float(b.get("priority", 1.0)),
            str(b.get("why", "")),
        )
        for b in doc.get("bands", [])
    ]


def allocate(
    bands: list[Band],
    budget_s: float,
    sample_rate: float,
    min_dwell_s: float = 0.05,
    freq_range_hz: tuple[float, float] = (0.0, float("inf")),
    downconverter_lo_hz: float = 0.0,
) -> Plan:
    plan = Plan()
    lo_lim, hi_lim = freq_range_hz
    candidates: list[tuple[Band, list[float]]] = []
    for b in bands:
        centres = b.steps(sample_rate)
        tuned = [
            c - downconverter_lo_hz if downconverter_lo_hz and c > hi_lim else c for c in centres
        ]
        if any(not lo_lim <= t <= hi_lim for t in tuned):
            plan.skipped.append((b, "outside the receiver's range (needs a downconverter)"))
            continue
        candidates.append((b, centres))

    # mandatory minimum, highest priority first, until the budget runs out
    candidates.sort(key=lambda bc: (-bc[0].priority, bc[0].lo_mhz))
    chosen: list[tuple[Band, list[float]]] = []
    remaining = budget_s
    for b, centres in candidates:
        need = min_dwell_s * len(centres)
        if need <= remaining:
            chosen.append((b, centres))
            remaining -= need
        else:
            plan.skipped.append(
                (b, f"needs {need:.1f} s minimum, only {remaining:.1f} s left in budget")
            )

    # share the rest by priority weight, per step
    weight = sum(b.priority * len(c) for b, c in chosen) or 1.0
    for b, centres in chosen:
        extra = remaining * (b.priority * len(centres)) / weight / len(centres)
        plan.dwells.append(Dwell(b, centres, min_dwell_s + extra))
    plan.dwells.sort(key=lambda d: d.band.lo_mhz)
    return plan
