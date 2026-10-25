from __future__ import annotations

from pathlib import Path

import pytest

from anteater.detections.base import Context, Observation
from anteater.pipeline import build_context
from anteater.scoring.baseline import Baseline
from anteater.selftest import selftest_settings
from anteater.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return selftest_settings(tmp_path)


@pytest.fixture
def ctx(settings: Settings) -> Context:
    return build_context(settings, Baseline())


@pytest.fixture
def obs():
    """Factory: obs("ble", "aa:bb:...", ts=..., lat=..., ...)."""

    def make(kind: str, subject: str, **kw: object) -> Observation:
        return Observation(kind, subject, **kw)  # type: ignore[arg-type]

    return make
