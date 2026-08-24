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
    """Retry decorator: exponential backoff, retrying only on `retry_on`,
    capped at `attempts` total tries. Any other exception propagates on the
    first occurrence. Used for failure modes where a bare retry (same
    request, no changes) can plausibly succeed — transient/timeout errors
    from BigQuery or an LLM provider — never for errors a retry can't fix.

    `sleep` defaults to a real sleep; tests pass a no-op to keep retry tests
    fast.
    """
    return tenacity.retry(
        retry=tenacity.retry_if_exception_type(retry_on),
        stop=tenacity.stop_after_attempt(attempts),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
        before_sleep=tenacity.before_sleep_log(logger, logging.INFO),
        sleep=sleep,
    )
