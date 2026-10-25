"""Spectrum features from IQ samples: PSD, noise floor, carriers, and a fixed-length vector."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def psd_db(iq: np.ndarray, sample_rate: float, nfft: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """Welch PSD (Hann, 50% overlap), DC-centred. Returns (offset frequencies Hz, power dB)."""
    iq = np.asarray(iq, dtype=np.complex64)
    if len(iq) < nfft:
        iq = np.pad(iq, (0, nfft - len(iq)))
    win = np.hanning(nfft).astype(np.float32)
    step = nfft // 2
    n_seg = 1 + (len(iq) - nfft) // step
    acc = np.zeros(nfft, dtype=np.float64)
    for i in range(n_seg):
        seg = iq[i * step : i * step + nfft] * win
        acc += np.abs(np.fft.fft(seg)) ** 2
    acc /= n_seg * float(np.sum(win**2))
    power = np.fft.fftshift(10 * np.log10(acc + 1e-20))
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / sample_rate))
    # the DC spike of zero-IF receivers is an artefact, not a signal
    mid = nfft // 2
    power[mid - 1 : mid + 2] = np.median(power)
    return freqs, power


def lo_offset_hz(sample_rate: float, sweep: int, nfft: int = 1024) -> float:
    """Offset tuning: how far to move the LO away from a step's centre frequency.

    ``psd_db`` blanks the bins around DC, so a carrier exactly at the tuned
    frequency is invisible. The planner centres single-step bands on their
    midpoint, which is often the very frequency devices use (433.92 MHz). Tuning
    4 bins off-centre moves that blind spot away, and flipping the sign every
    sweep means no frequency is blind two sweeps running. 4 bins is far inside
    the guard band the planner leaves (it only uses 80% of the sample rate).
    """
    return 4 * sample_rate / nfft * (1 if sweep % 2 else -1)


def noise_floor_db(power: np.ndarray) -> float:
    return float(np.median(power))


@dataclass(frozen=True, slots=True)
class Carrier:
    offset_hz: float
    power_db: float
    snr_db: float
    bandwidth_hz: float


def find_carriers(freqs: np.ndarray, power: np.ndarray, min_snr_db: float = 10.0, max_carriers: int = 8) -> list[Carrier]:
    """Contiguous runs above floor + min_snr, strongest first, with -3 dB-ish width."""
    floor = noise_floor_db(power)
    above = power > floor + min_snr_db
    out: list[Carrier] = []
    bin_hz = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0
    i, n = 0, len(power)
    while i < n:
        if not above[i]:
            i += 1
            continue
        j = i
        while j < n and above[j]:
            j += 1
        k = i + int(np.argmax(power[i:j]))
        out.append(Carrier(float(freqs[k]), float(power[k]), float(power[k] - floor), (j - i) * bin_hz))
        i = j
    out.sort(key=lambda c: c.snr_db, reverse=True)
    return out[:max_carriers]


def spectral_flatness(power_db: np.ndarray) -> float:
    """Geometric / arithmetic mean of linear power. 1 = white noise, ~0 = pure tone."""
    lin = 10 ** (power_db / 10)
    return float(np.exp(np.mean(np.log(lin + 1e-20))) / (np.mean(lin) + 1e-20))


FEATURE_NAMES = (
    "snr_db", "bandwidth_khz", "flatness", "crest_db", "kurtosis",
    "papr_db", "amp_std", "phase_diff_std", "n_carriers", "occupancy",
)


def feature_vector(iq: np.ndarray, sample_rate: float, nfft: int = 1024) -> np.ndarray:
    """10 features describing an emitter, in FEATURE_NAMES order. Used by the RFF classifier."""
    iq = np.asarray(iq, dtype=np.complex64)
    freqs, power = psd_db(iq, sample_rate, nfft)
    floor = noise_floor_db(power)
    carriers = find_carriers(freqs, power)
    top = carriers[0] if carriers else Carrier(0.0, floor, 0.0, 0.0)
    amp = np.abs(iq)
    mean_amp = float(np.mean(amp)) + 1e-12
    centred = amp - mean_amp
    var = float(np.mean(centred**2)) + 1e-12
    kurt = float(np.mean(centred**4) / var**2)
    papr = 10 * np.log10(float(np.max(amp**2)) / (float(np.mean(amp**2)) + 1e-12) + 1e-12)
    dphi = np.diff(np.unwrap(np.angle(iq))) if len(iq) > 1 else np.zeros(1)
    return np.array(
        [
            top.snr_db,
            top.bandwidth_hz / 1e3,
            spectral_flatness(power),
            float(np.max(power) - np.mean(power)),
            kurt,
            papr,
            float(np.std(amp) / mean_amp),
            float(np.std(dphi)),
            float(len(carriers)),
            float(np.mean(power > floor + 6.0)),
        ],
        dtype=np.float64,
    )
