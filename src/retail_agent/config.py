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
        gemini_api_key: Gemini (AI Studio) API key.
        gemini_model: Gemini model name.
        max_bytes_billed: BigQuery dry-run byte cap.
        row_limit: Maximum rows fetched per query.
        query_timeout_seconds: BigQuery client-side wait timeout.
        openrouter_api_key: OpenRouter API key, or `None` to disable the
            fallback provider and circuit breaker entirely.
        openrouter_model: OpenRouter model slug.
        provider_failure_threshold: Consecutive Gemini failures before the
            circuit breaker opens.
        provider_cooldown_seconds: How long the breaker stays open before
            Gemini is tried again.
        log_level: Root log level passed to `logging.basicConfig`.
    """

    project_id: str
    gemini_api_key: str
    gemini_model: str
    max_bytes_billed: int
    row_limit: int
    query_timeout_seconds: float
    openrouter_api_key: str | None
    openrouter_model: str
    provider_failure_threshold: int
    provider_cooldown_seconds: float
    log_level: str


def load_config() -> Config:
    """Load and validate agent configuration from the environment.

    Reads `.env` (via `python-dotenv`) first, then `os.environ` — real
    environment variables always take precedence over `.env`. Every
    optional variable has a default; only `GOOGLE_CLOUD_PROJECT` and
    `GEMINI_API_KEY` are required.

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
    missing = [
        name
        for name, value in [
            ("GOOGLE_CLOUD_PROJECT", project_id),
            ("GEMINI_API_KEY", gemini_api_key),
        ]
        if not value
    ]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    return Config(
        project_id=project_id,
        gemini_api_key=gemini_api_key,
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        max_bytes_billed=int(os.environ.get("BQ_MAX_BYTES_BILLED", 1_000_000_000)),
        row_limit=int(os.environ.get("BQ_ROW_LIMIT", 500)),
        query_timeout_seconds=float(os.environ.get("BQ_QUERY_TIMEOUT_SECONDS", 30)),
        # OPENROUTER_API_KEY is optional by design: if unset, the CLI runs
        # Gemini-only with no circuit breaker (nowhere to fail over to).
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
        openrouter_model=os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        provider_failure_threshold=int(os.environ.get("PROVIDER_FAILURE_THRESHOLD", 2)),
        provider_cooldown_seconds=float(os.environ.get("PROVIDER_COOLDOWN_SECONDS", 60)),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
