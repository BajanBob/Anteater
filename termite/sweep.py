"""One sweep over a dwell plan, producing anteater observations (as dicts).

Kept out of ``termite.cli`` so it can be tested with ``SimulatedReceiver``.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import numpy as np

from termite.plan.allocator import Plan
from termite.sdr.features import Carrier, find_carriers, lo_offset_hz, psd_db

NFFT = 1024


class Tunable(Protocol):
    sample_rate: float

    def tune(self, freq_hz: float) -> None: ...

    def read(self, n: int) -> np.ndarray: ...

    def freq_range(self) -> tuple[float, float]: ...


def sweep_once(
    rx: Tunable,
    plan: Plan,
    sweep: int,
    min_snr_db: float = 10.0,
    downconverter_lo_hz: float = 0.0,
    lat: float | None = None,
    lon: float | None = None,
) -> list[dict[str, Any]]:
    """Visit every step in ``plan`` once and return the carriers found, one per emitter.

    Neighbouring steps overlap, so one carrier can be seen twice, each time a
    few kHz off (frequency is only resolved to one FFT bin). Detections are
    therefore merged per band afterwards, keeping the strongest, rather than
    assigning each carrier to a step, which drops carriers on a step boundary.
    Carriers outside the band (which would get the wrong label) are dropped.
    """
    lo_off = lo_offset_hz(rx.sample_rate, sweep, NFFT)
    bin_hz = rx.sample_rate / NFFT
    top = rx.freq_range()[1]
    out: list[dict[str, Any]] = []
    for d in plan.dwells:
        lo_hz, hi_hz = d.band.lo_mhz * 1e6 - bin_hz, d.band.hi_mhz * 1e6 + bin_hz
        seen: list[tuple[float, Carrier]] = []
        for centre in d.centres_hz:
            rf = centre + lo_off
            # above the receiver's range, a downconverter shifts the signal down by its LO
            rx.tune(rf - downconverter_lo_hz if downconverter_lo_hz and centre > top else rf)
            samples = int(max(4096, min(rx.sample_rate * d.seconds_per_step, 4e6)))
            freqs, power = psd_db(rx.read(samples), rx.sample_rate, NFFT)
            seen += [
                (rf + c.offset_hz, c)
                for c in find_carriers(freqs, power, min_snr_db)
                if lo_hz <= rf + c.offset_hz <= hi_hz
            ]
        for f_hz, c in _merge(seen, bin_hz):
            f_mhz = f_hz / 1e6
            out.append(
                {
                    "kind": "sdr",
                    "subject": f"sdr:{f_mhz:.1f}",
                    "ts": time.time(),
                    "freq_mhz": f_mhz,
                    "lat": lat,
                    "lon": lon,
                    "extra": {
                        "snr_db": round(c.snr_db, 1),
                        "bandwidth_khz": round(c.bandwidth_hz / 1e3, 1),
                        "band": d.band.name,
                    },
                }
            )
    return out


def _merge(seen: list[tuple[float, Carrier]], bin_hz: float) -> list[tuple[float, Carrier]]:
    """Collapse detections of the same emitter: within 2 bins, or inside each other's bandwidth."""
    groups: list[list[tuple[float, Carrier]]] = []
    for f_hz, c in sorted(seen, key=lambda x: x[0]):
        if groups:
            pf, pc = groups[-1][-1]
            if f_hz - pf <= max(2 * bin_hz, (pc.bandwidth_hz + c.bandwidth_hz) / 2):
                groups[-1].append((f_hz, c))
                continue
        groups.append([(f_hz, c)])
    return [max(g, key=lambda x: x[1].snr_db) for g in groups]
