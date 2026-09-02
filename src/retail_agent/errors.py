class AgentError(Exception):
    """Base class for errors the orchestrator handles gracefully (never a raw
    traceback to the user).

    `self_correctable` tells the graph whether retrying via a model-generated
    SQL rewrite could plausibly fix this failure; `graceful_message` is the
    user-facing text shown when a self-correct budget is exhausted or the
    error is terminal outright.

    Attributes:
        self_correctable: Whether a model-driven SQL rewrite could plausibly
            fix this failure. Defaults to False; subclasses opt in explicitly.
        graceful_message: User-facing text shown when this error reaches the
            graph's `give_up` node.
    """

    self_correctable: bool = False
    graceful_message: str = "Something went wrong on my end. Please try rephrasing your question."


class SQLSafetyError(AgentError):
    """Raised by `sql_safety.check_read_only` when a query fails the
    read-only keyword/shape allowlist (e.g. contains DML/DDL, multiple
    statements, or `SELECT *` on `users`)."""

    self_correctable = True
    graceful_message = "I couldn't put together a valid, safe query for that. Could you rephrase it?"


class QueryTooExpensiveError(AgentError):
    """Raised by `BigQueryTool.run_query` when a query's dry-run byte
    estimate exceeds the configured `max_bytes_billed` cap."""

    self_correctable = True
    graceful_message = (
        "I couldn't find a way to answer that within the query cost limit — "
        "try narrowing the date range or the fields you're asking about."
    )


class QueryExecutionError(AgentError):
    """Unclassified BigQuery execution failure — defensive fallback only.

    Every failure `bq_tool.py` can actually name is raised as one of the
    typed subclasses below; this base class is reached only for an exception
    type nobody has classified yet, and is treated as terminal until it is.
    """

    self_correctable = False


class QuerySyntaxError(QueryExecutionError):
    """Raised by `bq_tool._classify` for a BigQuery `BadRequest`, `NotFound`,
    or `Conflict` — a malformed query or a reference to a nonexistent table
    or column, either of which a rewritten query could plausibly fix."""

    self_correctable = True
    graceful_message = (
        "I couldn't produce a valid query for that after a couple of tries. "
        "Could you rephrase or narrow the question?"
    )


class QueryPermissionError(QueryExecutionError):
    """Raised by `bq_tool._classify` for a BigQuery `Forbidden` or
    `Unauthorized` — an IAM grant is missing, which no query rewrite can
    fix."""

    self_correctable = False
    graceful_message = "I don't have permission to access the data needed for that request."


class QueryTransientError(QueryExecutionError):
    """Raised by `bq_tool._classify` for a BigQuery `ServerError`,
    `TooManyRequests`, `RetryError`, or a client-side timeout — reaching
    the graph means `resilience.bounded_backoff` already retried and failed,
    so a further attempt via the model can't succeed either."""

    self_correctable = False
    graceful_message = "BigQuery is having trouble responding right now. Please try again shortly."


class ProviderError(AgentError):
    """Base class for LLM provider (Gemini/OpenRouter) call failures that
    aren't more specifically classified below."""

    self_correctable = False
    graceful_message = "I'm having trouble reaching the language model right now. Please try again."


class ProviderTransientError(ProviderError):
    """Raised for a provider rate-limit (HTTP 429) or server error (5xx).
    Reaching the graph means `resilience.bounded_backoff` already retried
    and failed; the `ProviderCircuitBreaker` counts these toward failing
    over to the fallback provider."""


class ProviderAuthError(ProviderError):
    """Raised for a provider authentication/authorization failure
    (HTTP 401/403) — a misconfigured or revoked API key, not a transient
    condition."""

    graceful_message = "There's a configuration problem talking to the language model provider."


class GuardrailBlockedError(AgentError):
    """Raised by `guardrail.check_user_input` when a user message matches a
    known prompt-injection/jailbreak pattern.

    Caught before any model or tool call happens — no query rewrite could
    ever fix malicious intent in the request itself, so this is never
    self-correctable."""

    self_correctable = False
    graceful_message = (
        "I can only help with analysis questions about our sales data — "
        "I can't follow instructions embedded in a request like that."
    )


_ERROR_CLASSES_BY_NAME = {
    cls.__name__: cls
    for cls in (
        AgentError,
        SQLSafetyError,
        QueryTooExpensiveError,
        QueryExecutionError,
        QuerySyntaxError,
        QueryPermissionError,
        QueryTransientError,
        ProviderError,
        ProviderTransientError,
        ProviderAuthError,
        GuardrailBlockedError,
    )
}


def graceful_message_for(error_class_name: str) -> str:
    """Look up the user-facing message for an error class by name.

    Looked up by class name (a string) rather than the class object itself
    because that's the shape the data arrives in at the call site:
    `graph.call_tools` stores `type(exc).__name__` in state, not a live
    exception instance.

    Args:
        error_class_name: The `__name__` of an `AgentError` subclass, e.g.
            `"QueryPermissionError"`.

    Returns:
        The matching class's `graceful_message`, or `AgentError`'s generic
        message if `error_class_name` isn't registered (e.g. a class added
        without updating `_ERROR_CLASSES_BY_NAME`).
    """
    cls = _ERROR_CLASSES_BY_NAME.get(error_class_name, AgentError)
    return cls.graceful_message
