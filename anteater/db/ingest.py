"""Batched observation writer: collectors hand off quickly, the DB sees few big inserts."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from anteater.db.repository import Repository
from anteater.detections.base import Observation

log = logging.getLogger(__name__)


class BatchWriter:
    def __init__(self, repo: Repository, batch: int = 500, flush_s: float = 1.0, maxsize: int = 50_000) -> None:
        self.repo = repo
        self.batch = batch
        self.flush_s = flush_s
        self.queue: asyncio.Queue[Observation] = asyncio.Queue(maxsize)
        self.written = 0
        self.dropped = 0
        self._task: asyncio.Task[None] | None = None
        # Taken off the queue but not yet written. Kept on self so stop() can
        # still write them if the task is cancelled mid-batch.
        self._pending: list[Observation] = []

    def put(self, obs: Observation) -> None:
        try:
            self.queue.put_nowait(obs)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % 1000 == 1:
                log.warning("database is falling behind; dropped %d observations", self.dropped)

    async def _drain(self) -> None:
        """Move up to one batch from the queue into self._pending."""
        self._pending.append(await self.queue.get())
        deadline = asyncio.get_running_loop().time() + self.flush_s
        while len(self._pending) < self.batch:
            timeout = deadline - asyncio.get_running_loop().time()
            if timeout <= 0:
                break
            try:
                self._pending.append(await asyncio.wait_for(self.queue.get(), timeout))
            except TimeoutError:
                break

    async def _run(self, max_attempts: int = 3) -> None:
        attempts = 0
        while True:
            if not self._pending:
                await self._drain()
            try:
                self.written += await self.repo.add_observations(self._pending)
                self._pending, attempts = [], 0
            except Exception:
                attempts += 1
                if attempts >= max_attempts:
                    # a batch that keeps failing would block everything behind it
                    log.exception("dropping %d observations after %d failed writes", len(self._pending), attempts)
                    self.dropped += len(self._pending)
                    self._pending, attempts = [], 0
                else:
                    log.warning("write of %d observations failed (attempt %d), retrying",
                                len(self._pending), attempts)
                    await asyncio.sleep(2)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        rest, self._pending = self._pending, []
        while not self.queue.empty():
            rest.append(self.queue.get_nowait())
        if rest:
            self.written += await self.repo.add_observations(rest)
