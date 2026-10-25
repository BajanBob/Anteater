"""Locate bundled data (signatures, band plans, UI, migrations).

Search order, first hit wins:

1. ``$ANTEATER_HOME/<name>``
2. ``./config/<name>`` or ``./<name>`` (a source checkout)
3. the PyInstaller bundle (``sys._MEIPASS``)
4. ``anteater/_data/<name>`` inside an installed wheel
5. the same, extracted to ``~/.cache/anteater`` when running from ``anteater.pyz``
"""

from __future__ import annotations

import os
import sys
import zipfile
import zipimport
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_REPO_DIR = _PKG_DIR.parent

# name -> (path inside a checkout, path inside the wheel / bundle)
_LAYOUT: dict[str, tuple[str, str]] = {
    "vendors": ("config/vendors", "_data/vendors"),
    "spectrum": ("config/spectrum", "_data/spectrum"),
    "ui": ("ui", "_data/ui"),
    "alembic": ("alembic", "_data/alembic"),
    "config": ("config", "_data"),
}


def _zip_data_root() -> Path | None:
    """When running from a .pyz, extract anteater/_data once to a cache dir and return it."""
    loader = globals().get("__loader__")
    if not isinstance(loader, zipimport.zipimporter):
        return None
    archive = Path(loader.archive)
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    target = cache / "anteater" / f"pyz-{int(archive.stat().st_mtime)}"
    marker = target / ".complete"
    if not marker.exists():
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                if member.startswith("anteater/_data/") and not member.endswith("/"):
                    zf.extract(member, target)
        marker.touch()
    return target / "anteater"


def _candidates(name: str) -> list[Path]:
    checkout, packaged = _LAYOUT[name]
    out: list[Path] = []
    home = os.environ.get("ANTEATER_HOME")
    if home:
        out.append(Path(home) / checkout)
    out.append(Path.cwd() / checkout)
    out.append(_REPO_DIR / checkout)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(Path(meipass) / checkout)
    out.append(_PKG_DIR / packaged)
    zip_root = _zip_data_root()
    if zip_root is not None:
        out.append(zip_root / packaged)
    return out


def data_dir(name: str) -> Path:
    """Return the first existing directory for ``name``, or raise FileNotFoundError."""
    for path in _candidates(name):
        if path.is_dir():
            return path
    tried = ", ".join(str(p) for p in _candidates(name))
    raise FileNotFoundError(f"cannot find '{name}' data; looked in: {tried}")


def config_file(filename: str) -> Path | None:
    """Return a file from the config directory if it exists."""
    try:
        path = data_dir("config") / filename
    except FileNotFoundError:
        return None
    return path if path.is_file() else None
