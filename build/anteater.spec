# PyInstaller spec: one self-contained `anteater` executable (termite included as
# `anteater termite ...`). Build from the repo root:
#
#     pip install -e ".[dev,alerts]"
#     python -m PyInstaller --noconfirm --clean build/anteater.spec
#
# Output: dist/anteater (dist/anteater.exe on Windows).
# -*- mode: python -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is injected by PyInstaller)
BUILD = ROOT / "build"

datas = [
    (str(ROOT / "config" / "vendors"), "config/vendors"),
    (str(ROOT / "config" / "spectrum"), "config/spectrum"),
    (str(ROOT / "config" / "oui.csv"), "config"),
    (str(ROOT / "config" / "anteater.example.toml"), "config"),
    (str(ROOT / "ui"), "ui"),
    (str(ROOT / "alembic"), "alembic"),
]

hiddenimports = (
    collect_submodules("anteater")
    + collect_submodules("termite")
    + collect_submodules("uvicorn")
    + collect_submodules("alembic")
    + [
        "aiosqlite",
        "asyncpg",
        "sqlalchemy.dialects.sqlite.aiosqlite",
        "sqlalchemy.dialects.postgresql.asyncpg",
        "paho.mqtt.client",
        "orjson",
    ]
)

if sys.platform == "win32":
    icon = str(BUILD / "icons" / "anteater.ico")
elif sys.platform == "darwin":
    icon = str(BUILD / "icons" / "anteater.icns")
else:
    icon = None

a = Analysis(  # noqa: F821
    [str(ROOT / "anteater" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(BUILD / "hooks")],
    runtime_hooks=[str(BUILD / "hooks" / "rthook-soapy.py")],
    excludes=["tkinter", "matplotlib", "pandas", "IPython", "pytest", "mypy"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="anteater",
    console=True,
    strip=False,
    upx=False,
    icon=icon,
    version=str(BUILD / "windows" / "version_info.txt") if sys.platform == "win32" else None,
    entitlements_file=str(BUILD / "macos" / "entitlements.plist") if sys.platform == "darwin" else None,
    codesign_identity=None,
)
