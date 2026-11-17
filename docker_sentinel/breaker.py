"""Sliding-window crash tracking with a single-alert circuit breaker."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import NamedTuple


class CrashState(NamedTuple):
    """Result of recording a crash."""

    is_crash_loop: bool
    should_alert: bool


class CrashBreaker:
    """Track crashes per container and trip once a container exceeds its limit.

    A container is in a crash loop when it crashed more than ``max_restarts``
    times within ``window_sec`` seconds. The first crash that trips the breaker
    asks for an alert; later crashes inside the loop are suppressed until the
    container falls back under its limit.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._history: dict[str, list[float]] = {}
        self._tripped: set[str] = set()
        self._lock = threading.Lock()

    def record_crash(self, container_name: str, max_restarts: int, window_sec: int) -> CrashState:
        """Record a crash for ``container_name`` and return the breaker state."""
        now = self._clock()
        with self._lock:
            history = [t for t in self._history.get(container_name, []) if now - t <= window_sec]
            history.append(now)
            self._history[container_name] = history

            if len(history) > max_restarts:
                if container_name in self._tripped:
                    return CrashState(is_crash_loop=True, should_alert=False)
                self._tripped.add(container_name)
                return CrashState(is_crash_loop=True, should_alert=True)

            self._tripped.discard(container_name)
            return CrashState(is_crash_loop=False, should_alert=True)

    def is_tripped(self, container_name: str) -> bool:
        with self._lock:
            return container_name in self._tripped

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._tripped.clear()
