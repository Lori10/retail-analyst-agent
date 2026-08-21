import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    project_id: str
    gemini_api_key: str
    gemini_model: str
    max_bytes_billed: int
    row_limit: int
    query_timeout_seconds: float


def load_config() -> Config:
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
    )
