"""Settings: defaults < config/anteater.toml < .env < environment < CLI flags.

Environment variables use ``ANTEATER_<SECTION>__<KEY>``, for example
``ANTEATER_KISMET__URL``. ``ANTEATER_LOG_LEVEL`` is the one top-level key.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

ENV_PREFIX = "ANTEATER_"
NESTED = "__"


class GeneralSettings(BaseModel):
    db_url: str = "sqlite+aiosqlite:///./anteater.db"
    grid_precision: int = Field(default=7, ge=4, le=9)
    data_dir: Path = Path("data")
    # geohash prefixes treated as "home": devices seen there don't count towards following
    home_cells: list[str] = Field(default_factory=list)
    allowlist: Path = Path("config/allowlist.yaml")


class KismetSettings(BaseModel):
    url: str = "ws://localhost:2501"
    user: str = "kismet"
    password: str = ""
    api_key: str = ""
    poll_seconds: float = Field(default=2.0, gt=0)

    @property
    def http_url(self) -> str:
        """REST base URL derived from the websocket URL."""
        url = self.url.rstrip("/")
        if url.startswith("ws://"):
            return "http://" + url[5:]
        if url.startswith("wss://"):
            return "https://" + url[6:]
        return url

    @property
    def ws_url(self) -> str:
        url = self.url.rstrip("/")
        if url.startswith("http://"):
            return "ws://" + url[7:]
        if url.startswith("https://"):
            return "wss://" + url[8:]
        return url


class AlertSettings(BaseModel):
    audio: bool = True
    video: bool = False
    tts: bool = True
    mqtt: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_topic: str = "anteater/alerts"
    min_severity: str = "low"
    dedupe_seconds: float = 300.0
    evidence_log: Path = Path("data/evidence.jsonl")
    clips_dir: Path = Path("clips")


class GpsSettings(BaseModel):
    source: str = "gpsd"  # gpsd | fixed | none
    host: str = "localhost"
    port: int = 2947
    fixed_lat: float | None = None
    fixed_lon: float | None = None


class DetectSettings(BaseModel):
    engines: list[str] = Field(default_factory=lambda: ["all"])
    follow_min_cells: int = 3
    follow_min_sessions: int = 2
    follow_min_minutes: float = 20.0
    ssid_warmup_seconds: float = 300.0


class TermiteSettings(BaseModel):
    itu_region: int = Field(default=2, ge=1, le=3)
    driver: str = ""
    sample_rate: float = 10e6
    gain_db: float = 30.0
    min_freq_hz: float = 433e6
    max_freq_hz: float = 15e9
    sweep_budget_s: float = 120.0


class ApiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080


class Settings(BaseModel):
    log_level: str = "INFO"
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    kismet: KismetSettings = Field(default_factory=KismetSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    gps: GpsSettings = Field(default_factory=GpsSettings)
    detect: DetectSettings = Field(default_factory=DetectSettings)
    termite: TermiteSettings = Field(default_factory=TermiteSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unknown log level {v!r}")
        return v


def _parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _coerce(value: str) -> Any:
    """Turn env strings into TOML-ish values so pydantic sees lists and bools."""
    low = value.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()] if inner else []
    return value


def _apply_env(data: dict[str, Any], env: dict[str, str]) -> None:
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX):
            continue
        name = key[len(ENV_PREFIX) :].lower()
        if NESTED in name:
            section, _, field = name.partition(NESTED)
            data.setdefault(section, {})[field] = _coerce(value)
        elif name in Settings.model_fields:
            data[name] = _coerce(value)


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        elif v is not None:
            base[k] = v


def load_settings(
    config_path: Path | None = None,
    env_file: Path | None = Path(".env"),
    overrides: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> Settings:
    """Build Settings from every layer. ``overrides`` uses {"section": {"key": value}}."""
    data: dict[str, Any] = {}
    path = config_path or Path(os.environ.get("ANTEATER_CONFIG", "config/anteater.toml"))
    if path.is_file():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    elif config_path is not None:
        raise FileNotFoundError(f"config file not found: {config_path}")

    if env_file is not None:
        _apply_env(data, _parse_dotenv(env_file))
    _apply_env(data, dict(os.environ if environ is None else environ))
    if overrides:
        _deep_merge(data, overrides)
    return Settings.model_validate(data)
