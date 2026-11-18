"""Recursive SQL (trail, MAC chains, timeline) and recursive grid helpers."""

import os
import sys
import time

import pytest

from anteater.detections.base import Observation
from anteater.geo import grid

pytest.importorskip("aiosqlite")

from anteater.db.repository import Repository  # noqa: E402
from anteater.db.session import create_all  # noqa: E402

PG_URL = os.environ.get(
    "ANTEATER_TEST_PG_URL", ""
)  # e.g. postgresql+asyncpg://postgres@127.0.0.1/t


@pytest.fixture(params=["sqlite", "postgresql"])
async def repo(request, tmp_path):
    if request.param == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path / 't.db'}"
    elif PG_URL:
        url = PG_URL
    else:
        pytest.skip("set ANTEATER_TEST_PG_URL to also test on PostgreSQL")
    r = Repository.from_url(url)
    await create_all(r.engine)
    yield r
    if request.param == "postgresql":
        from anteater.db.models import Base

        async with r.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await r.close()


def ob(subject, ts, cell=None, ssid=None, kind="wifi", rssi=-60.0):
    lat = lon = None
    if cell:
        lat, lon = grid.decode(cell)
    return Observation(
        kind=kind, subject=subject, ts=ts, ssid=ssid, cell=cell, lat=lat, lon=lon, rssi=rssi
    )


# ---------------------------------------------------------------- SQL
async def test_trail_groups_consecutive_cells_into_legs(repo):
    now = time.time() - 600
    cells = ["d7q8n", "d7q8n", "d7q8p", "d7q8p", "d7q8p", "d7q8n"]
    await repo.add_observations([ob("aa:bb", now + i * 10, c) for i, c in enumerate(cells)])
    await repo.add_observations([ob("other", now, "zzzzz")])
    legs = await repo.device_trail("aa:bb", since_s=3600)
    assert [(leg["leg"], leg["cell"], leg["sightings"]) for leg in legs] == [
        (1, "d7q8n", 2),
        (2, "d7q8p", 3),
        (3, "d7q8n", 1),
    ]
    assert legs[1]["stay_s"] == pytest.approx(20, abs=0.01)


async def test_trail_respects_step_limit(repo):
    now = time.time() - 600
    await repo.add_observations(
        [ob("x", now + i, "d7q8n" if i % 2 else "d7q8p") for i in range(50)]
    )
    legs = await repo.device_trail("x", since_s=3600, max_steps=10)
    assert sum(leg["sightings"] for leg in legs) == 10


async def test_rotation_chains_link_rotating_macs(repo):
    t = time.time() - 1800
    rows = []
    # one phone: three random MACs, each starts ~30 s after the last stops
    for k, mac in enumerate(["r1", "r2", "r3"]):
        rows += [ob(mac, t + k * 200 + i * 10, "d7q8n", ssid="HomeNet") for i in range(17)]
    # same SSID but much later: a different visit, not linked
    rows += [ob("late", t + 1500, "d7q8n", ssid="HomeNet")]
    # same timing, different SSID: not linked
    rows += [ob("stranger", t + 190, "d7q8n", ssid="CoffeeShop")]
    # same SSID and timing but another cell: not linked
    rows += [ob("far", t + 365, "s0000", ssid="HomeNet")]
    await repo.add_observations(rows)
    chains = await repo.rotation_chains(since_s=3600, gap_s=120)
    assert chains[0]["subjects"] == ["r1", "r2", "r3"]
    assert chains[0]["ssid"] == "HomeNet"
    assert all("late" not in c["subjects"] for c in chains)
    assert all("stranger" not in c["subjects"] for c in chains)


async def test_rotation_chain_depth_is_capped(repo):
    t = time.time() - 3000
    rows = [ob(f"m{k:02d}", t + k * 20, "d7q8n", ssid="S") for k in range(30)]
    await repo.add_observations(rows)
    chains = await repo.rotation_chains(since_s=3600, gap_s=60, max_depth=5)
    assert chains[0]["length"] == 6  # root plus five hops


