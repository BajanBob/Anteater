"""Command line: ``termite <command>``. Receive-only."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import typer

from anteater.logging_setup import setup_logging
from anteater.sdr_optional import HAVE_SOAPY, INSTALL_HINT, SdrUnavailable
from anteater.settings import load_settings
from termite import __version__

app = typer.Typer(
    name="termite",
    help="SDR sweeps for anteater (433 MHz - 15 GHz). Receive-only.",
    no_args_is_help=True,
    add_completion=False,
)
log = logging.getLogger("termite")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"termite {__version__}")


@app.command()
def devices() -> None:
    """List attached SoapySDR devices."""
    if not HAVE_SOAPY:
        typer.secho(INSTALL_HINT, fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    from termite.sdr.soapy import list_devices

    found = list_devices()
    if not found:
        typer.echo("no SDR devices found")
    for d in found:
        typer.echo(", ".join(f"{k}={v}" for k, v in sorted(d.items())))


@app.command()
def plan(
    region: int | None = typer.Option(
        None, "--region", "-r", min=1, max=3, help="ITU region (default from config)."
    ),
    budget: float | None = typer.Option(None, "--budget", "-b", help="Seconds per full sweep."),
    sample_rate: float | None = typer.Option(None, "--sample-rate", help="Hz."),
    max_freq: float | None = typer.Option(
        None, "--max-freq", help="Receiver ceiling in Hz (default 6 GHz)."
    ),
    qubo: bool = typer.Option(
        False, "--qubo", help="Allocate whole seconds with the QUBO planner."
    ),
) -> None:
    """Show the dwell plan for a sweep."""
    from termite.plan.allocator import allocate, load_bands

    s = load_settings().termite
    region = region or s.itu_region
    sr = sample_rate or s.sample_rate
    bands = load_bands(region)
    p = allocate(
        bands, budget or s.sweep_budget_s, sr, freq_range_hz=(s.min_freq_hz, max_freq or 6e9)
    )
    typer.echo(
        f"ITU Region {region}: {len(p.dwells)} bands, {p.seconds:.1f} s per sweep at {sr / 1e6:.1f} MS/s"
    )
    if qubo:
        from termite.plan.dwell_qubo import solve

        units = int(budget or s.sweep_budget_s)
        alloc = solve([d.band for d in p.dwells], units)
        for d in p.dwells:
            typer.echo(
                f"  {d.band.name:<38} {d.band.lo_mhz:>9.1f}-{d.band.hi_mhz:<9.1f} MHz  {alloc[d.band.name]:>3d} s"
            )
    else:
        for d in p.dwells:
            typer.echo(
                f"  {d.band.name:<38} {d.band.lo_mhz:>9.1f}-{d.band.hi_mhz:<9.1f} MHz  "
                f"{len(d.centres_hz):>3d} steps x {d.seconds_per_step:.2f} s"
            )
    for b, why in p.skipped:
        typer.secho(f"  skipped {b.name}: {why}", fg=typer.colors.YELLOW)


@app.command()
def scan(
    region: int | None = typer.Option(None, "--region", "-r", min=1, max=3),
    budget: float | None = typer.Option(None, "--budget", "-b", help="Seconds per sweep."),
    device: str = typer.Option("", "--device", "-d", help="SoapySDR args, e.g. driver=rtlsdr"),
    downconverter_lo: float = typer.Option(
        0.0, "--downconverter-lo", help="LO of a downconverter in Hz."
    ),
    min_snr: float = typer.Option(
        10.0, "--min-snr", help="Report carriers this far above the noise floor (dB)."
    ),
    sweeps: int = typer.Option(0, "--sweeps", help="Stop after N sweeps (0 = forever)."),
    post: str | None = typer.Option(
        None, "--post", help="anteater API, e.g. http://localhost:8080/api/observations"
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Append observations to a JSON Lines file."
    ),
    simulate: bool = typer.Option(
        False, "--simulate", help="Use a simulated receiver (no hardware)."
    ),
) -> None:
    """Sweep the band plan and report carriers as anteater observations."""
    from termite.plan.allocator import allocate, load_bands
    from termite.sdr.soapy import open_receiver
    from termite.sweep import sweep_once

    settings = load_settings()
    setup_logging(settings.log_level)
    s = settings.termite
    bands = load_bands(region or s.itu_region)
    client = None
    if post:
        import httpx

        client = httpx.Client(timeout=10)
    sink = out.open("a", encoding="utf-8") if out else None
    lat, lon = settings.gps.fixed_lat, settings.gps.fixed_lon

    try:
        with open_receiver(device or s.driver, s.sample_rate, s.gain_db, simulate=simulate) as rx:
            p = allocate(
                bands,
                budget or s.sweep_budget_s,
                rx.sample_rate,
                freq_range_hz=rx.freq_range(),
                downconverter_lo_hz=downconverter_lo,
            )
            for b, why in p.skipped:
                log.info("skipping %s: %s", b.name, why)
            n = 0
            while sweeps == 0 or n < sweeps:
                n += 1
                batch = sweep_once(rx, p, n, min_snr, downconverter_lo, lat, lon)
                log.info("sweep %d: %d carriers", n, len(batch))
                for item in batch:
                    if sink:
                        sink.write(json.dumps(item) + "\n")
                    else:
                        typer.echo(
                            f"{item['freq_mhz']:>12.3f} MHz  {item['extra']['snr_db']:>5.1f} dB  {item['extra']['band']}"
                        )
                if sink:
                    sink.flush()
                if client and post and batch:
                    try:
                        client.post(post, json=batch).raise_for_status()
                    except Exception as exc:
                        log.warning("post to anteater failed: %s", exc)
    except SdrUnavailable as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        pass
    finally:
        if sink:
            sink.close()
        if client:
            client.close()


@app.command()
def classify(
    train: Path | None = typer.Option(
        None, "--train", help="CSV: label,f1,...,f10 (header optional)."
    ),
    model: Path = typer.Option(Path("data/termite_rff.npz"), "--model"),
    predict: Path | None = typer.Option(None, "--predict", help="CSV of feature rows to label."),
) -> None:
    """Train or apply the RFF emitter classifier on feature vectors."""
    from termite.qml.kernel_rff import RFFClassifier

    def read(path: Path, labelled: bool) -> tuple[np.ndarray, np.ndarray | None]:
        rows = [ln.split(",") for ln in path.read_text().splitlines() if ln.strip()]
        if rows and not _is_number(rows[0][-1]):
            rows = rows[1:]
        if labelled:
            return np.array([[float(v) for v in r[1:]] for r in rows]), np.array(
                [r[0] for r in rows]
            )
        return np.array([[float(v) for v in r] for r in rows]), None

    if train:
        X, y = read(train, True)
        assert y is not None
        clf = RFFClassifier().fit(X, y)
        model.parent.mkdir(parents=True, exist_ok=True)
        clf.save(model)
        typer.echo(
            f"trained on {len(y)} samples, {len(set(y))} classes, train accuracy {clf.score(X, y):.1%} -> {model}"
        )
    if predict:
        clf = RFFClassifier.load(model)
        X, _ = read(predict, False)
        for label, probs in zip(clf.predict(X), clf.predict_proba(X), strict=True):
            typer.echo(f"{label}\t{probs.max():.2f}")
    if not train and not predict:
        typer.echo("nothing to do: pass --train and/or --predict")


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def main() -> None:
    app()


if __name__ == "__main__":
    main()
