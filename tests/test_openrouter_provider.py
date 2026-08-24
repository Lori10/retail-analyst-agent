from types import SimpleNamespace

import httpx2
import openai
import pytest
from google.genai import types

from retail_agent.errors import ProviderAuthError, ProviderError, ProviderTransientError
from retail_agent.openrouter_provider import OpenRouterProvider


def _http_error(cls, status_code, message="boom"):
    response = httpx2.Response(status_code, request=httpx2.Request("POST", "https://openrouter.ai/api/v1"))
    return cls(message, response=response, body=None)


def _chat_completion(*, text=None, tool_calls=None):
    message = SimpleNamespace(content=text, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _Counter:
    def __init__(self):
        self.calls = 0


def _provider_with_script(script):
    provider = OpenRouterProvider(api_key="fake-key", model="fake-model")
    script = list(script)
    counter = _Counter()

    def fake_create(model, messages, tools):
        counter.calls += 1
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    provider._client.chat.completions.create = fake_create
    return provider, counter


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(OpenRouterProvider._generate_raw.retry, "sleep", lambda seconds: None)


def _user_content(text):
    return [types.Content(role="user", parts=[types.Part(text=text)])]


def test_auth_error_raises_provider_auth_error_without_retry():
    provider, counter = _provider_with_script([_http_error(openai.AuthenticationError, 401)])
    with pytest.raises(ProviderAuthError):
        provider.generate(contents=_user_content("hi"), system_instruction="x", tools=[])
    assert counter.calls == 1


def test_rate_limit_is_retried_and_succeeds():
    provider, counter = _provider_with_script(
        [_http_error(openai.RateLimitError, 429), _chat_completion(text="ok")]
    )
    result = provider.generate(contents=_user_content("hi"), system_instruction="x", tools=[])
    assert result.candidates[0].content.parts[0].text == "ok"
    assert counter.calls == 2


def test_server_error_exhausts_retries_and_raises_transient():
    provider, counter = _provider_with_script(
        [_http_error(openai.InternalServerError, 500), _http_error(openai.InternalServerError, 503)]
    )
    with pytest.raises(ProviderTransientError):
        provider.generate(contents=_user_content("hi"), system_instruction="x", tools=[])
    assert counter.calls == 2


def test_other_status_error_raises_generic_provider_error_without_retry():
    provider, counter = _provider_with_script([_http_error(openai.BadRequestError, 400)])
    with pytest.raises(ProviderError):
        provider.generate(contents=_user_content("hi"), system_instruction="x", tools=[])
    assert counter.calls == 1


def test_tool_call_response_translates_to_genai_function_call():
    tool_call = SimpleNamespace(
        id="call-1", function=SimpleNamespace(name="run_query", arguments='{"sql": "SELECT 1"}')
    )
    provider, counter = _provider_with_script([_chat_completion(tool_calls=[tool_call])])
    result = provider.generate(contents=_user_content("total revenue?"), system_instruction="x", tools=[])
    fc = result.candidates[0].content.parts[0].function_call
    assert fc.id == "call-1"
    assert fc.name == "run_query"
    assert fc.args == {"sql": "SELECT 1"}


def test_function_response_translates_to_tool_message():
    contents = [
        types.Content(role="user", parts=[types.Part(text="total revenue?")]),
        types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id="call-1", name="run_query", args={"sql": "SELECT 1"}))],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(id="call-1", name="run_query", response={"row_count": 1})
                )
            ],
        ),
    ]
    provider, counter = _provider_with_script([_chat_completion(text="Revenue is 42.")])

    captured = {}
    original_create = provider._client.chat.completions.create

    def capturing_create(model, messages, tools):
        captured["messages"] = messages
        return original_create(model=model, messages=messages, tools=tools)

    provider._client.chat.completions.create = capturing_create

    provider.generate(contents=contents, system_instruction="x", tools=[])

    tool_messages = [m for m in captured["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call-1"
    assert '"row_count": 1' in tool_messages[0]["content"]
