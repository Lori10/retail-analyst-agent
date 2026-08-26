import pytest

from retail_agent.circuit_breaker import ProviderCircuitBreaker
from retail_agent.errors import ProviderTransientError


class FakeProvider:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def generate(self, contents, system_instruction, tools):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_uses_primary_while_healthy():
    primary = FakeProvider(["ok-1", "ok-2"])
    fallback = FakeProvider([])
    breaker = ProviderCircuitBreaker(primary=primary, fallback=fallback)

    assert breaker.generate([], "x", []) == "ok-1"
    assert breaker.generate([], "x", []) == "ok-2"
    assert primary.calls == 2
    assert fallback.calls == 0


def test_opens_after_threshold_consecutive_failures_and_routes_to_fallback():
    primary = FakeProvider([ProviderTransientError("down"), ProviderTransientError("still down")])
    fallback = FakeProvider(["fallback-answer"])
    breaker = ProviderCircuitBreaker(primary=primary, fallback=fallback, failure_threshold=2)

    with pytest.raises(ProviderTransientError):
        breaker.generate([], "x", [])
    with pytest.raises(ProviderTransientError):
        breaker.generate([], "x", [])

    # breaker is now open — routes straight to fallback without touching primary
    assert breaker.generate([], "x", []) == "fallback-answer"
    assert primary.calls == 2
    assert fallback.calls == 1


def test_single_failure_below_threshold_does_not_open_the_breaker():
    primary = FakeProvider([ProviderTransientError("blip"), "recovered"])
    fallback = FakeProvider([])
    breaker = ProviderCircuitBreaker(primary=primary, fallback=fallback, failure_threshold=2)

    with pytest.raises(ProviderTransientError):
        breaker.generate([], "x", [])
    assert breaker.generate([], "x", []) == "recovered"
    assert primary.calls == 2
    assert fallback.calls == 0


def test_success_resets_the_consecutive_failure_count():
    primary = FakeProvider([ProviderTransientError("blip"), "ok", ProviderTransientError("blip-2"), "ok-2"])
    fallback = FakeProvider([])
    breaker = ProviderCircuitBreaker(primary=primary, fallback=fallback, failure_threshold=2)

    with pytest.raises(ProviderTransientError):
        breaker.generate([], "x", [])
    breaker.generate([], "x", [])  # success resets the streak
    with pytest.raises(ProviderTransientError):
        breaker.generate([], "x", [])
    # still below threshold since the streak was reset by the success above
    assert breaker.generate([], "x", []) == "ok-2"
    assert fallback.calls == 0


def test_cooldown_expires_and_primary_is_retried(monkeypatch):
    primary = FakeProvider([ProviderTransientError("down"), ProviderTransientError("down"), "recovered"])
    fallback = FakeProvider(["fallback-answer"])
    breaker = ProviderCircuitBreaker(primary=primary, fallback=fallback, failure_threshold=2, cooldown_seconds=30.0)

    clock = {"t": 1000.0}
    monkeypatch.setattr("retail_agent.circuit_breaker.time.monotonic", lambda: clock["t"])

    with pytest.raises(ProviderTransientError):
        breaker.generate([], "x", [])
    with pytest.raises(ProviderTransientError):
        breaker.generate([], "x", [])

    assert breaker.generate([], "x", []) == "fallback-answer"  # still within cooldown

    clock["t"] += 31.0  # cooldown has expired
    assert breaker.generate([], "x", []) == "recovered"
    assert primary.calls == 3
