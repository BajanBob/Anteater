"""End-to-end: the synthetic scenario trips every engine, settings load, replay works."""

import importlib.util
import json

import pytest

from anteater.detections import registry
from anteater.pipeline import read_jsonl
from anteater.scoring.baseline import Baseline
from anteater.selftest import run_scenario, scenario
from anteater.settings import load_settings


async def test_every_engine_fires(settings):
    findings = await run_scenario(settings)
    fired = {f.engine for f in findings}
    missing = set(registry.ENGINES) - fired
    assert not missing, f"engines that never fired: {sorted(missing)}"


def test_engine_resolution():
    assert registry.resolve(["all"]) == list(registry.ENGINES)
    assert "radar" not in registry.resolve(["all,-radar"])
    assert registry.resolve(["drones", "radar"]) == ["drones", "radar"]
    with pytest.raises(ValueError):
        registry.resolve(["nope"])


def test_settings_layers(tmp_path):
    toml = tmp_path / "a.toml"
    toml.write_text('log_level = "debug"\n[kismet]\nurl = "ws://pi:2501"\n[general]\ngrid_precision = 6\n')
    env = tmp_path / ".env"
    env.write_text("ANTEATER_GENERAL__GRID_PRECISION=8\nANTEATER_ALERTS__AUDIO=false\n")
    s = load_settings(toml, env_file=env, environ={"ANTEATER_KISMET__USER": "bob"},
                      overrides={"kismet": {"password": "pw"}})
    assert s.log_level == "DEBUG"
    assert s.kismet.url == "ws://pi:2501" and s.kismet.http_url == "http://pi:2501"
    assert s.general.grid_precision == 8  # .env beats the TOML file
    assert s.alerts.audio is False
    assert s.kismet.user == "bob" and s.kismet.password == "pw"


def test_replay_file_roundtrip(tmp_path):
    path = tmp_path / "obs.jsonl"
    with path.open("w") as fh:
        fh.write("# comment\n\n")
        for o in scenario()[:5]:
            fh.write(json.dumps(o.to_dict()) + "\n")
        fh.write("not json\n")
    loaded = list(read_jsonl(path))
    assert len(loaded) == 5
    assert loaded[0].kind == scenario()[0].kind


def test_shipped_sample_replays():
    from anteater.paths import _REPO_DIR

    loaded = list(read_jsonl(_REPO_DIR / "tests" / "data" / "sample.jsonl"))
    assert len(loaded) >= 10


def test_baseline_persists(tmp_path):
    b = Baseline()
    assert b.record("aa", "gcpvj0", 1.0, "s1") is True
    assert b.record("aa", "gcpvj0", 2.0, "s2") is False
    path = tmp_path / "b.json"
    b.save(path)
    again = Baseline.load(path)
    assert again.is_normal("aa", "gcpvj0")
    assert again.cells_for("aa") == {"gcpvj0"}


@pytest.mark.skipif(importlib.util.find_spec("cv2") is None, reason="OpenCV not installed")
def test_hud_frame(settings):
    import asyncio

    from anteater.alerts.video import render_hud

    finding = asyncio.run(run_scenario(settings))[0]
    frame = render_hud(finding)
    assert frame.shape == (1080, 1920, 3) and frame.any()


def test_audio_motifs_render():
    from anteater.alerts.audio import MOTIFS, synth

    for motif in MOTIFS.values():
        buf = synth(motif)
        assert buf.dtype.name == "float32" and 0 < len(buf) < 22050 * 3
        assert abs(buf).max() <= 0.41


@pytest.mark.db
async def test_repository_roundtrip(tmp_path):
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")
    from anteater.db.repository import Repository
    from anteater.db.session import create_all

    repo = Repository.from_url(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    try:
        await create_all(repo.engine)
        items = scenario()
        await repo.add_observations(items)
        got, cursor = await repo.observations_after((0.0, 0), limit=7)
        rest, _ = await repo.observations_after(cursor, limit=1000)
        assert len(got) + len(rest) == len(items)
        stats = await repo.stats(since_s=10 * 86400)
        assert stats["observations"] == len(items)
    finally:
        await repo.close()


@pytest.mark.skipif(importlib.util.find_spec("fastapi") is None, reason="FastAPI not installed")
def test_api_ingest_and_health(settings):
    from fastapi.testclient import TestClient

    from anteater.alerts.router import AlertRouter
    from anteater.alerts.sinks import MemorySink
    from anteater.api.app import create_app
    from anteater.pipeline import Pipeline

    sink = MemorySink()
    pipeline = Pipeline.from_settings(settings, AlertRouter([sink], "info", 0))
    client = TestClient(create_app(settings, None, pipeline))
    assert client.get("/api/health").json()["pipeline"] is True
    payload = [o.to_dict() for o in scenario()]
    r = client.post("/api/observations", json=payload)
    assert r.status_code == 202 and r.json()["findings"] > 0
    assert client.post("/api/observations", json={"kind": "ble"}).status_code == 422
