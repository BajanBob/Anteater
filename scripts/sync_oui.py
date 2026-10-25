#!/usr/bin/env python3
"""Refresh config/oui.csv from the IEEE registries. Same as `anteater fp sync`.

Used by the fp-sync GitHub workflow, which commits the result weekly.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anteater.oui_sync import sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path("config/oui.csv"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        n = sync(args.dest)
    except Exception as exc:
        print(f"OUI sync failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {n:,} assignments to {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
