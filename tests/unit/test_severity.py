import pytest

from anteater.alerts.router import AlertRouter
from anteater.alerts.sinks import MemorySink
from anteater.detections.base import Finding
from anteater.scoring.severity import Severity, combine, escalate


def test_parse():
    assert Severity.parse("High") is Severity.HIGH
    assert Severity.parse(4) is Severity.CRITICAL
    assert Severity.parse(99) is Severity.CRITICAL
    with pytest.raises(ValueError):
        Severity.parse("loud")


@pytest.mark.parametrize(
    "score,level",
    [
        (0.0, Severity.INFO),
        (0.2, Severity.LOW),
        (0.5, Severity.MEDIUM),
        (0.7, Severity.HIGH),
        (0.95, Severity.CRITICAL),
    ],
)
def test_from_score(score, level):
    assert Severity.from_score(score) is level


def test_escalate_caps_at_high_and_never_lowers():
    assert escalate(Severity.LOW, 0) is Severity.LOW
    assert escalate(Severity.LOW, 3) is Severity.MEDIUM
    assert escalate(Severity.LOW, 30) is Severity.HIGH
    assert escalate(Severity.CRITICAL, 30) is Severity.CRITICAL


def test_combine_is_bounded_and_monotonic():
    assert combine([]) == 0.0
    assert combine([0.5, 0.5]) == pytest.approx(0.75)
    assert combine([0.9, 0.9, 0.9]) < 1.0
    assert combine([0.3, 0.6]) >= 0.6


async def test_router_filters_and_dedupes():
    sink = MemorySink()
    router = AlertRouter([sink], min_severity="low", dedupe_s=60)

    def f(sev, ts, title="t"):
        return Finding("e", sev, title, "aa:bb", ts=ts)

    assert not await router.route(f(Severity.INFO, 0))  # below threshold
    assert await router.route(f(Severity.LOW, 0))
    assert not await router.route(f(Severity.LOW, 10))  # duplicate
    assert await router.route(f(Severity.HIGH, 20))  # escalation goes through
    assert await router.route(f(Severity.LOW, 200))  # window expired
    assert await router.route(f(Severity.LOW, 201, "other"))  # different finding
    assert len(sink.findings) == 4


async def test_router_survives_a_broken_sink():
    class Broken:
        name = "broken"

        def emit(self, f):
            raise RuntimeError("boom")

    sink = MemorySink()
    router = AlertRouter([Broken(), sink], min_severity="info", dedupe_s=0)
    assert await router.route(Finding("e", Severity.LOW, "t", "s"))
    assert len(sink.findings) == 1
