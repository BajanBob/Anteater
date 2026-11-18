"""Recursive SQL (WITH RECURSIVE) queries over the observations table.

All three run unchanged on SQLite and on PostgreSQL/TimescaleDB; only the
"seconds since epoch" expression differs by dialect.

* TRAIL_SQL    - walks one device's sightings in time order, one row per step,
                 and numbers a new "leg" every time it moves to another grid cell.
* CHAINS_SQL   - links randomised MAC addresses into chains: B follows A when it
                 appears soon after A goes quiet, probing for the same SSID in the
                 same cell. Each chain is probably one physical phone.
* SLOTS_SQL    - a recursive series of fixed time slots joined to the sightings,
                 so silent slots show up as gaps instead of disappearing.

Every recursion has a hard step/depth limit and the chain walk also refuses to
revisit a subject, so bad data can never make a query run forever.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MAX_TRAIL_STEPS = 5000
MAX_CHAIN_DEPTH = 50
MAX_SLOTS = 10000


def epoch(dialect: str, col: str) -> str:
    """SQL for 'seconds since 1970' of a timestamp column."""
    if dialect == "postgresql":
        return f"EXTRACT(EPOCH FROM {col})"
    # SQLite stores DateTime as ISO text; julianday() parses it
    return f"((julianday({col}) - 2440587.5) * 86400.0)"


TRAIL_SQL = """
WITH RECURSIVE
seen AS (
    SELECT ts, cell, lat, lon, rssi,
           ROW_NUMBER() OVER (ORDER BY ts, id) AS n
    FROM observations
    WHERE subject = :subject AND ts >= :since AND cell IS NOT NULL
),
walk(n, ts, cell, lat, lon, rssi, leg) AS (
    SELECT n, ts, cell, lat, lon, rssi, 1
    FROM seen WHERE n = 1
    UNION ALL
    SELECT s.n, s.ts, s.cell, s.lat, s.lon, s.rssi,
           w.leg + CASE WHEN s.cell <> w.cell THEN 1 ELSE 0 END
    FROM walk w
    JOIN seen s ON s.n = w.n + 1
    WHERE w.n < :max_steps
)
SELECT leg,
       cell,
       MIN(ts)   AS arrived,
       MAX(ts)   AS departed,
       AVG(lat)  AS lat,
       AVG(lon)  AS lon,
       COUNT(*)  AS sightings,
       MAX(rssi) AS best_rssi
FROM walk
GROUP BY leg, cell
ORDER BY leg
"""


def chains_sql(dialect: str) -> str:
    first, last = epoch(dialect, "b.first_ts"), epoch(dialect, "a.last_ts")
    return f"""
WITH RECURSIVE
span AS (
    SELECT subject, ssid,
           MIN(ts) AS first_ts, MAX(ts) AS last_ts,
           MIN(cell) AS cell, COUNT(*) AS n
    FROM observations
    WHERE ts >= :since AND kind = :kind AND ssid IS NOT NULL AND ssid <> ''
    GROUP BY subject, ssid
),
cand AS (
    SELECT a.subject AS src, b.subject AS dst, a.ssid AS ssid,
           {first} - {last} AS gap_s,
           ROW_NUMBER() OVER (PARTITION BY a.subject ORDER BY b.first_ts, b.subject) AS rs,
           ROW_NUMBER() OVER (PARTITION BY b.subject ORDER BY a.last_ts DESC, a.subject) AS rd
    FROM span a
    JOIN span b
      ON b.ssid = a.ssid
     AND b.subject <> a.subject
     AND b.first_ts > a.last_ts
     AND {first} - {last} <= :gap
     AND (a.cell IS NULL OR b.cell IS NULL OR a.cell = b.cell)
),
link AS (
    SELECT src, dst, ssid, gap_s FROM cand WHERE rs = 1 AND rd = 1
),
chain(root, subject, depth, path, ssid) AS (
    SELECT l.src, l.src, 0, '/' || l.src || '/', l.ssid
    FROM link l
    WHERE l.src NOT IN (SELECT dst FROM link)
    UNION ALL
    SELECT c.root, l.dst, c.depth + 1, c.path || l.dst || '/', c.ssid
    FROM chain c
    JOIN link l ON l.src = c.subject
    WHERE c.depth < :max_depth
      AND c.path NOT LIKE '%/' || l.dst || '/%'
)
SELECT root, subject, depth, path, ssid
FROM chain
ORDER BY root, depth
"""


def slots_sql(dialect: str) -> str:
    ts = epoch(dialect, "o.ts")
    return f"""
WITH RECURSIVE
slot(k, t0) AS (
    SELECT 0, CAST(:start AS DOUBLE PRECISION)
    UNION ALL
    SELECT k + 1, t0 + :width
    FROM slot
    WHERE t0 + :width < :stop AND k + 1 < :max_slots
)
SELECT s.k, s.t0,
       COUNT(o.id)               AS sightings,
       COUNT(DISTINCT o.subject) AS devices
FROM slot s
LEFT JOIN observations o
  ON o.ts >= :since
 AND {ts} >= s.t0 AND {ts} < s.t0 + :width
 AND (:kind = '' OR o.kind = :kind)
GROUP BY s.k, s.t0
ORDER BY s.k
"""


def as_dt(v: Any) -> datetime:
    """Normalise a timestamp from either backend to an aware UTC datetime."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    d = datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def group_chains(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn flat (root, subject, depth) rows into one record per chain, longest first."""
    by_root: dict[str, dict[str, Any]] = {}
    for r in rows:
        c = by_root.setdefault(r["root"], {"root": r["root"], "ssid": r["ssid"], "subjects": []})
        c["subjects"].append(r["subject"])
    out = [dict(c, length=len(c["subjects"])) for c in by_root.values()]
    out.sort(key=lambda c: (-c["length"], c["root"]))
    return out
