# Troubleshooting

Start with `anteater selftest --no-radio` (the install), then
`anteater selftest` (Kismet and GPS too). Run anything with
`ANTEATER_LOG_LEVEL=DEBUG` for detail.

## Install

**`SoapySDR` fails to install with pip.** That's expected: it isn't on PyPI.
Install it from OS packages ([TERMITE.md](TERMITE.md)). Anteater runs
without it; only termite needs it.

**`sounddevice` / PortAudio errors.** Install PortAudio
(`sudo apt install libportaudio2`). Without it, audio alerts fall back to the
terminal bell.

**No spoken alerts.** TTS uses `espeak-ng` (`sudo apt install espeak-ng`) or
macOS `say`. Set `[alerts] tts = false` to silence it.

## Database

**`connection refused` on port 5432.** Start the stack with
`docker compose up -d` and wait for `docker compose ps` to show `db` as
healthy. Or switch to SQLite: `ANTEATER_GENERAL__DB_URL=sqlite+aiosqlite:///./anteater.db`.

**`anteater db upgrade` says the scripts weren't found.** A build without
the `alembic/` folder falls back to creating tables directly. That's fine for
new databases.

**Too much disk.** With TimescaleDB, add a retention policy:
`SELECT add_retention_policy('observations', INTERVAL '90 days');`

## Kismet

**`kismet poll failed: 401`.** Wrong credentials. Use an API key
(`ANTEATER_KISMET__API_KEY`) or check user and password; Kismet asks you to
set them on first login at http://localhost:2501.

**Collector runs but sees no devices.** Check Kismet's web UI shows data
sources running. Interfaces must support monitor mode, and Kismet needs root
or the `kismet` group.

**`--kismet` URL: ws or http?** Either works. Anteater derives the REST
(`http://`) and event-bus (`ws://`) URLs from one setting.

## GPS

**`no fix from gpsd`.** Run `cgps` or `gpsmon`; a cold start outdoors can
take a few minutes. For a fixed install, set `[gps] source = "fixed"`.

## Findings

**Too many alerts.** Raise `[alerts] min_severity`, lengthen
`dedupe_seconds`, add your own devices to `config/allowlist.yaml`, and set
`home_cells` so nothing seen at home counts as following.

**Nothing from `fingerprints` for vendor rules.** `config/oui.csv` is empty
until you run `anteater fp sync`.

**`follow_score` never fires.** It needs GPS and real movement: at least 3
places about a kilometre apart, over 20+ minutes.

## Dashboard

**Blank page or 404 at `/`.** The UI files weren't found. Run from the repo
root, or set `ANTEATER_HOME` to it.

**Dashboard reachable from other machines.** It binds to 127.0.0.1 by
default. If you pass `--host 0.0.0.0`, anyone on that network can read your
findings; put an authenticating reverse proxy in front.

## Executables

**macOS refuses to open the binary.** Unsigned builds are quarantined. Run
`xattr -d com.apple.quarantine anteater-macos-arm64`, or right-click it and
choose Open.

**Windows SmartScreen warning.** The build isn't code-signed. Choose
"More info → Run anyway", or build it yourself with `make build`.

**AppImage won't start: FUSE.** Install `libfuse2`, or run it with
`--appimage-extract-and-run`.
