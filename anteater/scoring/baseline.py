"""Per-grid-cell baselines: what is normally seen where, and where each device has been."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Seen:
    first: float
    last: float
    count: int = 1
    sessions: list[str] = field(default_factory=list)

    def touch(self, ts: float, session: str | None) -> None:
        self.first = min(self.first, ts)
        self.last = max(self.last, ts)
        self.count += 1
        if session and session not in self.sessions:
            self.sessions.append(session)
            # keep memory bounded on long-lived baselines
            del self.sessions[:-50]


@dataclass(slots=True)
class CellStats:
    observations: int = 0
    sessions: list[str] = field(default_factory=list)
    subjects: dict[str, Seen] = field(default_factory=dict)


class Baseline:
    """Learned picture of the RF environment.

    ``cells[geohash].subjects[subject]`` answers "is this normal here?" and
    ``tracks[subject][geohash]`` answers "where has this device been?".
    """

    def __init__(self, familiar_after_sessions: int = 3) -> None:
        self.familiar_after_sessions = familiar_after_sessions
        self.cells: dict[str, CellStats] = defaultdict(CellStats)
        self.tracks: dict[str, dict[str, Seen]] = defaultdict(dict)

    # ------------------------------------------------------------- update
    def record(self, subject: str, cell: str | None, ts: float, session: str | None) -> bool:
        """Record a sighting. Returns True if ``subject`` was new to ``cell``."""
        if not cell:
            return False
        stats = self.cells[cell]
        stats.observations += 1
        if session and session not in stats.sessions:
            stats.sessions.append(session)
            del stats.sessions[:-50]
        new = subject not in stats.subjects
        if new:
            stats.subjects[subject] = Seen(ts, ts, 1, [session] if session else [])
        else:
            stats.subjects[subject].touch(ts, session)
        track = self.tracks[subject]
        if cell in track:
            track[cell].touch(ts, session)
        else:
            track[cell] = Seen(ts, ts, 1, [session] if session else [])
        return new

    # -------------------------------------------------------------- query
    def is_familiar(self, cell: str | None) -> bool:
        return bool(cell) and len(self.cells[cell].sessions) >= self.familiar_after_sessions  # type: ignore[index]

    def is_normal(self, subject: str, cell: str | None) -> bool:
        """Seen in this cell in more than one session before."""
        if not cell or cell not in self.cells:
            return False
        seen = self.cells[cell].subjects.get(subject)
        return seen is not None and len(seen.sessions) >= 2

    def cells_for(self, subject: str, precision: int | None = None) -> set[str]:
        cells = self.tracks.get(subject, {}).keys()
        return {c[:precision] for c in cells} if precision else set(cells)

    def sessions_for(self, subject: str) -> set[str]:
        out: set[str] = set()
        for seen in self.tracks.get(subject, {}).values():
            out.update(seen.sessions)
        return out

    def span_seconds(self, subject: str) -> float:
        track = self.tracks.get(subject)
        if not track:
            return 0.0
        return max(s.last for s in track.values()) - min(s.first for s in track.values())

    # -------------------------------------------------------- persistence
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "familiar_after_sessions": self.familiar_after_sessions,
            "cells": {
                k: {
                    "observations": v.observations,
                    "sessions": v.sessions,
                    "subjects": {s: asdict(x) for s, x in v.subjects.items()},
                }
                for k, v in self.cells.items()
            },
            "tracks": {s: {c: asdict(x) for c, x in t.items()} for s, t in self.tracks.items()},
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Baseline:
        base = cls()
        if not path.is_file():
            return base
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("ignoring unreadable baseline %s: %s", path, exc)
            return base
        base.familiar_after_sessions = int(data.get("familiar_after_sessions", 3))
        for k, v in data.get("cells", {}).items():
            base.cells[k] = CellStats(
                observations=v["observations"],
                sessions=list(v["sessions"]),
                subjects={s: Seen(**x) for s, x in v["subjects"].items()},
            )
        for s, t in data.get("tracks", {}).items():
            base.tracks[s] = {c: Seen(**x) for c, x in t.items()}
        return base
