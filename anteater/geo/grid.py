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


# ------------------------------------------------------------------ recursion
# Geohash cells nest: every cell has 32 children one character longer. The
# functions below walk that tree, and the neighbour graph, recursively.

MAX_FILL_DEPTH = 400  # recursion frames per flood-fill call; deeper work is re-queued


def children(cell: str) -> list[str]:
    """The 32 cells one level finer inside ``cell``."""
    return [cell + c for c in _BASE32]


def rollup(counts: dict[str, int], precision: int = 1) -> list[dict[str, object]]:
    """Recursively group per-cell counts into a zoomable tree.

    ``counts`` maps fine cells (any mix of lengths) to a count. The result has one
    node per cell at ``precision`` with ``count`` (sum of everything inside) and
    ``children`` built the same way one level finer, down to the input cells.
    Only branches that hold data are created, so the tree stays small.
    """
    if precision < 1:
        raise ValueError("precision must be >= 1")
    items = [(c.lower(), n) for c, n in counts.items() if c]
    return _rollup(items, precision)


def _rollup(items: list[tuple[str, int]], precision: int) -> list[dict[str, object]]:
    groups: dict[str, list[tuple[str, int]]] = {}
    for cell, n in items:
        if len(cell) >= precision:
            groups.setdefault(cell[:precision], []).append((cell, n))
    out: list[dict[str, object]] = []
    for prefix in sorted(groups):
        members = groups[prefix]
        deeper = [(c, n) for c, n in members if len(c) > precision]
        out.append(
            {
                "cell": prefix,
                "count": sum(n for _, n in members),
                # base case: nothing finer below this prefix
                "children": _rollup(deeper, precision + 1) if deeper else [],
            }
        )
    return out


def clusters(cells: set[str] | list[str] | dict[str, int]) -> list[list[str]]:
    """Group touching cells (8-neighbour) into clusters by recursive flood fill.

    Python's call stack is limited, so each fill recurses at most MAX_FILL_DEPTH
    frames; cells reached at that depth are handed back and filled by a fresh
    recursive call, which gives the same answer for any cluster size.
    """
    todo = {c.lower() for c in cells}
    out: list[list[str]] = []
    while todo:
        seed = min(todo)
        found: set[str] = set()
        frontier = [seed]
        while frontier:
            nxt: list[str] = []
            for c in frontier:
                _fill(c, todo, found, 0, nxt)
            frontier = nxt
        out.append(sorted(found))
    out.sort(key=lambda g: (-len(g), g[0]))
    return out


def _fill(cell: str, todo: set[str], found: set[str], depth: int, overflow: list[str]) -> None:
    if cell not in todo:
        return
    if depth >= MAX_FILL_DEPTH:
        overflow.append(cell)  # continue later from a shallow stack
        return
    todo.discard(cell)
    found.add(cell)
    for n in neighbours(cell):
        _fill(n, todo, found, depth + 1, overflow)


def hotspots(counts: dict[str, int], min_total: int = 1) -> list[dict[str, object]]:
    """Clusters of busy cells with their total count and weighted centre."""
    out: list[dict[str, object]] = []
    for group in clusters(counts):
        total = sum(counts.get(c, counts.get(c.upper(), 0)) for c in group)
        if total < min_total:
            continue
        lat = lon = 0.0
        for c in group:
            w = counts.get(c, counts.get(c.upper(), 0)) or 0
            clat, clon = decode(c)
            lat += clat * w
            lon += clon * w
        w_all = total or 1
        out.append({"cells": group, "total": total, "lat": lat / w_all, "lon": lon / w_all})
    out.sort(key=lambda h: -int(h["total"]))  # type: ignore[call-overload]
    return out
