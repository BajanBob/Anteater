"""Command line: ``anteater <command>``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

import typer

from anteater import __version__
from anteater.logging_setup import setup_logging
from anteater.settings import Settings, load_settings

app = typer.Typer(
    name="anteater",
    help="Passive RF surveillance detection. Receive-only: never transmits.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="Database schema.", no_args_is_help=True)
fp_app = typer.Typer(help="Vendor fingerprints and the OUI registry.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(fp_app, name="fp")


def _add_termite() -> None:
    """`anteater termite ...` mirrors the `termite` command, so one executable does both."""
    try:
        from termite.cli import app as termite_app
    except ImportError:  # anteater installed without termite
        return
    app.add_typer(termite_app, name="termite", help="SDR sweeps (same as the `termite` command).")


_add_termite()

log = logging.getLogger("anteater")

ConfigOpt = typer.Option(
    None, "--config", "-c", help="Path to anteater.toml.", envvar="ANTEATER_CONFIG"
)


def _settings(config: Path | None, **overrides: dict[str, Any]) -> Settings:
    try:
        s = load_settings(
            config,
            overrides={
                k: {kk: vv for kk, vv in v.items() if vv is not None} for k, v in overrides.items()
            },
        )
    except Exception as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None
    setup_logging(s.log_level)
    return s


def _run(coro: Any) -> None:
    """Run a coroutine, turning Ctrl-C / SIGTERM into a clean cancel."""

    async def main() -> None:
        task = asyncio.ensure_future(coro)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows
                loop.add_signal_handler(sig, task.cancel)
        with contextlib.suppress(asyncio.CancelledError):
            await task

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())


def _router(settings: Settings, repo: Any = None, quiet: bool = False) -> Any:
    from anteater.alerts.router import AlertRouter
    from anteater.alerts.sinks import DbSink, EvidenceSink, LogSink, MqttSink

    a = settings.alerts
    router = AlertRouter(min_severity=a.min_severity, dedupe_s=a.dedupe_seconds)
    if not quiet:
        router.add(LogSink())
    router.add(EvidenceSink(a.evidence_log))
    if repo is not None:
        router.add(DbSink(repo))
    if a.mqtt:
        try:
            router.add(MqttSink(a.mqtt_host, a.mqtt_port, a.mqtt_topic))
        except Exception as exc:
            log.warning("MQTT disabled: %s", exc)
    if a.audio and not quiet:
        from anteater.alerts.audio import AudioSink

        router.add(AudioSink(tts=a.tts))
    if a.video:
        try:
            from anteater.alerts.video import VideoSink

            router.add(VideoSink(a.clips_dir))
        except ImportError:
            log.warning("video alerts need OpenCV: pip install 'anteater[alerts]'")
    return router


# ------------------------------------------------------------- basics
@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"anteater {__version__}")


@app.command()
def selftest(
    no_radio: bool = typer.Option(False, "--no-radio", help="Skip Kismet and GPS checks."),
    config: Path | None = ConfigOpt,
) -> None:
    """Validate the install. With --no-radio, needs no hardware at all."""
    from anteater import selftest as st

    settings = _settings(config)
    report = st.run(settings, radio=not no_radio)
    width = max(len(c.name) for c in report.checks)
    for c in report.checks:
        mark = (
            typer.style(" ok ", fg=typer.colors.GREEN)
            if c.ok
            else (
                typer.style("warn", fg=typer.colors.YELLOW)
                if c.optional
                else typer.style("FAIL", fg=typer.colors.RED)
            )
        )
        typer.echo(f"[{mark}] {c.name.ljust(width)}  {c.detail}")
    typer.echo("selftest passed" if report.ok else "selftest FAILED")
    raise typer.Exit(0 if report.ok else 1)


# ----------------------------------------------------------------- db
@db_app.command("upgrade")
def db_upgrade(config: Path | None = ConfigOpt) -> None:
    """Create or migrate the schema to the latest version."""
    from anteater.db.session import upgrade

    settings = _settings(config)
    try:
        typer.echo(upgrade(settings.general.db_url))
    except Exception as exc:
        typer.secho(f"database upgrade failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


# ----------------------------------------------------------------- fp
@fp_app.command("sync")
def fp_sync(
    dest: Path = typer.Option(Path("config/oui.csv"), help="Where to write the merged registry."),
) -> None:
    """Download the IEEE OUI registries (MA-L, MA-M, MA-S) into config/oui.csv."""
    from anteater.oui_sync import sync

    setup_logging("INFO")
    try:
        n = sync(dest)
    except Exception as exc:
        typer.secho(f"OUI sync failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"wrote {n:,} assignments to {dest}")


@fp_app.command("list")
def fp_list(config: Path | None = ConfigOpt) -> None:
    """List loaded fingerprint rules."""
    from rich.console import Console
    from rich.table import Table

    from anteater.pipeline import build_context

    ctx = build_context(_settings(config))
    table = Table(
        "category",
        "rule",
        "severity",
        "file",
        title=f"{len(ctx.fingerprints.rules)} rules, {len(ctx.oui):,} OUI entries",
    )
    for r in ctx.fingerprints.rules:
        table.add_row(r.category, r.name, r.severity.label, r.source)
    Console().print(table)


# ------------------------------------------------------------ collect
@app.command()
def collect(
    kismet: str | None = typer.Option(
        None, "--kismet", help="Kismet URL, e.g. ws://localhost:2501"
    ),
    user: str | None = typer.Option(None, "--user"),
    password: str | None = typer.Option(None, "--pass", "--password"),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Kismet API key (instead of user/pass)."
    ),
    detect: bool = typer.Option(False, "--detect", help="Also run every engine in this process."),
    config: Path | None = ConfigOpt,
) -> None:
    """Ingest Kismet + GPS into the database."""
    settings = _settings(
        config, kismet={"url": kismet, "user": user, "password": password, "api_key": api_key}
    )

    async def main() -> None:
        from anteater.collectors.gps import make_gps
        from anteater.collectors.kismet_eventbus import KismetCollector
        from anteater.db.ingest import BatchWriter
        from anteater.db.repository import Repository
        from anteater.db.session import create_all
        from anteater.geo.grid import cell_of
        from anteater.pipeline import Pipeline

        repo = Repository.from_url(settings.general.db_url)
        await create_all(repo.engine)
        writer = BatchWriter(repo)
        writer.start()
        gps = make_gps(settings.gps)
        await gps.start()
        collector = KismetCollector(settings.kismet, gps)
        pipeline = Pipeline.from_settings(settings, _router(settings, repo)) if detect else None
        session_id = pipeline.ctx.session if pipeline else time.strftime("%Y%m%d-%H%M%S")
        await repo.start_session(session_id, note="collect")
        log.info("collecting from %s (session %s)", settings.kismet.http_url, session_id)
        n = 0
        try:
            async for obs in collector.stream():
                obs.session = session_id
                obs.cell = cell_of(obs.lat, obs.lon, settings.general.grid_precision)
                if pipeline:
                    await pipeline.process(obs)
                writer.put(obs)
                n += 1
                if n % 5000 == 0:
                    log.info("%d observations collected", n)
        finally:
            collector.stop()
            await gps.stop()
            await writer.stop()
            if pipeline:
                pipeline.close()
            await repo.end_session(session_id)
            await repo.close()
            log.info("stopped after %d observations", n)

    _run(main())


# ------------------------------------------------------------- detect
@app.command()
def detect(
    engines: str = typer.Option(
        "all", "--engines", "-e", help="Comma list, 'all', or '-name' to exclude."
    ),
    replay: Path | None = typer.Option(
        None, "--replay", help="Run over a JSON Lines file instead of the DB."
    ),
    no_db: bool = typer.Option(False, "--no-db", help="Don't read or write the database."),
    config: Path | None = ConfigOpt,
) -> None:
    """Run detection engines on new observations (or a replay file)."""
    from anteater.detections import registry

    settings = _settings(config)
    try:
        names = registry.resolve([engines])
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None
    if no_db and replay is None:
        typer.secho(
            "live detection reads the database; use --replay FILE with --no-db",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    async def main() -> None:
        from anteater.pipeline import Pipeline, read_jsonl

        repo = None
        if not no_db:
            from anteater.db.repository import Repository
            from anteater.db.session import create_all

            repo = Repository.from_url(settings.general.db_url)
            await create_all(repo.engine)
        pipeline = Pipeline.from_settings(settings, _router(settings, repo), names)
        log.info("engines: %s", ", ".join(names))
        try:
            if replay is not None:
                for obs in read_jsonl(replay):
                    await pipeline.process(obs)
                log.info(
                    "replayed %d observations, %d findings",
                    pipeline.observations,
                    pipeline.findings,
                )
                return
            assert repo is not None
            cursor = (time.time() - 60, 0)
            while True:
                batch, cursor = await repo.observations_after(cursor)
                for obs in batch:
                    await pipeline.process(obs)
                if not batch:
                    await asyncio.sleep(1.0)
        finally:
            pipeline.close()
            if repo is not None:
                await repo.close()

    _run(main())


# ----------------------------------------------------------------- ui
@app.command()
def ui(
    port: int | None = typer.Option(None, "--port", "-p"),
    host: str | None = typer.Option(None, "--host", help="Bind address (default 127.0.0.1)."),
    detect: bool = typer.Option(
        False, "--detect", help="Run engines on observations POSTed to the API."
    ),
    config: Path | None = ConfigOpt,
) -> None:
    """Serve the dashboard and HTTP API."""
    import uvicorn

    from anteater.alerts.sinks import BroadcastSink
    from anteater.api.app import create_app
    from anteater.db.repository import Repository

    settings = _settings(config, api={"port": port, "host": host})
    repo = Repository.from_url(settings.general.db_url)
    bus = BroadcastSink()
    pipeline = None
    if detect:
        from anteater.pipeline import Pipeline

        router = _router(settings, repo)
        router.add(bus)
        pipeline = Pipeline.from_settings(settings, router)

    async def init() -> None:
        from anteater.db.session import create_all

        await create_all(repo.engine)

    asyncio.run(init())
    if settings.api.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "dashboard bound to %s: anyone on that network can read your findings",
            settings.api.host,
        )
    typer.echo(f"dashboard: http://{settings.api.host}:{settings.api.port}")
    uvicorn.run(
        create_app(settings, repo, pipeline, bus),
        host=settings.api.host,
        port=settings.api.port,
        log_level=settings.log_level.lower(),
    )


def main() -> None:
    """Entry point for ``python -m anteater``, the zipapp and the PyInstaller build."""
    try:
        app()
    except BrokenPipeError:  # e.g. `anteater fp list | head`
        sys.exit(0)


if __name__ == "__main__":
    main()
