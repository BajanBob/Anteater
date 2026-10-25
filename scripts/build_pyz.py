#!/usr/bin/env python3
"""Build dist/anteater.pyz: a single executable file holding anteater, termite and their data.

It runs anywhere Python 3.11+ and the dependencies are installed
(`pip install anteater` or `pip install -r` the pyproject deps):

    ./dist/anteater.pyz selftest --no-radio
    python3 dist/anteater.pyz ui

For a fully self-contained binary with Python and every dependency inside,
use `make build` (PyInstaller) or the release workflow instead.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = '''import sys
from anteater.cli import main
if __name__ == "__main__":
    sys.argv[0] = "anteater"
    main()
'''


def main() -> int:
    out = ROOT / "dist" / "anteater.pyz"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        for pkg in ("anteater", "termite"):
            shutil.copytree(ROOT / pkg, stage / pkg, ignore=ignore)
        data = stage / "anteater" / "_data"
        shutil.copytree(ROOT / "config" / "vendors", data / "vendors")
        shutil.copytree(ROOT / "config" / "spectrum", data / "spectrum")
        shutil.copytree(ROOT / "ui", data / "ui")
        shutil.copytree(ROOT / "alembic", data / "alembic", ignore=ignore)
        shutil.copy2(ROOT / "config" / "oui.csv", data / "oui.csv")
        (stage / "__main__.py").write_text(MAIN, encoding="utf-8")
        zipapp.create_archive(stage, out, interpreter="/usr/bin/env python3", compressed=True)
    print(f"built {out} ({out.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
