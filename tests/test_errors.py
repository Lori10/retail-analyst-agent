from retail_agent import errors
from retail_agent.errors import (
    AgentError,
    ConversationStoreError,
    ExternalToolError,
    ExternalToolUnavailableError,
    GuardrailBlockedError,
    ProviderAuthError,
    ProviderError,
    ProviderTransientError,
    QueryExecutionError,
    QueryPermissionError,
    QuerySyntaxError,
    QueryTooExpensiveError,
    QueryTransientError,
    ReportsStoreError,
    SQLSafetyError,
    graceful_message_for,
)

SELF_CORRECTABLE = {SQLSafetyError, QueryTooExpensiveError, QuerySyntaxError, ExternalToolError}
NOT_SELF_CORRECTABLE = {
    AgentError,
    QueryExecutionError,
    QueryPermissionError,
    QueryTransientError,
    ProviderError,
    ProviderTransientError,
    ProviderAuthError,
    GuardrailBlockedError,
    ReportsStoreError,
    ConversationStoreError,
    ExternalToolUnavailableError,
}


def test_self_correctable_flags_match_the_taxonomy():
    for cls in SELF_CORRECTABLE:
        assert cls.self_correctable is True, cls
    for cls in NOT_SELF_CORRECTABLE:
        assert cls.self_correctable is False, cls


def test_every_error_has_a_non_default_graceful_message_except_the_base_classes():
    # AgentError and QueryExecutionError intentionally inherit the base
    # message — they're catch-all/defensive classes, not specific failures.
    defaults_ok = {AgentError, QueryExecutionError}
    for cls in SELF_CORRECTABLE | NOT_SELF_CORRECTABLE:
        if cls in defaults_ok:
            continue
        assert cls.graceful_message != AgentError.graceful_message, cls


def test_query_subclasses_are_still_query_execution_errors():
    # bq_tool.py's typed subclasses must remain catchable via the pre-existing
    # QueryExecutionError/AgentError call sites (purely additive hierarchy).
    for cls in (QuerySyntaxError, QueryPermissionError, QueryTransientError):
        assert issubclass(cls, QueryExecutionError)
        assert issubclass(cls, AgentError)


def test_graceful_message_for_known_class():
    assert graceful_message_for("QueryPermissionError") == QueryPermissionError.graceful_message


def test_graceful_message_for_unknown_class_falls_back_to_agent_error():
    assert graceful_message_for("SomeFutureExceptionType") == AgentError.graceful_message


def test_all_public_error_classes_are_registered_in_the_lookup_table():
    public_error_classes = {
        obj
        for name, obj in vars(errors).items()
        if isinstance(obj, type) and issubclass(obj, AgentError) and not name.startswith("_")
    }
    assert public_error_classes == SELF_CORRECTABLE | NOT_SELF_CORRECTABLE
