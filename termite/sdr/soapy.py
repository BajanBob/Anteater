"""Thin SoapySDR wrapper (receive only), plus a simulated device for tests and demos."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import numpy as np

from anteater.sdr_optional import require_soapy

log = logging.getLogger(__name__)


class Receiver(Protocol):
    sample_rate: float

    def tune(self, freq_hz: float) -> None: ...
    def read(self, n: int) -> np.ndarray: ...
    def freq_range(self) -> tuple[float, float]: ...
    def close(self) -> None: ...


def list_devices() -> list[dict[str, str]]:
    soapy = require_soapy()
    return [dict(d) for d in soapy.Device.enumerate()]


class SoapyReceiver:
    """One RX channel. Never configures or opens a TX stream."""

    def __init__(self, args: str = "", sample_rate: float = 10e6, gain_db: float = 30.0, channel: int = 0) -> None:
        soapy = require_soapy()
        self._soapy = soapy
        self.dev: Any = soapy.Device(args)
        self.channel = channel
        self.dev.setSampleRate(soapy.SOAPY_SDR_RX, channel, sample_rate)
        self.sample_rate = float(self.dev.getSampleRate(soapy.SOAPY_SDR_RX, channel))
        try:
            self.dev.setGainMode(soapy.SOAPY_SDR_RX, channel, False)
        except Exception:
            pass
        self.dev.setGain(soapy.SOAPY_SDR_RX, channel, gain_db)
        self.stream = self.dev.setupStream(soapy.SOAPY_SDR_RX, soapy.SOAPY_SDR_CF32, [channel])
        self.dev.activateStream(self.stream)
        self._buf = np.empty(1 << 16, dtype=np.complex64)

    def freq_range(self) -> tuple[float, float]:
        ranges = self.dev.getFrequencyRange(self._soapy.SOAPY_SDR_RX, self.channel)
        return min(r.minimum() for r in ranges), max(r.maximum() for r in ranges)

    def tune(self, freq_hz: float) -> None:
        self.dev.setFrequency(self._soapy.SOAPY_SDR_RX, self.channel, freq_hz)

    def read(self, n: int) -> np.ndarray:
        out = np.empty(n, dtype=np.complex64)
        got = 0
        # discard the first buffer after a retune: it still holds the old frequency
        self.dev.readStream(self.stream, [self._buf], len(self._buf), timeoutUs=200_000)
        while got < n:
            want = min(len(self._buf), n - got)
            sr = self.dev.readStream(self.stream, [self._buf], want, timeoutUs=500_000)
            if sr.ret <= 0:
                log.debug("readStream returned %s", sr.ret)
                continue
            out[got : got + sr.ret] = self._buf[: sr.ret]
            got += sr.ret
        return out

    def close(self) -> None:
        try:
            self.dev.deactivateStream(self.stream)
            self.dev.closeStream(self.stream)
        except Exception:
            pass


class SimulatedReceiver:
    """Noise plus fixed carriers. ``carriers`` maps frequency (Hz) to SNR (dB)."""

    def __init__(self, sample_rate: float = 10e6, carriers: dict[float, float] | None = None,
                 freq_range: tuple[float, float] = (1e6, 40e9), seed: int = 0) -> None:
        self.sample_rate = sample_rate
        self.carriers = carriers or {}
        self._range = freq_range
        self._centre = 0.0
        self._rng = np.random.default_rng(seed)

    def freq_range(self) -> tuple[float, float]:
        return self._range

    def tune(self, freq_hz: float) -> None:
        self._centre = freq_hz

    def read(self, n: int) -> np.ndarray:
        noise = (self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n)) / np.sqrt(2)
        t = np.arange(n) / self.sample_rate
        sig = noise.astype(np.complex64)
        for f, snr_db in self.carriers.items():
            offset = f - self._centre
            if abs(offset) < self.sample_rate / 2 * 0.9:
                amp = 10 ** (snr_db / 20)
                sig += (amp * np.exp(2j * np.pi * offset * t)).astype(np.complex64)
        return sig

    def close(self) -> None:
        return None


@contextmanager
def open_receiver(args: str = "", sample_rate: float = 10e6, gain_db: float = 30.0,
                  simulate: bool = False) -> Iterator[Receiver]:
    rx: Receiver
    if simulate:
        rx = SimulatedReceiver(sample_rate, carriers={433.92e6: 25.0, 915.0e6: 18.0, 10525e6: 20.0})
    else:
        rx = SoapyReceiver(args, sample_rate, gain_db)
    try:
        yield rx
    finally:
        rx.close()
