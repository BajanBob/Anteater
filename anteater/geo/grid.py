"""Geohash grid squares. Self-contained, so there is no extra dependency.

Precision guide (cell size at the equator, roughly):
5 = 4.9 km, 6 = 1.2 km, 7 = 153 m, 8 = 38 m, 9 = 4.8 m.
"""

from __future__ import annotations

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_DECODE = {c: i for i, c in enumerate(_BASE32)}


def encode(lat: float, lon: float, precision: int = 7) -> str:
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError(f"coordinates out of range: {lat}, {lon}")
    if precision < 1:
        raise ValueError("precision must be >= 1")
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out: list[str] = []
    bit, ch, even = 0, 0, True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch = (ch << 1) | 1
                lon_lo = mid
            else:
                ch <<= 1
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch = (ch << 1) | 1
                lat_lo = mid
            else:
                ch <<= 1
                lat_hi = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(_BASE32[ch])
            bit, ch = 0, 0
    return "".join(out)


def bounds(cell: str) -> tuple[float, float, float, float]:
    """Return (lat_min, lon_min, lat_max, lon_max)."""
    if not cell:
        raise ValueError("empty geohash")
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    even = True
    for c in cell.lower():
        try:
            val = _DECODE[c]
        except KeyError:
            raise ValueError(f"invalid geohash character {c!r}") from None
        for shift in range(4, -1, -1):
            b = (val >> shift) & 1
            if even:
                mid = (lon_lo + lon_hi) / 2
                if b:
                    lon_lo = mid
                else:
                    lon_hi = mid
            else:
                mid = (lat_lo + lat_hi) / 2
                if b:
                    lat_lo = mid
                else:
                    lat_hi = mid
            even = not even
    return lat_lo, lon_lo, lat_hi, lon_hi


def decode(cell: str) -> tuple[float, float]:
    """Centre of the cell as (lat, lon)."""
    lat_lo, lon_lo, lat_hi, lon_hi = bounds(cell)
    return (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2


def neighbours(cell: str) -> list[str]:
    """The eight surrounding cells at the same precision (fewer at the poles)."""
    lat_lo, lon_lo, lat_hi, lon_hi = bounds(cell)
    dlat, dlon = lat_hi - lat_lo, lon_hi - lon_lo
    lat, lon = (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2
    out: list[str] = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == dy == 0:
                continue
            nlat = lat + dy * dlat
            if not -90.0 <= nlat <= 90.0:
                continue
            nlon = (lon + dx * dlon + 180.0) % 360.0 - 180.0
            n = encode(nlat, nlon, len(cell))
            if n != cell and n not in out:
                out.append(n)
    return out


def parent(cell: str, precision: int) -> str:
    return cell[:precision]


def cell_of(lat: float | None, lon: float | None, precision: int = 7) -> str | None:
    if lat is None or lon is None:
        return None
    return encode(lat, lon, precision)
