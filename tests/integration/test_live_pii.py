"""Regression tests against real BigQuery for the PII stripping guarantee.

These exist because the fake-client unit tests in tests/test_bq_tool.py
cannot catch a live schema drift or a real BigQuery API quirk — both
happened once already during development (see the Skeleton Ledger):
postal_code/user_geom were missing from the PII registry until a live
schema dump surfaced them, and the default_dataset bug only showed up
against a real query. This suite re-runs that exact class of check.

Skipped unless GOOGLE_CLOUD_PROJECT and GEMINI_API_KEY are set as real
exported environment variables (a .env file alone is not enough — see
conftest.py) — so `uv run pytest` never runs these by accident, and a
clean checkout or CI without live credentials never fails here.
"""

import os

import pytest
from google.cloud import bigquery

from retail_agent.bq_tool import BigQueryTool
from retail_agent.config import load_config
from retail_agent.pii import PII_COLUMNS

pytestmark = pytest.mark.skipif(
    not (os.environ.get("GOOGLE_CLOUD_PROJECT") and os.environ.get("GEMINI_API_KEY")),
    reason="requires live GOOGLE_CLOUD_PROJECT + GEMINI_API_KEY credentials",
)


@pytest.fixture(scope="module")
def bq_tool():
    config = load_config()
    client = bigquery.Client(project=config.project_id)
    return BigQueryTool(
        client=client,
        max_bytes_billed=config.max_bytes_billed,
        row_limit=config.row_limit,
        timeout_seconds=config.query_timeout_seconds,
    )


def test_live_users_schema_never_exposes_pii_columns(bq_tool):
    schema = bq_tool.get_schema("users")
    names = {field["name"].lower() for field in schema}
    assert names.isdisjoint(PII_COLUMNS)
    assert "id" in names  # sanity: the call actually hit the real table


def test_live_query_strips_pii_even_when_explicitly_selected(bq_tool):
    result = bq_tool.run_query(
        "SELECT id, email, first_name, last_name, street_address, "
        "latitude, longitude, postal_code, user_geom FROM users LIMIT 5"
    )
    assert set(result["redacted_columns"]) == PII_COLUMNS
    assert list(result["dataframe"].columns) == ["id"]
    assert result["row_count"] == 5


def test_live_aggregate_query_still_works_end_to_end(bq_tool):
    result = bq_tool.run_query(
        "SELECT COUNT(*) AS n FROM orders"
    )
    assert result["row_count"] == 1
    assert result["dataframe"]["n"].iloc[0] > 0
