"""Audio alerts: a distinct motif per severity, plus optional text-to-speech.

Motifs are synthesised with numpy and played through ``sounddevice`` (the
``alerts`` extra). Without it the terminal bell is used. TTS uses the first
of espeak-ng, espeak or macOS ``say`` found on PATH.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time

import numpy as np

from anteater.detections.base import Finding
from anteater.scoring.severity import Severity

log = logging.getLogger(__name__)
RATE = 22_050

# (frequency Hz, duration s); 0 Hz is a rest. Rising and faster with severity.
MOTIFS: dict[Severity, list[tuple[float, float]]] = {
    Severity.INFO: [(660, 0.08)],
    Severity.LOW: [(660, 0.12), (0, 0.05), (880, 0.12)],
    Severity.MEDIUM: [(523, 0.12), (659, 0.12), (784, 0.18)],
    Severity.HIGH: [(880, 0.1), (0, 0.04), (880, 0.1), (0, 0.04), (1175, 0.22)],
    Severity.CRITICAL: [(1175.0, 0.08), (880.0, 0.08)] * 4 + [(1568.0, 0.3)],
}


def synth(motif: list[tuple[float, float]], rate: int = RATE, volume: float = 0.4) -> np.ndarray:
    """Render a motif to float32 mono samples with short fades to avoid clicks."""
    parts: list[np.ndarray] = []
    for freq, dur in motif:
        n = max(1, int(rate * dur))
        if freq <= 0:
            parts.append(np.zeros(n, dtype=np.float32))
            continue
        t = np.arange(n, dtype=np.float32) / rate
        tone = np.sin(2 * np.pi * freq * t).astype(np.float32)
        fade = min(n // 2, int(rate * 0.005))
        if fade:
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            tone[:fade] *= ramp
            tone[-fade:] *= ramp[::-1]
        parts.append(tone * volume)
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def _tts_command() -> list[str] | None:
    for exe in ("espeak-ng", "espeak"):
        path = shutil.which(exe)
        if path:
            return [path, "-s", "165"]
    if sys.platform == "darwin" and shutil.which("say"):
        return ["say"]
    return None


class AudioSink:
    name = "audio"

    def __init__(self, tts: bool = True, min_gap_s: float = 2.0) -> None:
        self.min_gap_s = min_gap_s
        self._last = 0.0
        self._tts = _tts_command() if tts else None
        try:
            import sounddevice

            self._sd = sounddevice
        except (ImportError, OSError):  # missing package or no PortAudio
            self._sd = None
            log.info("sounddevice unavailable; using the terminal bell for audio alerts")
        self._buffers = {sev: synth(m) for sev, m in MOTIFS.items()}

    def emit(self, f: Finding) -> None:
        now = time.monotonic()
        if now - self._last < self.min_gap_s and f.severity < Severity.HIGH:
            return
        self._last = now
        if self._sd is not None:
            try:
                self._sd.play(self._buffers[f.severity], RATE, blocking=False)
            except Exception as exc:  # no output device
                log.debug("audio playback failed: %s", exc)
                self._sd = None
        else:
            sys.stderr.write("\a")
            sys.stderr.flush()
        if self._tts and f.severity >= Severity.MEDIUM:
            text = f"{f.severity.label} alert. {f.title}."
            try:
                subprocess.Popen(
                    [*self._tts, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except OSError as exc:
                log.debug("tts failed: %s", exc)
                self._tts = None
