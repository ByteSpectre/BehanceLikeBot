from __future__ import annotations

import threading
import time


class StopRequested(Exception):
    pass


class SkipRequested(Exception):
    pass


class RunControl:
    """Thread-safe pause, skip and stop state for interruptible operations."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._paused = False
        self._stopped = False
        self._skip_requested = False

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused

    @property
    def stopped(self) -> bool:
        with self._condition:
            return self._stopped

    def pause(self) -> None:
        with self._condition:
            self._paused = True
            self._condition.notify_all()

    def resume(self) -> None:
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def skip(self) -> None:
        with self._condition:
            self._skip_requested = True
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._paused = False
            self._condition.notify_all()

    def checkpoint(self, allow_skip: bool = True) -> None:
        with self._condition:
            while self._paused and not self._stopped:
                self._condition.wait(timeout=0.25)
            if self._stopped:
                raise StopRequested
            if allow_skip and self._skip_requested:
                self._skip_requested = False
                raise SkipRequested

    def sleep(self, seconds: float, allow_skip: bool = True) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self.checkpoint(allow_skip=allow_skip)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            with self._condition:
                self._condition.wait(timeout=min(0.25, remaining))

    def consume_skip(self) -> bool:
        with self._condition:
            requested = self._skip_requested
            self._skip_requested = False
            return requested
