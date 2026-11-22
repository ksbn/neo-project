"""A minimal circuit breaker for interactive callers.

The dashboard deliberately doesn't cache failures (so it recovers as soon as a
source is back), but Streamlit reruns the whole script on every widget
interaction. Without a breaker, every keystroke would pay every timeout again.
After a failure the breaker skips the source for ``cooldown_s`` seconds and
raises :class:`SourceCoolingDown` instead, which is instant.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

from planetary_defense.domain.errors import NeoError

T = TypeVar("T")


class SourceCoolingDown(NeoError):
    """A source failed recently and is being skipped until its cooldown ends."""

    def __init__(self, last_error: NeoError, retry_in_s: float) -> None:
        super().__init__(f"skipped for another {retry_in_s:.0f}s after: {last_error}")
        self.last_error = last_error
        self.retry_in_s = retry_in_s


class CircuitBreaker:
    """Opens after one failure and closes again after ``cooldown_s`` or :meth:`reset`.

    Only :class:`NeoError` opens it: a programmer error propagates untouched.
    Safe to share between Streamlit sessions (threads).
    """

    def __init__(self, cooldown_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._open_until = 0.0
        self._last_error: NeoError | None = None

    def call(self, fn: Callable[[], T]) -> T:
        with self._lock:
            remaining = self._open_until - self._clock()
            if remaining > 0 and self._last_error is not None:
                raise SourceCoolingDown(self._last_error, remaining)
        try:
            result = fn()
        except NeoError as exc:
            with self._lock:
                self._open_until = self._clock() + self._cooldown_s
                self._last_error = exc
            raise
        self.reset()
        return result

    def reset(self) -> None:
        with self._lock:
            self._open_until = 0.0
            self._last_error = None
