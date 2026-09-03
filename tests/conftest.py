import json
import logging

import pytest

from retail_agent.tracing import TRACE_LOGGER_NAME


class _TraceCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.events.append(json.loads(record.getMessage()))


@pytest.fixture
def trace_events():
    """Capture structured trace events (`retail_agent.trace`) emitted during a test.

    Attaches directly to the trace logger rather than relying on pytest's
    `caplog` fixture, which only captures records that propagate up to the
    root logger — `tracing.configure_tracing` deliberately sets
    `propagate = False` on this logger (so production trace output never
    mixes with the app's plain-text console logs), which means `caplog`
    alone never sees these records regardless of `configure_tracing` having
    been called.

    Fully isolates the logger for the duration of the test — any handler(s)
    left behind by a prior test's `configure_tracing(...)` call (e.g. a
    `FileHandler` pointed at another test's now-torn-down `tmp_path`) are
    removed and restored afterward, so trace events from this test can never
    leak into a stale destination.

    Yields:
        A list of parsed JSON payloads (dicts), appended to in emission
        order as events are logged during the test.
    """
    logger = logging.getLogger(TRACE_LOGGER_NAME)
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    previous_propagate = logger.propagate
    for previous_handler in previous_handlers:
        logger.removeHandler(previous_handler)

    handler = _TraceCapture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler.events
    finally:
        logger.removeHandler(handler)
        for previous_handler in previous_handlers:
            logger.addHandler(previous_handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
