"""Core types shared by every engine: Observation in, Finding out."""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from anteater.scoring.severity import Severity

if TYPE_CHECKING:
    from anteater.detections.fingerprints import FingerprintDB, OuiDB
    from anteater.scoring.baseline import Baseline
    from anteater.settings import Settings

# Observation kinds
WIFI_AP = "wifi_ap"
WIFI_CLIENT = "wifi_client"
BLE = "ble"
CELL = "cell"
SDR = "sdr"
TPMS = "tpms"
KINDS = (WIFI_AP, WIFI_CLIENT, BLE, CELL, SDR, TPMS)


@dataclass(slots=True)
class Observation:
    """One sighting of one emitter. Produced by collectors, termite or replay files."""

    kind: str
    subject: str  # MAC for Wi-Fi/BLE, "mcc-mnc-lac-cid" for cells, "sdr:<MHz>" for SDR
    ts: float = field(default_factory=time.time)
    ssid: str | None = None
    channel: str | None = None
    freq_mhz: float | None = None
    rssi: float | None = None
    lat: float | None = None
    lon: float | None = None
    cell: str | None = None  # geohash
    vendor: str | None = None
    crypt: str | None = None
    hidden: bool | None = None
    session: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind in (WIFI_AP, WIFI_CLIENT, BLE):
            self.subject = self.subject.lower()

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, {}, "")}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Observation:
        known = cls.__dataclass_fields__.keys()
        base = {k: v for k, v in data.items() if k in known}
        extra = dict(base.pop("extra", None) or {})
        extra.update({k: v for k, v in data.items() if k not in known})
        if "kind" not in base or "subject" not in base:
            raise ValueError("observation needs 'kind' and 'subject'")
        return cls(**base, extra=extra)


@dataclass(slots=True)
class Finding:
    engine: str
    severity: Severity
    title: str
    subject: str
    ts: float = field(default_factory=time.time)
    kind: str | None = None
    cell: str | None = None
    lat: float | None = None
    lon: float | None = None
    score: float = 0.5
    evidence: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.engine, self.subject, self.title)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.label
        return d


@dataclass
class Context:
    """Shared, long-lived state handed to every engine."""

    settings: Settings
    baseline: Baseline
    oui: OuiDB
    fingerprints: FingerprintDB
    session: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S"))
    allowlist: set[str] = field(default_factory=set)
    home_cells: tuple[str, ...] = ()
    # subject -> number of findings so far (repeat offenders)
    flagged: dict[str, int] = field(default_factory=dict)

    def is_home(self, cell: str | None) -> bool:
        return bool(cell) and any(cell.startswith(h) for h in self.home_cells)  # type: ignore[union-attr]

    def is_allowed(self, subject: str) -> bool:
        return subject.lower() in self.allowlist


class Detector(ABC):
    """Base class for engines. Subclasses set ``name`` and implement ``process``."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    kinds: ClassVar[tuple[str, ...]] = KINDS

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def accepts(self, obs: Observation) -> bool:
        return obs.kind in self.kinds

    @abstractmethod
    def process(self, obs: Observation) -> list[Finding]:
        """Inspect one observation and return zero or more findings."""

    def finding(
        self,
        obs: Observation,
        severity: Severity,
        title: str,
        score: float,
        **evidence: Any,
    ) -> Finding:
        return Finding(
            engine=self.name,
            severity=severity,
            title=title,
            subject=obs.subject,
            ts=obs.ts,
            kind=obs.kind,
            cell=obs.cell,
            lat=obs.lat,
            lon=obs.lon,
            score=score,
            evidence=evidence,
        )


def is_randomised_mac(mac: str) -> bool:
    """Locally administered bit set: the address is (almost always) randomised."""
    try:
        return bool(int(mac.replace("-", ":").split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False
