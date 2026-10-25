"""The detection pipeline: enrich -> baseline -> engines -> alerts.

Collectors, replay files and the HTTP ingest endpoint all feed observations
through :meth:`Pipeline.process`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path
from typing import Any

import yaml

from anteater.alerts.router import AlertRouter
from anteater.detections import registry
from anteater.detections.base import Context, Detector, Finding, Observation
from anteater.detections.fingerprints import FingerprintDB, OuiDB
from anteater.geo.grid import cell_of
from anteater.paths import config_file, data_dir
from anteater.scoring.baseline import Baseline
from anteater.scoring.severity import escalate
from anteater.settings import Settings

log = logging.getLogger(__name__)


def load_allowlist(path: Path) -> tuple[set[str], list[str]]:
    """Return (allowed subjects, extra home cells) from allowlist.yaml."""
    if not path.is_file():
        return set(), []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    subjects: set[str] = set()
    for item in doc.get("devices") or []:
        value = item.get("mac") or item.get("subject") if isinstance(item, dict) else item
        if value:
            subjects.add(str(value).lower())
    return subjects, [str(c) for c in doc.get("home_cells") or []]


def build_context(settings: Settings, baseline: Baseline | None = None) -> Context:
    try:
        vendors = data_dir("vendors")
    except FileNotFoundError:
        log.warning("no vendor fingerprints found; fingerprint engine will only use heuristics")
        vendors = None
    allowed, extra_home = load_allowlist(settings.general.allowlist)
    if baseline is None:
        baseline = Baseline.load(settings.general.data_dir / "baseline.json")
    return Context(
        settings=settings,
        baseline=baseline,
        oui=OuiDB.load(config_file("oui.csv")),
        fingerprints=FingerprintDB.load_dir(vendors),
        allowlist=allowed,
        home_cells=tuple(settings.general.home_cells) + tuple(extra_home),
    )


class Pipeline:
    def __init__(self, ctx: Context, engines: Iterable[Detector], router: AlertRouter) -> None:
        self.ctx = ctx
        self.engines = list(engines)
        self.router = router
        self.observations = 0
        self.findings = 0
        self._last_save = time.monotonic()

    @classmethod
    def from_settings(
        cls, settings: Settings, router: AlertRouter, engines: Iterable[str] | None = None
    ) -> Pipeline:
        ctx = build_context(settings)
        return cls(ctx, registry.build(ctx, engines or settings.detect.engines), router)

    def enrich(self, obs: Observation) -> Observation:
        if obs.session is None:
            obs.session = self.ctx.session
        if obs.cell is None:
            obs.cell = cell_of(obs.lat, obs.lon, self.ctx.settings.general.grid_precision)
        if obs.vendor is None and obs.kind in ("wifi_ap", "wifi_client", "ble"):
            obs.vendor = self.ctx.oui.lookup(obs.subject)
        return obs

    async def process(self, obs: Observation) -> list[Finding]:
        self.observations += 1
        obs = self.enrich(obs)
        if self.ctx.is_allowed(obs.subject):
            return []
        new_here = self.ctx.baseline.record(obs.subject, obs.cell, obs.ts, obs.session)
        obs.extra["_new_in_cell"] = new_here

        emitted: list[Finding] = []
        for engine in self.engines:
            if not engine.accepts(obs):
                continue
            try:
                results = engine.process(obs)
            except Exception:
                log.exception("engine %s failed on %s", engine.name, obs.subject)
                continue
            for f in results:
                repeats = self.ctx.flagged.get(f.subject, 0)
                f.severity = escalate(f.severity, repeats)
                self.ctx.flagged[f.subject] = repeats + 1
                if await self.router.route(f):
                    emitted.append(f)
        obs.extra.pop("_new_in_cell", None)
        self.findings += len(emitted)
        self._maybe_save()
        return emitted

    async def run(self, source: AsyncIterator[Observation]) -> None:
        async for obs in source:
            await self.process(obs)

    def _maybe_save(self, force: bool = False) -> None:
        if force or time.monotonic() - self._last_save > 60:
            self._last_save = time.monotonic()
            try:
                self.ctx.baseline.save(self.ctx.settings.general.data_dir / "baseline.json")
            except OSError as exc:
                log.warning("cannot save baseline: %s", exc)

    def close(self) -> None:
        self._maybe_save(force=True)
        self.router.close()


# --------------------------------------------------------------- replay
def read_jsonl(path: Path) -> Iterator[Observation]:
    """Observations from a JSON Lines file. Blank lines and # comments are skipped."""
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data: dict[str, Any] = json.loads(line)
                yield Observation.from_dict(data)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                log.warning("%s:%d: skipped: %s", path, n, exc)
