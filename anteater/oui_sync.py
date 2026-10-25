"""Download the IEEE MAC address registries and merge them into config/oui.csv."""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SOURCES = {
    "MA-L": "https://standards-oui.ieee.org/oui/oui.csv",
    "MA-M": "https://standards-oui.ieee.org/oui28/mam.csv",
    "MA-S": "https://standards-oui.ieee.org/oui36/oui36.csv",
}
HEADER = ["Registry", "Assignment", "Organization Name", "Organization Address"]
# IEEE throttles clients without a descriptive user agent
USER_AGENT = "anteater-oui-sync/1.0 (+https://github.com/USER/anteater)"


def merge(csv_texts: dict[str, str]) -> list[list[str]]:
    rows: dict[str, list[str]] = {}
    for registry, text in csv_texts.items():
        for row in csv.DictReader(io.StringIO(text)):
            assignment = (row.get("Assignment") or "").strip().upper()
            org = (row.get("Organization Name") or "").strip()
            if not assignment or not org:
                continue
            rows[assignment] = [registry, assignment, org, (row.get("Organization Address") or "").strip()]
    return [rows[k] for k in sorted(rows)]


def write(rows: list[list[str]], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    tmp.replace(dest)


def sync(dest: Path, timeout: float = 60.0) -> int:
    """Fetch every registry and write ``dest``. Returns the number of assignments."""
    import httpx

    texts: dict[str, str] = {}
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for registry, url in SOURCES.items():
            log.info("fetching %s", url)
            r = client.get(url)
            r.raise_for_status()
            texts[registry] = r.text
    rows = merge(texts)
    if len(rows) < 10_000:
        raise RuntimeError(f"only {len(rows)} assignments downloaded; refusing to overwrite {dest}")
    write(rows, dest)
    return len(rows)
