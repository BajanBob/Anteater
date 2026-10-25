"""Traffic radar and 24 GHz sensors, from termite SDR observations.

Input: ``kind: sdr`` with ``freq_mhz`` and ``extra.snr_db``. Bands come from
``config/vendors/radar.yaml`` (``bands:`` table). Bands above about 6 GHz need
a downconverter in front of the SDR; termite reports the corrected frequency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anteater.detections.base import SDR, Context, Detector, Finding, Observation
from anteater.scoring.severity import Severity

MIN_SNR_DB = 10.0


@dataclass(frozen=True, slots=True)
class RadarBand:
    name: str
    lo_mhz: float
    hi_mhz: float
    severity: Severity
    note: str = ""


DEFAULT_BANDS = (
    RadarBand("X band traffic radar", 10500.0, 10550.0, Severity.MEDIUM),
    RadarBand(
        "K band (traffic radar or 24 GHz motion/door sensor)", 24050.0, 24250.0, Severity.LOW
    ),
    RadarBand("Ka band traffic radar", 33400.0, 36000.0, Severity.MEDIUM),
)


def bands_from_table(table: Any) -> tuple[RadarBand, ...]:
    if not table:
        return DEFAULT_BANDS
    return tuple(
        RadarBand(
            str(b["name"]),
            float(b["lo_mhz"]),
            float(b["hi_mhz"]),
            Severity.parse(b.get("severity", "low")),
            str(b.get("note", "")),
        )
        for b in table
    )


class Radar(Detector):
    name = "radar"
    description = "X/K/Ka-band traffic radar, 24 GHz door sensors"
    kinds = (SDR,)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.bands = bands_from_table(ctx.fingerprints.tables.get("radar.bands"))
        self.hits: dict[str, int] = {}
        self.level: dict[str, Severity] = {}

    def process(self, obs: Observation) -> list[Finding]:
        if obs.freq_mhz is None:
            return []
        snr = float(obs.extra.get("snr_db", 0.0))
        if snr < MIN_SNR_DB:
            return []
        for band in self.bands:
            if band.lo_mhz <= obs.freq_mhz <= band.hi_mhz:
                n = self.hits[band.name] = self.hits.get(band.name, 0) + 1
                # persistent across sweeps -> one level up
                sev = Severity(min(int(Severity.HIGH), int(band.severity) + (1 if n >= 3 else 0)))
                if n > 1 and sev <= self.level.get(band.name, Severity.INFO):
                    return []  # already reported at this level
                self.level[band.name] = sev
                return [
                    self.finding(
                        obs,
                        sev,
                        band.name,
                        0.4 + 0.1 * int(sev),
                        freq_mhz=round(obs.freq_mhz, 3),
                        snr_db=round(snr, 1),
                        sweeps_seen=n,
                        note=band.note,
                    )
                ]
        return []
