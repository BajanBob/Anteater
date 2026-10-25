"""GNSS helpers: fixes, NMEA 0183 parsing (GGA / RMC) and distances."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True, slots=True)
class Fix:
    lat: float
    lon: float
    ts: float
    alt_m: float | None = None
    speed_mps: float | None = None
    hdop: float | None = None
    sats: int | None = None
    quality: int = 1  # 0 = no fix

    @property
    def valid(self) -> bool:
        return self.quality > 0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def nmea_checksum_ok(sentence: str) -> bool:
    s = sentence.strip()
    if not s.startswith("$") or "*" not in s:
        return False
    body, _, given = s[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(given[:2], 16)
    except ValueError:
        return False


def _coord(value: str, hemi: str) -> float | None:
    if not value or not hemi:
        return None
    dot = value.index(".") if "." in value else len(value)
    degrees = float(value[: dot - 2])
    minutes = float(value[dot - 2 :])
    out = degrees + minutes / 60.0
    return -out if hemi in ("S", "W") else out


def _hms(value: str, date: datetime | None = None) -> float:
    base = date or datetime.now(UTC)
    if len(value) < 6:
        return base.timestamp()
    h, m = int(value[0:2]), int(value[2:4])
    s = float(value[4:])
    whole = int(s)
    dt = base.replace(hour=h, minute=m, second=whole, microsecond=int((s - whole) * 1e6))
    return dt.timestamp()


def parse_nmea(sentence: str) -> Fix | None:
    """Parse one GGA or RMC sentence. Returns None for other types, bad checksums or no fix."""
    if not nmea_checksum_ok(sentence):
        return None
    fields = sentence.strip()[1:].split("*")[0].split(",")
    kind = fields[0][-3:]
    try:
        if kind == "GGA":
            quality = int(fields[6] or 0)
            lat, lon = _coord(fields[2], fields[3]), _coord(fields[4], fields[5])
            if quality == 0 or lat is None or lon is None:
                return None
            return Fix(
                lat=lat,
                lon=lon,
                ts=_hms(fields[1]),
                alt_m=float(fields[9]) if fields[9] else None,
                hdop=float(fields[8]) if fields[8] else None,
                sats=int(fields[7]) if fields[7] else None,
                quality=quality,
            )
        if kind == "RMC":
            if fields[2] != "A":
                return None
            lat, lon = _coord(fields[3], fields[4]), _coord(fields[5], fields[6])
            if lat is None or lon is None:
                return None
            date = None
            if len(fields[9]) == 6:
                d = fields[9]
                date = datetime(2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]), tzinfo=UTC)
            knots = float(fields[7]) if fields[7] else None
            return Fix(
                lat=lat,
                lon=lon,
                ts=_hms(fields[1], date),
                speed_mps=knots * 0.514444 if knots is not None else None,
            )
    except (ValueError, IndexError):
        return None
    return None
