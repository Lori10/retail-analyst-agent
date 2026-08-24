import logging

import pytest

from retail_agent.resilience import bounded_backoff

logger = logging.getLogger("test_resilience")


class TransientError(Exception):
    pass


class OtherError(Exception):
    pass


def test_retries_matching_exception_until_success():
    calls = {"n": 0}

    @bounded_backoff(retry_on=TransientError, attempts=2, logger=logger, sleep=lambda s: None)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TransientError("try again")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_gives_up_after_max_attempts():
    calls = {"n": 0}

    @bounded_backoff(retry_on=TransientError, attempts=2, logger=logger, sleep=lambda s: None)
    def always_fails():
        calls["n"] += 1
        raise TransientError("nope")

    with pytest.raises(TransientError):
        always_fails()
    assert calls["n"] == 2


def test_does_not_retry_non_matching_exception():
    calls = {"n": 0}

    @bounded_backoff(retry_on=TransientError, attempts=2, logger=logger, sleep=lambda s: None)
    def wrong_error():
        calls["n"] += 1
        raise OtherError("not transient")

    with pytest.raises(OtherError):
        wrong_error()
    assert calls["n"] == 1
