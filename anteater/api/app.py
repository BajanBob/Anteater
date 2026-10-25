"""Dashboard + HTTP API.

Routes:
  GET  /                    dashboard (ui/index.html)
  GET  /api/health          liveness and versions
  GET  /api/findings        recent findings (?limit=100&min_severity=low)
  GET  /api/stats           counts over the last 24 h (?window=86400)
  POST /api/observations    ingest one observation or a list (termite, phones, scripts)
  WS   /ws/alerts           live findings as JSON
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from anteater import __version__
from anteater.alerts.sinks import BroadcastSink
from anteater.db.repository import Repository
from anteater.detections.base import Observation
from anteater.paths import data_dir
from anteater.pipeline import Pipeline
from anteater.scoring.severity import Severity
from anteater.settings import Settings

log = logging.getLogger(__name__)
MAX_INGEST = 5000


def create_app(
    settings: Settings,
    repo: Repository | None = None,
    pipeline: Pipeline | None = None,
    broadcast: BroadcastSink | None = None,
    poll_db_s: float = 2.0,
) -> FastAPI:
    bus = broadcast or BroadcastSink()

    async def _poll_db() -> None:
        """Forward findings written by a separate `anteater detect` process."""
        assert repo is not None
        seen: set[str] = set()
        last = time.time() - 5
        while True:
            try:
                rows = await repo.recent_findings(limit=200)
                for row in reversed(rows):
                    if row["ts"] >= last and row["id"] not in seen:
                        seen.add(row["id"])
                        for q in list(bus.queues):
                            if q.full():
                                q.get_nowait()
                            q.put_nowait(row)
                if rows:
                    last = max(last, rows[0]["ts"] - 1)
                if len(seen) > 10_000:
                    seen.clear()
            except Exception as exc:
                log.debug("finding poll failed: %s", exc)
            await asyncio.sleep(poll_db_s)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        poller = asyncio.create_task(_poll_db()) if repo is not None and pipeline is None else None
        try:
            yield
        finally:
            if poller:
                poller.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller

    app = FastAPI(title="Anteater", version=__version__, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.repo = repo
    app.state.pipeline = pipeline
    app.state.bus = bus

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        try:
            return FileResponse(data_dir("ui") / "index.html")
        except FileNotFoundError:
            raise HTTPException(404, "dashboard files not found") from None

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "database": repo is not None,
            "pipeline": pipeline is not None,
            "time": time.time(),
        }

    @app.get("/api/findings")
    async def findings(limit: int = 100, min_severity: str = "info") -> list[dict[str, Any]]:
        if repo is None:
            raise HTTPException(503, "no database configured")
        try:
            sev = int(Severity.parse(min_severity))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return await repo.recent_findings(limit=max(1, min(limit, 1000)), min_severity=sev)

    @app.get("/api/stats")
    async def stats(window: float = 86400.0) -> dict[str, Any]:
        if repo is None:
            raise HTTPException(503, "no database configured")
        return await repo.stats(since_s=window)

    @app.post("/api/observations")
    async def ingest(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:  # includes json.JSONDecodeError
            raise HTTPException(400, "body must be JSON: one observation object or a list of them") from None
        items = body if isinstance(body, list) else [body]
        if len(items) > MAX_INGEST:
            raise HTTPException(413, f"at most {MAX_INGEST} observations per request")
        if not all(isinstance(x, dict) for x in items):
            raise HTTPException(422, "each observation must be a JSON object")
        try:
            observations = [Observation.from_dict(x) for x in items]
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"bad observation: {exc}") from None
        found = 0
        if pipeline is not None:
            for obs in observations:
                found += len(await pipeline.process(obs))
        if repo is not None:
            await repo.add_observations(observations)
        if pipeline is None and repo is None:
            raise HTTPException(503, "nothing to ingest into")
        return JSONResponse({"accepted": len(observations), "findings": found}, status_code=202)

    @app.websocket("/ws/alerts")
    async def alerts(ws: WebSocket) -> None:
        await ws.accept()
        q = bus.subscribe()
        try:
            while True:
                await ws.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(q)

    return app
