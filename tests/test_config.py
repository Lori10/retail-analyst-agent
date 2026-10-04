import pytest

from retail_agent import config as config_module
from retail_agent.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Keep a developer's real `.env` out of these tests."""
    monkeypatch.setattr(config_module, "load_dotenv", lambda: None)


def test_gemini_key_required_by_default(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_config()


def test_gemini_key_optional_when_not_required(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    config = load_config(require_gemini=False)

    assert config.project_id == "proj"
    assert config.gemini_api_key is None


def test_project_still_required_when_gemini_not_required(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with pytest.raises(ConfigError, match="GOOGLE_CLOUD_PROJECT"):
        load_config(require_gemini=False)
