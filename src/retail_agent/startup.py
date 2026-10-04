from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from retail_agent.bq_tool import BigQueryTool
    from retail_agent.reports_store import ReportsStore


class StartupError(Exception):
    """Raised when the agent can't be built at all (bad credentials, no
    network, etc.) — distinct from AgentError, which covers failures during
    a conversation turn after the agent is already running."""


def build_bq_tool(config) -> BigQueryTool:
    """Construct an authenticated `BigQueryTool` from config.

    Shared by the CLI and the MCP server (`mcp_server.py`). The BigQuery and
    google-auth imports happen here rather than at module level, so importing
    this module stays cheap — the MCP server must answer its stdio handshake
    before a client's connect timeout, and only builds this on the first
    tool call.

    Args:
        config: A loaded `Config`.

    Returns:
        A ready `BigQueryTool`.

    Raises:
        StartupError: BigQuery credentials are missing/invalid, or the
            client otherwise fails to construct.
    """
    from google.auth.exceptions import DefaultCredentialsError
    from google.cloud import bigquery

    from retail_agent.bq_tool import BigQueryTool

    try:
        client = bigquery.Client(project=config.project_id)
    except DefaultCredentialsError as exc:
        raise StartupError(
            "No Google Cloud credentials found. Run "
            "'gcloud auth application-default login' and try again."
        ) from exc
    except Exception as exc:
        raise StartupError(f"Could not connect to BigQuery: {exc}") from exc

    return BigQueryTool(
        client=client,
        max_bytes_billed=config.max_bytes_billed,
        row_limit=config.row_limit,
        timeout_seconds=config.query_timeout_seconds,
    )


def build_reports_store(config) -> ReportsStore:
    """Connect to the Saved Reports Store.

    Shared by the CLI and the MCP server (`mcp_server.py`); imports
    `psycopg` lazily for the same reason as `build_bq_tool`.

    Args:
        config: A loaded `Config`.

    Returns:
        A connected `ReportsStore`.

    Raises:
        StartupError: Postgres is unreachable or the connection fails.
    """
    from retail_agent.reports_store import ReportsStore

    try:
        return ReportsStore(config.reports_database_url)
    except Exception as exc:
        raise StartupError(
            f"Could not connect to the saved reports database: {exc}. Is Postgres running "
            "('docker compose up -d postgres')?"
        ) from exc
