import logging
from typing import Callable

import tenacity


def bounded_backoff(
    *,
    retry_on: type[BaseException],
    attempts: int,
    logger: logging.Logger,
    sleep: Callable[[int | float], None] = tenacity.nap.sleep,
):
    """Build a retry decorator: exponential backoff, retrying only on
    `retry_on`, capped at `attempts` total tries. Any other exception
    propagates on the first occurrence. Used for failure modes where a bare
    retry (same request, no changes) can plausibly succeed — transient/
    timeout errors from BigQuery or an LLM provider — never for errors a
    retry can't fix.

    Args:
        retry_on: Exception type (or tuple of types) that triggers a retry;
            any other exception propagates immediately.
        attempts: Maximum total attempts, including the first.
        logger: Logger used to record each retry via `before_sleep_log`.
        sleep: Sleep function called between attempts. Defaults to a real
            sleep; tests pass a no-op to keep retry tests fast — tenacity's
            own default binds to `tenacity.nap.sleep` at decoration time, so
            monkeypatching that module attribute afterward has no effect,
            while an injected callable here does.

    Returns:
        A decorator suitable for wrapping a function or method whose retry
        semantics should follow this policy.
    """
    return tenacity.retry(
        retry=tenacity.retry_if_exception_type(retry_on),
        stop=tenacity.stop_after_attempt(attempts),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
        before_sleep=tenacity.before_sleep_log(logger, logging.INFO),
        sleep=sleep,
    )
