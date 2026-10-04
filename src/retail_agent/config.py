import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised by `load_config` when a required environment variable is
    missing. Distinct from `AgentError` — this is a startup failure, before
    any conversation turn exists to handle gracefully."""


@dataclass(frozen=True)
class Config:
    """Fully-resolved agent configuration, loaded once at startup.

    Attributes:
        project_id: GCP project ID/number BigQuery queries run against.
        gemini_api_key: Gemini (AI Studio) API key. `None` only when
            loaded with `require_gemini=False` (the MCP server, which never
            calls Gemini).
        gemini_model: Gemini model name.
        max_bytes_billed: BigQuery dry-run byte cap.
        row_limit: Maximum rows fetched per query.
        query_timeout_seconds: BigQuery client-side wait timeout.
        log_level: Root log level passed to `logging.basicConfig`.
        reports_database_url: Postgres connection string for the Saved
            Reports store.
        trace_log_destination: Where structured JSON tracing events
            (docs/design.md §3 Observability) are written — `"stderr"`/
            `"stdout"`, or a filesystem path. See `tracing.configure_tracing`.
    """

    project_id: str
    gemini_api_key: str | None
    gemini_model: str
    max_bytes_billed: int
    row_limit: int
    query_timeout_seconds: float
    log_level: str
    reports_database_url: str
    trace_log_destination: str


def load_config(require_gemini: bool = True) -> Config:
    """Load and validate agent configuration from the environment.

    Reads `.env` (via `python-dotenv`) first, then `os.environ` — real
    environment variables always take precedence over `.env`. Every
    optional variable has a default; only `GOOGLE_CLOUD_PROJECT` and
    `GEMINI_API_KEY` are required.

    Args:
        require_gemini: Whether `GEMINI_API_KEY` is required. The MCP server
            (`mcp_server.py`) passes `False` — it exposes the data tools to
            an external client's own model and never calls Gemini itself.

    Returns:
        A populated `Config`.

    Raises:
        ConfigError: One or both required environment variables are unset;
            names every missing one at once rather than failing on the
            first.
    """
    load_dotenv()  # values already in the environment take precedence over .env

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    required = [("GOOGLE_CLOUD_PROJECT", project_id)]
    if require_gemini:
        required.append(("GEMINI_API_KEY", gemini_api_key))
    missing = [name for name, value in required if not value]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    return Config(
        project_id=project_id,
        gemini_api_key=gemini_api_key,
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        max_bytes_billed=int(os.environ.get("BQ_MAX_BYTES_BILLED", 1_000_000_000)),
        row_limit=int(os.environ.get("BQ_ROW_LIMIT", 500)),
        query_timeout_seconds=float(os.environ.get("BQ_QUERY_TIMEOUT_SECONDS", 30)),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        reports_database_url=os.environ.get(
            "REPORTS_DATABASE_URL", "postgresql://retail_agent:retail_agent@localhost:5432/retail_agent_reports"
        ),
        trace_log_destination=os.environ.get("TRACE_LOG_DESTINATION", "stderr"),
    )
