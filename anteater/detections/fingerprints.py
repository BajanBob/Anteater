"""Vendor fingerprints: IEEE OUI lookup plus YAML signature rules.

A rule file in ``config/vendors/`` looks like::

    category: alpr
    severity: high          # default for every rule in the file
    rules:
      - name: Flock Safety (registered OUI)
        match:
          vendor: ["(?i)flock safety"]   # regex on the IEEE organisation name
          ssid: ["^Flock-[0-9A-Fa-f]+$"]
          kinds: [wifi_ap, ble]          # restrict to these observation kinds
        mode: any                        # any (default) or all criteria must hit
        severity: high
        note: free text shown in the evidence log

Match criteria: ``vendor``, ``ssid``, ``name`` (BLE local name), ``mac_prefix``,
``ble_company_id``, ``ble_mfr_prefix`` (hex of manufacturer data, company id
little-endian first), ``ble_service_uuid``, ``vendor_ie`` (OUI hex of a Wi-Fi
vendor IE) and ``kinds``. ``kinds`` always has to match; the others are
combined by ``mode``. A rule with only ``kinds`` matches every observation of
those kinds.
"""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from anteater.detections.base import Detector, Finding, Observation, is_randomised_mac
from anteater.scoring.severity import Severity

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ OUI
def normalise_mac(mac: str) -> str:
    hexes = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    return ":".join(hexes[i : i + 2] for i in range(0, len(hexes), 2))


class OuiDB:
    """IEEE MA-L/MA-M/MA-S registry, as written by ``anteater fp sync``."""

    def __init__(self) -> None:
        # prefix length in hex digits -> {prefix: organisation}
        self._by_len: dict[int, dict[str, str]] = {6: {}, 7: {}, 9: {}}

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_len.values())

    def add(self, prefix_hex: str, org: str) -> None:
        p = prefix_hex.lower()
        self._by_len.setdefault(len(p), {})[p] = org.strip()

    @classmethod
    def load(cls, path: Path | None) -> OuiDB:
        db = cls()
        if path is None or not path.is_file():
            return db
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                prefix = (row.get("Assignment") or "").strip()
                org = (row.get("Organization Name") or "").strip()
                if prefix and org:
                    db.add(prefix, org)
        log.debug("loaded %d OUI entries from %s", len(db), path)
        return db

    def lookup(self, mac: str) -> str | None:
        if is_randomised_mac(mac):
            return None
        hexes = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
        for n in (9, 7, 6):  # longest (most specific) assignment first
            hit = self._by_len.get(n, {}).get(hexes[:n])
            if hit:
                return hit
        return None


# ---------------------------------------------------------------- rules
@dataclass(slots=True)
class Rule:
    name: str
    category: str
    severity: Severity
    note: str = ""
    mode: str = "any"
    kinds: tuple[str, ...] = ()
    vendor: tuple[re.Pattern[str], ...] = ()
    ssid: tuple[re.Pattern[str], ...] = ()
    local_name: tuple[re.Pattern[str], ...] = ()
    mac_prefix: tuple[str, ...] = ()
    ble_company_id: tuple[int, ...] = ()
    ble_mfr_prefix: tuple[str, ...] = ()
    ble_service_uuid: tuple[str, ...] = ()
    vendor_ie: tuple[str, ...] = ()
    source: str = ""

    def criteria(self, obs: Observation) -> list[bool]:
        """One bool per criterion the rule defines."""
        out: list[bool] = []
        extra = obs.extra
        if self.vendor:
            out.append(bool(obs.vendor) and any(p.search(obs.vendor or "") for p in self.vendor))
        if self.ssid:
            out.append(bool(obs.ssid) and any(p.search(obs.ssid or "") for p in self.ssid))
        if self.local_name:
            nm = str(extra.get("name") or "")
            out.append(bool(nm) and any(p.search(nm) for p in self.local_name))
        if self.mac_prefix:
            mac = normalise_mac(obs.subject)
            out.append(any(mac.startswith(p) for p in self.mac_prefix))
        if self.ble_company_id:
            ids = {int(x) for x in _as_list(extra.get("company_ids"))}
            out.append(bool(ids & set(self.ble_company_id)))
        if self.ble_mfr_prefix:
            data = [str(x).lower() for x in _as_list(extra.get("manufacturer_data"))]
            out.append(any(d.startswith(p) for d in data for p in self.ble_mfr_prefix))
        if self.ble_service_uuid:
            uuids = {_short_uuid(u) for u in _as_list(extra.get("service_uuids"))}
            uuids |= {_short_uuid(u) for u in (extra.get("service_data") or {})}
            out.append(bool(uuids & set(self.ble_service_uuid)))
        if self.vendor_ie:
            ies = {str(ie.get("oui", "")).lower().replace(":", "") for ie in _as_list(extra.get("vendor_ies")) if isinstance(ie, dict)}
            out.append(bool(ies & set(self.vendor_ie)))
        return out

    def matches(self, obs: Observation) -> bool:
        if self.kinds and obs.kind not in self.kinds:
            return False
        hits = self.criteria(obs)
        if not hits:
            return bool(self.kinds)  # a kinds-only rule matches every observation of that kind
        return all(hits) if self.mode == "all" else any(hits)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _short_uuid(u: Any) -> str:
    """Reduce a 16-bit UUID in any common spelling to 4 lowercase hex digits."""
    s = str(u).lower().replace("0x", "").replace("-", "")
    if len(s) == 32 and s.endswith("00001000800000805f9b34fb"):
        s = s[4:8]
    return s[-4:] if len(s) <= 8 else s


