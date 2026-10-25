"""Alert sinks: log, evidence file, MQTT, database, in-process broadcast."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anteater.detections.base import Finding
from anteater.scoring.severity import Severity

if TYPE_CHECKING:
    from anteater.db.repository import Repository

log = logging.getLogger("anteater.alert")

_LOG_LEVEL = {
    Severity.INFO: logging.DEBUG,
    Severity.LOW: logging.INFO,
    Severity.MEDIUM: logging.WARNING,
    Severity.HIGH: logging.WARNING,
    Severity.CRITICAL: logging.ERROR,
}


def _dumps(obj: Any) -> str:
    try:
        import orjson

        return orjson.dumps(obj, default=str).decode()
    except ImportError:
        return json.dumps(obj, default=str, separators=(",", ":"))


class LogSink:
    name = "log"

    def emit(self, f: Finding) -> None:
        log.log(
            _LOG_LEVEL[f.severity],
            "[%s] %s: %s (%s)",
            f.severity.label.upper(),
            f.engine,
            f.title,
            f.subject,
        )


class EvidenceSink:
    """Append-only JSON Lines evidence log. One finding per line, flushed immediately."""

    name = "evidence"

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def emit(self, f: Finding) -> None:
        self._fh.write(_dumps(f.to_dict()) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class MqttSink:
    """Publishes to ``<topic>/<severity>/<engine>``, retained=False, QoS 1."""

    name = "mqtt"

    def __init__(self, host: str, port: int = 1883, topic: str = "anteater/alerts") -> None:
        import paho.mqtt.client as mqtt

        self.topic = topic.rstrip("/")
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="anteater")
        self.client.connect_async(host, port)
        self.client.loop_start()

    def emit(self, f: Finding) -> None:
        self.client.publish(
            f"{self.topic}/{f.severity.label}/{f.engine}", _dumps(f.to_dict()), qos=1
        )

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class DbSink:
    name = "db"

    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def emit(self, f: Finding) -> None:
        await self.repo.add_finding(f)


class BroadcastSink:
    """Fan-out to in-process subscribers (the dashboard's websocket clients)."""

    name = "broadcast"

    def __init__(self, maxsize: int = 256) -> None:
        self.maxsize = maxsize
        self.queues: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self.maxsize)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self.queues.discard(q)

    def emit(self, f: Finding) -> None:
        payload = f.to_dict()
        for q in list(self.queues):
            if q.full():  # slow client: drop its oldest alert rather than block
                q.get_nowait()
            q.put_nowait(payload)


class MemorySink:
    """Collects findings; used by selftest and tests."""

    name = "memory"

    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def emit(self, f: Finding) -> None:
        self.findings.append(f)
