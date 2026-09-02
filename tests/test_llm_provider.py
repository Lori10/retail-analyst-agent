import pytest
from langchain_core.exceptions import (
    ModelAPIError,
    ModelAuthenticationError,
    ModelInvalidRequestError,
    ModelPermissionDeniedError,
    ModelRateLimitError,
)
from langchain_core.messages import AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from retail_agent.errors import ProviderAuthError, ProviderError, ProviderTransientError
from retail_agent.llm_provider import GeminiProvider


class _Counter:
    def __init__(self):
        self.calls = 0


def _provider_with_script(monkeypatch, script):
    """Build a `GeminiProvider` whose underlying `ChatGoogleGenerativeAI.invoke`
    is replaced with a canned script, patched at the class level since the
    `.bind_tools(...)`-wrapped instance is a pydantic model that rejects
    arbitrary instance attribute assignment."""
    provider = GeminiProvider(api_key="fake-key", model="fake-model")
    script = list(script)
    counter = _Counter()

    def fake_invoke(self, *args, **kwargs):
        counter.calls += 1
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ChatGoogleGenerativeAI, "invoke", fake_invoke)
    return provider, counter


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(GeminiProvider._generate_raw.retry, "sleep", lambda seconds: None)


def test_auth_error_raises_provider_auth_error_without_retry(monkeypatch):
    provider, counter = _provider_with_script(monkeypatch, [ModelAuthenticationError("bad key")])
    with pytest.raises(ProviderAuthError):
        provider.generate(messages=[], system_instruction="x")
    assert counter.calls == 1


def test_permission_denied_raises_provider_auth_error_without_retry(monkeypatch):
    provider, counter = _provider_with_script(monkeypatch, [ModelPermissionDeniedError("forbidden")])
    with pytest.raises(ProviderAuthError):
        provider.generate(messages=[], system_instruction="x")
    assert counter.calls == 1


def test_rate_limit_is_retried_and_succeeds(monkeypatch):
    reply = AIMessage(content="ok")
    provider, counter = _provider_with_script(monkeypatch, [ModelRateLimitError("slow down"), reply])
    result = provider.generate(messages=[], system_instruction="x")
    assert result is reply
    assert counter.calls == 2


def test_server_error_exhausts_retries_and_raises_transient(monkeypatch):
    provider, counter = _provider_with_script(monkeypatch, [ModelAPIError("boom"), ModelAPIError("boom again")])
    with pytest.raises(ProviderTransientError):
        provider.generate(messages=[], system_instruction="x")
    assert counter.calls == 2


def test_invalid_request_error_raises_generic_provider_error_without_retry(monkeypatch):
    provider, counter = _provider_with_script(monkeypatch, [ModelInvalidRequestError("bad request")])
    with pytest.raises(ProviderError):
        provider.generate(messages=[], system_instruction="x")
    assert counter.calls == 1


def test_timeout_error_is_treated_as_transient(monkeypatch):
    reply = AIMessage(content="ok")
    provider, counter = _provider_with_script(monkeypatch, [TimeoutError("client timed out"), reply])
    result = provider.generate(messages=[], system_instruction="x")
    assert result is reply
    assert counter.calls == 2


def test_connection_error_is_treated_as_transient(monkeypatch):
    reply = AIMessage(content="ok")
    provider, counter = _provider_with_script(monkeypatch, [ConnectionError("connection reset"), reply])
    result = provider.generate(messages=[], system_instruction="x")
    assert result is reply
    assert counter.calls == 2


def test_timeout_error_exhausts_retries_and_raises_provider_transient_error(monkeypatch):
    provider, counter = _provider_with_script(
        monkeypatch, [TimeoutError("client timed out"), TimeoutError("client timed out again")]
    )
    with pytest.raises(ProviderTransientError):
        provider.generate(messages=[], system_instruction="x")
    assert counter.calls == 2


def test_connection_error_exhausts_retries_and_raises_provider_transient_error(monkeypatch):
    provider, counter = _provider_with_script(
        monkeypatch, [ConnectionError("connection reset"), ConnectionError("connection reset again")]
    )
    with pytest.raises(ProviderTransientError):
        provider.generate(messages=[], system_instruction="x")
    assert counter.calls == 2
