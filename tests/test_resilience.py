"""CircuitBreaker (resilience.py) and the dashboard's error wording (ui_messages.py)."""

import pytest

from errors import StorageError, UpstreamAuthError, UpstreamUnavailable
from resilience import CircuitBreaker, SourceCoolingDown
from ui_messages import describe


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Flaky:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)


def test_failure_opens_the_breaker_and_later_calls_skip_the_source():
    clock, source = Clock(), Flaky(StorageError("down"), "ok")
    breaker = CircuitBreaker(30, clock)

    with pytest.raises(StorageError):
        breaker.call(source)
    clock.now += 10
    with pytest.raises(SourceCoolingDown) as exc:
        breaker.call(source)

    assert source.calls == 1  # the dead source was not touched again
    assert isinstance(exc.value.last_error, StorageError)
    assert exc.value.retry_in_s == pytest.approx(20)


def test_breaker_closes_after_the_cooldown():
    clock, source = Clock(), Flaky(StorageError("down"), "ok")
    breaker = CircuitBreaker(30, clock)
    with pytest.raises(StorageError):
        breaker.call(source)

    clock.now += 31
    assert breaker.call(source) == "ok"
    assert breaker.call(lambda: "again") == "again"


def test_reset_closes_the_breaker_immediately():
    breaker = CircuitBreaker(30, Clock())
    with pytest.raises(StorageError):
        breaker.call(Flaky(StorageError("down")))
    breaker.reset()
    assert breaker.call(lambda: "ok") == "ok"


def test_programmer_errors_pass_through_without_opening_the_breaker():
    breaker = CircuitBreaker(30, Clock())
    with pytest.raises(KeyError):
        breaker.call(Flaky(KeyError("bug")))
    assert breaker.call(lambda: "ok") == "ok"


def test_ui_wording_hides_storage_details_and_explains_cooldowns():
    assert "10.0.0.5" not in describe(StorageError("host 10.0.0.5 refused"))
    assert "NASA_API_KEY" in describe(UpstreamAuthError("HTTP 403", status_code=403))
    assert describe(UpstreamUnavailable("Timed out")) == "Timed out"

    cooling = describe(SourceCoolingDown(StorageError("host 10.0.0.5"), 12.4))
    assert "database is unavailable" in cooling and "Retrying in 12s" in cooling and "10.0.0.5" not in cooling
