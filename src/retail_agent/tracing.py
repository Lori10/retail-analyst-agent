import json
import logging
import sys

TRACE_LOGGER_NAME = "retail_agent.trace"

_trace_logger = logging.getLogger(TRACE_LOGGER_NAME)


def configure_tracing(destination: str) -> None:
    """Wire the trace logger to emit one Cloud-Logging-shaped JSON line per event.

    `destination` is `"stderr"`/`"stdout"` (or the conventional `"-"` for stdout)
    for the corresponding stream; any other value is treated as a file path
    (appended to, never truncated). Defaulting to stderr keeps the interactive
    REPL's own stdout output clean — Cloud Logging ingests JSON lines from either
    stream identically, so this is a local-UX choice, not a production
    requirement.

    Idempotent: clears any handler(s) from a prior call first, so re-calling this
    (e.g. across tests pointed at different destinations) never leaves events
    being written to more than one place at once.

    Args:
        destination: `"stderr"`, `"stdout"`/`"-"`, or a filesystem path.
    """
    for handler in list(_trace_logger.handlers):
        _trace_logger.removeHandler(handler)
        handler.close()

    if destination == "stdout" or destination == "-":
        handler = logging.StreamHandler(sys.stdout)
    elif destination == "stderr":
        handler = logging.StreamHandler(sys.stderr)
    else:
        handler = logging.FileHandler(destination, mode="a", encoding="utf-8")

    handler.setFormatter(logging.Formatter("%(message)s"))  # log_event owns the full JSON line
    _trace_logger.addHandler(handler)
    _trace_logger.setLevel(logging.INFO)
    _trace_logger.propagate = False  # never touches basicConfig's console handler


def log_event(event: str, **fields) -> None:
    """Emit one structured trace line: one LLM call, tool call, or turn.

    `severity`/`message` are Cloud Logging's reserved structured-log keys —
    `message` carries the event name (`"llm_call"`/`"tool_call"`/`"turn"`),
    `severity` is `"ERROR"` when `fields` includes a truthy `error_class`, else
    `"INFO"`. Every other field becomes part of the entry's jsonPayload.

    A safe no-op if `configure_tracing` was never called (an unconfigured logger
    with no handler simply drops the record) — this can be called from anywhere,
    including tests, without needing to set up a destination first.

    Args:
        event: The event type — `"llm_call"`, `"tool_call"`, or `"turn"`.
        **fields: Event-specific fields, merged into the payload after
            `severity`/`message`.
    """
    severity = "ERROR" if fields.get("error_class") else "INFO"
    payload = {"severity": severity, "message": event, **fields}
    _trace_logger.info(json.dumps(payload, default=str))