async def test_timeline_includes_silent_slots(repo):
    now = time.time()
    await repo.add_observations([ob("a", now - 250), ob("b", now - 245), ob("a", now - 30)])
    slots = await repo.timeline(since_s=300, width_s=60)
    assert len(slots) == 5
    counts = [s["sightings"] for s in slots]
    assert sum(counts) == 3
    assert 0 in counts  # quiet minutes are reported, not dropped
    assert max(s["devices"] for s in slots) == 2


async def test_cell_counts_feed_hotspots(repo):
    now = time.time() - 60
    await repo.add_observations(
        [ob("a", now, "d7q8n"), ob("b", now, "d7q8n"), ob("c", now, "d7q8p"), ob("d", now, "s0000")]
    )
    spots = grid.hotspots(await repo.cell_counts(since_s=3600), min_total=2)
    assert spots[0]["cells"] == ["d7q8n", "d7q8p"]
    assert spots[0]["total"] == 3


# ---------------------------------------------------------------- Python
def test_children_are_one_level_finer():
    kids = grid.children("d7q")
    assert len(kids) == 32 and all(k.startswith("d7q") and len(k) == 4 for k in kids)


def test_rollup_builds_nested_counts():
    tree = grid.rollup({"d7q8n": 2, "d7q8p": 3, "d7qb0": 1, "s0000": 4}, precision=2)
    top = {n["cell"]: n for n in tree}
    assert top["d7"]["count"] == 6 and top["s0"]["count"] == 4
    d7q = top["d7"]["children"][0]
    assert d7q["cell"] == "d7q" and d7q["count"] == 6
    assert [c["cell"] for c in d7q["children"]] == ["d7q8", "d7qb"]
    leaf = d7q["children"][0]["children"][0]
    assert leaf == {"cell": "d7q8n", "count": 2, "children": []}


def test_clusters_split_disconnected_groups():
    a = "d7q8n"
    ring = grid.neighbours(a)
    far = grid.encode(-33.9, 151.2, 5)
    groups = grid.clusters([a, *ring[:3], far])
    assert len(groups) == 2
    assert len(groups[0]) == 4 and groups[1] == [far]


def test_clusters_survive_huge_regions_without_stack_overflow():
    # a solid 60 x 60 block at precision 6 = 3,600 touching cells
    lat0, lon0 = 13.0, -59.7
    lat_lo, lon_lo, lat_hi, lon_hi = grid.bounds(grid.encode(lat0, lon0, 6))
    dlat, dlon = lat_hi - lat_lo, lon_hi - lon_lo
    cells = {grid.encode(lat0 + i * dlat, lon0 + j * dlon, 6) for i in range(60) for j in range(60)}
    assert len(cells) > sys.getrecursionlimit()
    groups = grid.clusters(cells)
    assert len(groups) == 1 and len(groups[0]) == len(cells)


async def test_api_exposes_recursive_endpoints(tmp_path, settings):
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    from anteater.api.app import create_app

    r = Repository.from_url(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    await create_all(r.engine)
    t = time.time() - 900
    await r.add_observations(
        [ob("p1", t + i * 10, "d7q8n", ssid="H") for i in range(5)]
        + [ob("p2", t + 100 + i * 10, "d7q8p", ssid="H") for i in range(5)]
    )
    app = create_app(settings, repo=r, poll_db_s=3600)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        legs = (await c.get("/api/trail/p2")).json()
        assert legs[0]["cell"] == "d7q8p"
        assert (await c.get("/api/chains?gap=60")).json() == []  # different cells
        assert len((await c.get("/api/timeline?window=600&slot=60")).json()) == 10
        assert (await c.get("/api/hotspots?min_devices=2")).json()[0]["total"] == 2
        assert (await c.get("/api/timeline?window=1e9&slot=1")).status_code == 400
    await r.close()
