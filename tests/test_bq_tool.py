import pandas as pd
import pytest

from retail_agent.bq_tool import BigQueryTool
from retail_agent.errors import QueryTooExpensiveError, SQLSafetyError


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


class FakeRunner:
    def __init__(self, client):
        self.client = client
        self.dataset_id = "bigquery-public-data.thelook_ecommerce"


def _make_tool(*, dry_run_bytes=100, rows=None, max_bytes_billed=1_000_000_000):
    if rows is None:
        rows = pd.DataFrame({"id": [1], "total_revenue": [10.0]})
    client = FakeClient(dry_run_bytes=dry_run_bytes, rows=rows)
    runner = FakeRunner(client)
    tool = BigQueryTool(runner=runner, max_bytes_billed=max_bytes_billed, row_limit=100, timeout_seconds=5)
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
