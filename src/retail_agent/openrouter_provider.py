import json
import logging

import openai
from google.genai import types

from retail_agent.errors import ProviderAuthError, ProviderError, ProviderTransientError
from retail_agent.resilience import bounded_backoff

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _to_openai_messages(system_instruction: str, contents: list[types.Content]) -> list[dict]:
    messages = [{"role": "system", "content": system_instruction}]
    for content in contents:
        text = "".join(part.text for part in content.parts if part.text)
        tool_calls = [
            {
                "id": part.function_call.id,
                "type": "function",
                "function": {
                    "name": part.function_call.name,
                    "arguments": json.dumps(part.function_call.args or {}),
                },
            }
            for part in content.parts
            if part.function_call is not None
        ]
        tool_responses = [
            {
                "role": "tool",
                "tool_call_id": part.function_response.id,
                "content": json.dumps(part.function_response.response),
            }
            for part in content.parts
            if part.function_response is not None
        ]

        if tool_responses:
            messages.extend(tool_responses)
            continue

        message = {"role": "assistant" if content.role == "model" else "user", "content": text or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        messages.append(message)
    return messages


def _to_openai_tools(tools: list[types.Tool]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": decl.name,
                "description": decl.description,
                "parameters": decl.parameters_json_schema,
            },
        }
        for tool in tools
        for decl in (tool.function_declarations or [])
    ]


def _classify_openai_error(exc: openai.OpenAIError) -> ProviderError:
    if isinstance(exc, openai.AuthenticationError):
        return ProviderAuthError(str(exc))
    if isinstance(exc, (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)):
        return ProviderTransientError(str(exc))
    if isinstance(exc, openai.APIStatusError) and (exc.status_code == 429 or exc.status_code >= 500):
        return ProviderTransientError(str(exc))
    return ProviderError(str(exc))


def _to_genai_response(response) -> types.GenerateContentResponse:
    message = response.choices[0].message
    parts = []
    if message.content:
        parts.append(types.Part(text=message.content))
    for tool_call in message.tool_calls or []:
        parts.append(
            types.Part(
                function_call=types.FunctionCall(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    args=json.loads(tool_call.function.arguments or "{}"),
                )
            )
        )
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


class OpenRouterProvider:
    """Fallback provider, same `generate(...)` shape as GeminiProvider.
    Talks to OpenRouter's OpenAI-compatible chat-completions endpoint via
    the openai SDK, translating to/from google-genai's `types.Content` shape
    so graph.py and the circuit breaker can treat both providers identically.
    """

    def __init__(self, api_key: str, model: str) -> None:
        # max_retries=0: bounded_backoff below is the sole retry policy, so
        # the SDK's own internal retries don't stack multiplicatively on top.
        self._client = openai.OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key, max_retries=0)
        self._model = model

    @bounded_backoff(retry_on=ProviderTransientError, attempts=2, logger=logger)
    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        messages = _to_openai_messages(system_instruction, contents)
        openai_tools = _to_openai_tools(tools)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=openai_tools or openai.NOT_GIVEN,
            )
        except openai.OpenAIError as exc:
            typed = _classify_openai_error(exc)
            logger.warning("provider_call_failed", extra={"provider": "openrouter", "error_class": type(typed).__name__})
            raise typed from exc
        return _to_genai_response(response)
