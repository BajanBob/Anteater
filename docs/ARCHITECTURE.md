# Architecture

Anteater is three small processes around one database, plus an optional SDR
sweeper. Each can be restarted without the others.

```
 Kismet ──REST + event bus──▶ anteater collect ──batched inserts──▶  DB  ◀── anteater ui ──▶ browser
 gpsd ──────────────────────▶      (+ GPS fix)                       │          (dashboard, API)
                                                                     ▼
 termite scan ──POST /api/observations──▶ anteater ui --detect   anteater detect
                                                                     │
                                         findings ◀──────────────────┘
                                             │
                    log · evidence.jsonl · DB · MQTT · audio · 1080p HUD clips
```

## Data flow

1. **Collectors** turn raw sources into `Observation` records
   (`anteater/detections/base.py`): one sighting of one emitter, with kind,
   subject (MAC, cell id or frequency), signal, position and free-form
   `extra` fields. `collectors/kismet_eventbus.py` polls Kismet's REST API
   and uses its event bus to poll sooner when new devices appear.
   `collectors/gps.py` keeps the latest gpsd fix.
2. **Ingest** (`db/ingest.py`) batches observations into the database so a
   busy RF environment means a few large inserts, not thousands of small ones.
3. **The pipeline** (`pipeline.py`) enriches each observation (geohash cell,
   OUI vendor, session), records it in the **baseline**
   (`scoring/baseline.py`), then runs every enabled **engine**.
4. **Engines** (`detections/*.py`) return `Finding`s. Each is a small class
   with its own state; they share the `Context` (settings, baseline,
   fingerprints, allowlist, repeat-offender counts).
5. **Severity** (`scoring/severity.py`): engines pick a level; the pipeline
   escalates repeat offenders by one level per three prior findings, capped
   at HIGH.
6. **The alert router** (`alerts/router.py`) drops findings below
   `alerts.min_severity` and repeats within `alerts.dedupe_seconds` (unless
   the severity went up), then fans out to every sink.

## Why separate processes

`collect` must never stall on slow detection or a slow browser, and
`detect` should be restartable after changing engines or rules without
losing radio data. The database is the hand-off: `detect` follows new rows
with a `(ts, id)` cursor, `ui` polls recent findings for its websocket.
For a single small device, `anteater collect --detect` runs everything in
one process.

## Storage

- **TimescaleDB** (default via docker compose): `observations` becomes a
  hypertable on `ts`, so retention and compression policies work.
- **SQLite** works for a laptop or Pi: set
  `ANTEATER_GENERAL__DB_URL=sqlite+aiosqlite:///./anteater.db`.
- `data/baseline.json` holds the learned per-cell baseline; delete it to
  start learning afresh.
- `data/evidence.jsonl` is an append-only log of every alert, kept even
  when the database is wiped.

## Adding an engine

1. Create `anteater/detections/my_engine.py` with a `Detector` subclass: set
   `name`, `description` and `kinds`, and implement `process(obs)`.
2. Register it in `detections/registry.py`.
3. Add observations that trip it to `selftest.scenario()`. The selftest and
   `tests/unit/test_smoke.py` fail until every registered engine fires.

## Packaging

- `pip install .` installs `anteater` and `termite`. The wheel carries the
  signatures, band plans, UI and migrations under `anteater/_data`.
- `build/anteater.spec` makes one PyInstaller executable that contains Python
  and all dependencies; `termite` is available as `anteater termite`.
- `scripts/build_pyz.py` makes `anteater.pyz`, a single file that runs on any
  Python 3.11+ with the dependencies installed.
- `anteater/paths.py` finds data in a checkout, a wheel, the PyInstaller
  bundle or the zipapp, in that order.
