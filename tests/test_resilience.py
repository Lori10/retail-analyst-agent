import logging

import pytest
import tenacity

from retail_agent.resilience import bounded_backoff

logger = logging.getLogger("test_resilience")


class TransientError(Exception):
    pass


class OtherError(Exception):
    pass


def test_retries_matching_exception_until_success():
    calls = {"n": 0}

    @bounded_backoff(retry=tenacity.retry_if_exception_type(TransientError), attempts=2, logger=logger, sleep=lambda s: None)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TransientError("try again")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_gives_up_after_max_attempts():
    calls = {"n": 0}

    @bounded_backoff(retry=tenacity.retry_if_exception_type(TransientError), attempts=2, logger=logger, sleep=lambda s: None)
    def always_fails():
        calls["n"] += 1
        raise TransientError("nope")

    with pytest.raises(TransientError):
        always_fails()
    assert calls["n"] == 2


def test_does_not_retry_non_matching_exception():
    calls = {"n": 0}

    @bounded_backoff(retry=tenacity.retry_if_exception_type(TransientError), attempts=2, logger=logger, sleep=lambda s: None)
    def wrong_error():
        calls["n"] += 1
        raise OtherError("not transient")

    with pytest.raises(OtherError):
        wrong_error()
    assert calls["n"] == 1


def test_retry_accepts_a_predicate_condition_not_just_a_type():
    # retry_if_exception_type can't distinguish "retryable" from "not" when
    # a client raises the same exception type for both (e.g. google-genai's
    # ClientError covering every 4xx) — bounded_backoff's `retry` param
    # accepts any tenacity retry condition, including a predicate over the
    # exception's contents, not just its type.
    calls = {"n": 0}

    def is_retryable(exc):
        return getattr(exc, "code", None) == 429

    class FlakyError(Exception):
        def __init__(self, code):
            super().__init__(str(code))
            self.code = code

    @bounded_backoff(retry=tenacity.retry_if_exception(is_retryable), attempts=2, logger=logger, sleep=lambda s: None)
    def sometimes_retryable():
        calls["n"] += 1
        raise FlakyError(429 if calls["n"] == 1 else 400)

    with pytest.raises(FlakyError) as exc_info:
        sometimes_retryable()
    assert exc_info.value.code == 400
    assert calls["n"] == 2
