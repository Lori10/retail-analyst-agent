import pytest
from google.genai import errors as genai_errors

from retail_agent.errors import ProviderAuthError, ProviderError, ProviderTransientError
from retail_agent.llm_provider import GeminiProvider


def _api_error(code):
    return genai_errors.APIError(code, {"error": {"message": "boom", "status": "ERROR"}})


class _Counter:
    def __init__(self):
        self.calls = 0


def _provider_with_script(script):
    provider = GeminiProvider(api_key="fake-key", model="fake-model")
    script = list(script)
    counter = _Counter()

    def fake_generate_content(model, contents, config):
        counter.calls += 1
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    provider._client.models.generate_content = fake_generate_content
    return provider, counter


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(GeminiProvider._generate_raw.retry, "sleep", lambda seconds: None)


def test_auth_error_raises_provider_auth_error_without_retry():
    provider, counter = _provider_with_script([_api_error(403)])
    with pytest.raises(ProviderAuthError):
        provider.generate(contents=[], system_instruction="x", tools=[])
    assert counter.calls == 1


def test_rate_limit_is_retried_and_succeeds():
    provider, counter = _provider_with_script([_api_error(429), "ok"])
    result = provider.generate(contents=[], system_instruction="x", tools=[])
    assert result == "ok"
    assert counter.calls == 2


def test_server_error_exhausts_retries_and_raises_transient():
    provider, counter = _provider_with_script([_api_error(500), _api_error(503)])
    with pytest.raises(ProviderTransientError):
        provider.generate(contents=[], system_instruction="x", tools=[])
    assert counter.calls == 2


def test_other_client_error_raises_generic_provider_error_without_retry():
    provider, counter = _provider_with_script([_api_error(400)])
    with pytest.raises(ProviderError):
        provider.generate(contents=[], system_instruction="x", tools=[])
    assert counter.calls == 1


def test_timeout_error_is_treated_as_transient():
    provider, counter = _provider_with_script([TimeoutError("client timed out"), "ok"])
    result = provider.generate(contents=[], system_instruction="x", tools=[])
    assert result == "ok"
    assert counter.calls == 2
