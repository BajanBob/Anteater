"""Video alerts: a 1080p heads-up-display frame per alert, saved as a short clip.

Needs OpenCV (the ``alerts`` extra). Clips land in ``alerts.clips_dir`` as
``<timestamp>_<engine>.mp4`` and make a visual evidence trail you can scrub.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from anteater.detections.base import Finding
from anteater.scoring.severity import Severity

log = logging.getLogger(__name__)
W, H = 1920, 1080

# BGR
COLOURS: dict[Severity, tuple[int, int, int]] = {
    Severity.INFO: (200, 200, 200),
    Severity.LOW: (220, 200, 80),
    Severity.MEDIUM: (40, 200, 240),
    Severity.HIGH: (30, 120, 255),
    Severity.CRITICAL: (60, 60, 240),
}


def render_hud(f: Finding, frame_no: int = 0, cv2: Any = None) -> np.ndarray:
    """Draw one HUD frame for a finding. Returns a BGR uint8 array (1080, 1920, 3)."""
    if cv2 is None:
        import cv2 as _cv2

        cv2 = _cv2
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:] = (18, 14, 12)
    colour = COLOURS[f.severity]
    font = cv2.FONT_HERSHEY_SIMPLEX

    # pulsing frame border
    pulse = 6 + int(4 * (1 + np.sin(frame_no / 3.0)))
    cv2.rectangle(img, (0, 0), (W - 1, H - 1), colour, pulse)
    # header band
    cv2.rectangle(img, (0, 0), (W, 140), colour, -1)
    cv2.putText(img, f"{f.severity.label.upper()}  |  {f.engine}", (60, 95), font, 2.0, (10, 10, 10), 4, cv2.LINE_AA)

    title = f.title if len(f.title) <= 60 else f.title[:57] + "..."
    cv2.putText(img, title, (60, 260), font, 1.8, (245, 245, 245), 3, cv2.LINE_AA)
    cv2.putText(img, f"subject  {f.subject}", (60, 350), font, 1.3, colour, 2, cv2.LINE_AA)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.ts))
    where = f"{f.lat:.5f}, {f.lon:.5f}" if f.lat is not None and f.lon is not None else "no GPS fix"
    cv2.putText(img, f"{stamp}   {where}   cell {f.cell or '-'}", (60, 430), font, 1.0, (180, 180, 180), 2, cv2.LINE_AA)

    y = 520
    for k, v in list(f.evidence.items())[:9]:
        text = f"{k}: {v}"
        if len(text) > 95:
            text = text[:92] + "..."
        cv2.putText(img, text, (90, y), font, 0.95, (210, 210, 210), 2, cv2.LINE_AA)
        y += 52

    # confidence bar
    cv2.rectangle(img, (60, H - 90), (W - 60, H - 60), (70, 70, 70), 2)
    cv2.rectangle(img, (62, H - 88), (62 + int((W - 124) * max(0.0, min(1.0, f.score))), H - 62), colour, -1)
    cv2.putText(img, f"confidence {f.score:.0%}", (60, H - 105), font, 0.9, (200, 200, 200), 2, cv2.LINE_AA)
    return img


class VideoSink:
    name = "video"

    def __init__(self, clips_dir: Path, seconds: float = 3.0, fps: int = 10,
                 min_severity: Severity = Severity.MEDIUM) -> None:
        import cv2

        self.cv2 = cv2
        self.dir = clips_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.frames = max(1, int(seconds * fps))
        self.fps = fps
        self.min_severity = min_severity
        self.written: list[Path] = []

    def emit(self, f: Finding) -> None:
        if f.severity < self.min_severity:
            return
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(f.ts)) + f"_{f.engine}_{f.id[:6]}.mp4"
        path = self.dir / name
        writer = self.cv2.VideoWriter(str(path), self.cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (W, H))
        if not writer.isOpened():
            log.warning("cannot open video writer for %s", path)
            return
        try:
            for i in range(self.frames):
                writer.write(render_hud(f, i, self.cv2))
        finally:
            writer.release()
        self.written.append(path)
        log.debug("wrote alert clip %s", path)
