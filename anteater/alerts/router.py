"""Alert router: severity filter, de-duplication, fan-out to sinks."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Iterable
from typing import Protocol

from anteater.detections.base import Finding
from anteater.scoring.severity import Severity

log = logging.getLogger(__name__)


class Sink(Protocol):
    name: str

    def emit(self, finding: Finding) -> None | Awaitable[None]: ...


class AlertRouter:
    """Drops findings below ``min_severity`` and repeats inside ``dedupe_s``.

    A repeat at a *higher* severity always goes through. Every sink gets every
    alert; one failing sink never blocks the others.
    """

    def __init__(
        self,
        sinks: Iterable[Sink] = (),
        min_severity: Severity | str = Severity.LOW,
        dedupe_s: float = 300.0,
    ) -> None:
        self.sinks: list[Sink] = list(sinks)
        self.min_severity = Severity.parse(min_severity)
        self.dedupe_s = dedupe_s
        self._last: dict[tuple[str, str, str], tuple[float, Severity]] = {}
        self.sent = 0
        self.suppressed = 0

    def add(self, sink: Sink) -> None:
        self.sinks.append(sink)

    def should_send(self, f: Finding) -> bool:
        if f.severity < self.min_severity:
            return False
        prev = self._last.get(f.key)
        if prev is not None:
            ts, sev = prev
            if f.ts - ts < self.dedupe_s and f.severity <= sev:
                return False
        self._last[f.key] = (f.ts, f.severity)
        return True

    async def route(self, f: Finding) -> bool:
        if not self.should_send(f):
            self.suppressed += 1
            return False
        self.sent += 1
        for sink in self.sinks:
            try:
                result = sink.emit(f)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("alert sink %s failed", getattr(sink, "name", sink))
        return True

    def close(self) -> None:
        for sink in self.sinks:
            closer = getattr(sink, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    log.exception("closing sink %s failed", getattr(sink, "name", sink))