def _compile(items: Iterable[Any]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(str(x)) for x in items)


def _int(x: Any) -> int:
    return int(x, 0) if isinstance(x, str) else int(x)


def parse_rules(doc: dict[str, Any], source: str = "") -> list[Rule]:
    category = str(doc.get("category", Path(source).stem or "misc"))
    default = Severity.parse(doc.get("severity", "low"))
    rules: list[Rule] = []
    for raw in doc.get("rules") or []:
        m = raw.get("match") or {}
        rules.append(
            Rule(
                name=str(raw["name"]),
                category=str(raw.get("category", category)),
                severity=Severity.parse(raw.get("severity", default)),
                note=str(raw.get("note", "")),
                mode=str(raw.get("mode", "any")),
                kinds=tuple(m.get("kinds") or ()),
                vendor=_compile(m.get("vendor") or ()),
                ssid=_compile(m.get("ssid") or ()),
                local_name=_compile(m.get("name") or ()),
                mac_prefix=tuple(normalise_mac(p) for p in m.get("mac_prefix") or ()),
                ble_company_id=tuple(_int(x) for x in m.get("ble_company_id") or ()),
                ble_mfr_prefix=tuple(str(x).lower() for x in m.get("ble_mfr_prefix") or ()),
                ble_service_uuid=tuple(_short_uuid(x) for x in m.get("ble_service_uuid") or ()),
                vendor_ie=tuple(str(x).lower().replace(":", "") for x in m.get("vendor_ie") or ()),
                source=source,
            )
        )
    return rules


@dataclass
class FingerprintDB:
    rules: list[Rule] = field(default_factory=list)
    # extra data blocks from YAML files without rules (e.g. radar bands)
    tables: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load_dir(cls, directory: Path | None) -> FingerprintDB:
        db = cls()
        if directory is None or not directory.is_dir():
            return db
        for path in sorted(directory.glob("*.yaml")):
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"{path}: invalid YAML: {exc}") from exc
            db.rules.extend(parse_rules(doc, path.name))
            for key, value in doc.items():
                if key not in {"category", "severity", "rules", "description"}:
                    db.tables[f"{path.stem}.{key}"] = value
        return db

    def match(self, obs: Observation) -> list[Rule]:
        return [r for r in self.rules if r.matches(obs)]


# ------------------------------------------------- heuristic plug-ins
# Category modules (flock.py, cameras.py) register extra matchers here. Each
# takes an Observation and returns (category, title, severity, note) tuples.
Heuristic = Callable[[Observation], list[tuple[str, str, Severity, str]]]
HEURISTICS: list[Heuristic] = []


def register_heuristic(fn: Heuristic) -> Heuristic:
    HEURISTICS.append(fn)
    return fn


# --------------------------------------------------------------- engine
class FingerprintEngine(Detector):
    name = "fingerprints"
    description = "Flock Safety, ALPR backhaul, wireless cameras, AirTags, TPMS"

    def process(self, obs: Observation) -> list[Finding]:
        out: list[Finding] = []
        seen: set[str] = set()
        for rule in self.ctx.fingerprints.match(obs):
            title = f"{rule.category}: {rule.name}"
            if title in seen:
                continue
            seen.add(title)
            out.append(
                self.finding(
                    obs,
                    rule.severity,
                    title,
                    score=0.4 + 0.15 * int(rule.severity),
                    category=rule.category,
                    rule=rule.name,
                    rule_file=rule.source,
                    note=rule.note,
                    vendor=obs.vendor,
                    ssid=obs.ssid,
                    rssi=obs.rssi,
                )
            )
        for heuristic in HEURISTICS:
            for category, name, sev, note in heuristic(obs):
                title = f"{category}: {name}"
                if title in seen:
                    continue
                seen.add(title)
                out.append(
                    self.finding(
                        obs, sev, title, score=0.3 + 0.1 * int(sev),
                        category=category, rule=name, rule_file="heuristic", note=note,
                        vendor=obs.vendor, ssid=obs.ssid, rssi=obs.rssi,
                    )
                )
        return out


# importing these registers their heuristics
from anteater.detections import cameras as _cameras  # noqa: E402,F401
from anteater.detections import flock as _flock  # noqa: E402,F401
