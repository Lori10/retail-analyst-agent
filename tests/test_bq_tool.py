import pandas as pd
import pytest
from google.api_core import exceptions as gax

from retail_agent import bq_tool as bq_tool_module
from retail_agent.bq_tool import BigQueryTool
from retail_agent.errors import (
    QueryExecutionError,
    QueryPermissionError,
    QuerySyntaxError,
    QueryTooExpensiveError,
    QueryTransientError,
    SQLSafetyError,
)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # bq_tool._call_bq_raw is wrapped with a real exponential-backoff sleep
    # by default; tests that trigger a retry would otherwise take ~1s+ each.
    monkeypatch.setattr(bq_tool_module._call_bq_raw.retry, "sleep", lambda seconds: None)


class FakeJob:
    def __init__(self, *, total_bytes_processed=0, rows=None):
        self.total_bytes_processed = total_bytes_processed
        self._rows = rows

    def result(self, timeout=None, max_results=None):
        return self

    def to_dataframe(self):
        return self._rows


class FakeClient:
    def __init__(self, *, dry_run_bytes, rows):
        self.dry_run_bytes = dry_run_bytes
        self.rows = rows
        self.queries = []

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        if job_config is not None and job_config.dry_run:
            return FakeJob(total_bytes_processed=self.dry_run_bytes)
        return FakeJob(rows=self.rows)


class ScriptedClient:
    """A fake BQ client whose `query`/`get_table` calls raise or return in a
    pre-scripted sequence, one entry consumed per call — for exercising
    typed-error classification and the transient-error backoff path."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def _next(self):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def query(self, sql, job_config=None):
        return self._next()

    def get_table(self, table_ref):
        return self._next()


def _make_tool(*, dry_run_bytes=100, rows=None, max_bytes_billed=1_000_000_000):
    if rows is None:
        rows = pd.DataFrame({"id": [1], "total_revenue": [10.0]})
    client = FakeClient(dry_run_bytes=dry_run_bytes, rows=rows)
    tool = BigQueryTool(client=client, max_bytes_billed=max_bytes_billed, row_limit=100, timeout_seconds=5)
    return tool, client


def test_rejects_unsafe_sql_before_touching_bigquery():
    tool, client = _make_tool()
    with pytest.raises(SQLSafetyError):
        tool.run_query("DROP TABLE orders")
    assert client.queries == []


def test_rejects_query_over_the_byte_cap():
    tool, client = _make_tool(dry_run_bytes=2_000_000_000, max_bytes_billed=1_000_000_000)
    with pytest.raises(QueryTooExpensiveError):
        tool.run_query("SELECT * FROM order_items")
    # only the dry-run call happened, the real (billable) query never ran
    assert len(client.queries) == 1


def test_successful_query_returns_pii_stripped_dataframe():
    rows = pd.DataFrame({"id": [1], "email": ["a@b.com"], "total_revenue": [42.0]})
    tool, client = _make_tool(dry_run_bytes=100, rows=rows)
    result = tool.run_query("SELECT id, email, total_revenue FROM users")
    assert result["redacted_columns"] == ["email"]
    assert list(result["dataframe"].columns) == ["id", "total_revenue"]
    assert result["row_count"] == 1
    assert len(client.queries) == 2  # dry run + real run


def _tool_with_script(script):
    client = ScriptedClient(script)
    tool = BigQueryTool(client=client, max_bytes_billed=1_000_000_000, row_limit=100, timeout_seconds=5)
    return tool, client


def test_dry_run_bad_request_raises_query_syntax_error_without_retry():
    tool, client = _tool_with_script([gax.BadRequest("bad sql")])
    with pytest.raises(QuerySyntaxError):
        tool.run_query("SELECT 1")
    assert client.calls == 1  # syntax errors are not retried


def test_dry_run_forbidden_raises_query_permission_error():
    tool, client = _tool_with_script([gax.Forbidden("no access")])
    with pytest.raises(QueryPermissionError):
        tool.run_query("SELECT 1")
    assert client.calls == 1


def test_transient_execute_error_is_retried_and_succeeds():
    rows = pd.DataFrame({"id": [1]})
    tool, client = _tool_with_script(
        [
            FakeJob(total_bytes_processed=100),  # dry run
            gax.ServerError("temporary outage"),  # execute attempt 1
            FakeJob(rows=rows),  # execute attempt 2 (retry) succeeds
        ]
    )
    result = tool.run_query("SELECT 1")
    assert result["row_count"] == 1
    assert client.calls == 3


def test_transient_execute_error_exhausts_retries_and_raises():
    tool, client = _tool_with_script(
        [
            FakeJob(total_bytes_processed=100),  # dry run
            gax.ServerError("outage"),  # execute attempt 1
            gax.ServerError("still down"),  # execute attempt 2 — budget exhausted
        ]
    )
    with pytest.raises(QueryTransientError):
        tool.run_query("SELECT 1")
    assert client.calls == 3  # dry run + 2 execute attempts, no 3rd attempt


def test_unclassified_exception_raises_base_query_execution_error():
    tool, client = _tool_with_script([RuntimeError("mystery failure")])
    with pytest.raises(QueryExecutionError):
        tool.run_query("SELECT 1")
    assert client.calls == 1


def test_transient_error_that_recovers_on_retry_never_logs_bq_call_failed(caplog):
    # Classification/logging happens once, in _call_bq, only after
    # _call_bq_raw's retries are fully resolved — a transient error that
    # succeeds on retry should never be classified or logged as a failure.
    rows = pd.DataFrame({"id": [1]})
    tool, _ = _tool_with_script(
        [
            FakeJob(total_bytes_processed=100),
            gax.ServerError("temporary outage"),
            FakeJob(rows=rows),
        ]
    )
    with caplog.at_level("WARNING"):
        tool.run_query("SELECT 1")
    assert "bq_call_failed" not in caplog.text


def test_transient_error_that_exhausts_retries_logs_bq_call_failed_exactly_once(caplog):
    tool, _ = _tool_with_script(
        [
            FakeJob(total_bytes_processed=100),
            gax.ServerError("outage"),
            gax.ServerError("still down"),
        ]
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(QueryTransientError):
            tool.run_query("SELECT 1")
    assert caplog.text.count("bq_call_failed") == 1


def test_get_schema_not_found_raises_query_syntax_error():
    client = ScriptedClient([gax.NotFound("no such table")])
    tool = BigQueryTool(client=client, max_bytes_billed=1_000_000_000, row_limit=100, timeout_seconds=5)
    with pytest.raises(QuerySyntaxError):
        tool.get_schema("not_a_table")
