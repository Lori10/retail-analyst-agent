class AgentError(Exception):
    """Base class for errors the orchestrator handles gracefully (never a raw
    traceback to the user).

    `self_correctable` tells the graph whether retrying via a model-generated
    SQL rewrite could plausibly fix this failure; `graceful_message` is the
    user-facing text shown when a self-correct budget is exhausted or the
    error is terminal outright.
    """

    self_correctable: bool = False
    graceful_message: str = "Something went wrong on my end. Please try rephrasing your question."


class SQLSafetyError(AgentError):
    self_correctable = True
    graceful_message = "I couldn't put together a valid, safe query for that. Could you rephrase it?"


class QueryTooExpensiveError(AgentError):
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
    self_correctable = True
    graceful_message = (
        "I couldn't produce a valid query for that after a couple of tries. "
        "Could you rephrase or narrow the question?"
    )


class QueryPermissionError(QueryExecutionError):
    self_correctable = False
    graceful_message = "I don't have permission to access the data needed for that request."


class QueryTransientError(QueryExecutionError):
    self_correctable = False
    graceful_message = "BigQuery is having trouble responding right now. Please try again shortly."


class ProviderError(AgentError):
    self_correctable = False
    graceful_message = "I'm having trouble reaching the language model right now. Please try again."


class ProviderTransientError(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    graceful_message = "There's a configuration problem talking to the language model provider."


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
    )
}


def graceful_message_for(error_class_name: str) -> str:
    cls = _ERROR_CLASSES_BY_NAME.get(error_class_name, AgentError)
    return cls.graceful_message
