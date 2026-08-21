class AgentError(Exception):
    """Base class for errors the orchestrator handles gracefully (never a raw traceback to the user)."""


class SQLSafetyError(AgentError):
    pass


class QueryTooExpensiveError(AgentError):
    pass


class QueryExecutionError(AgentError):
    """Generic BigQuery execution failure; split into typed retry/permission/transient
    subclasses in the resilience-depth slice (see CLAUDE.md build order, step 2)."""
