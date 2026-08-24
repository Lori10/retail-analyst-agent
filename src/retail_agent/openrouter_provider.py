import json
import logging

import openai
import tenacity
from google.genai import types

from retail_agent.errors import ProviderAuthError, ProviderError, ProviderTransientError
from retail_agent.resilience import bounded_backoff

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# The openai SDK maps every 5xx status to InternalServerError specifically
# (not just 500), so — unlike google-genai, which lumps all of 4xx/5xx into
# two generic types — these four are a complete, precise "transient" set by
# type alone: no separate status-code check is needed on top of them.
_TRANSIENT_OPENAI_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


def _to_openai_messages(system_instruction: str, contents: list[types.Content]) -> list[dict]:
    """Translate google-genai message history into OpenAI chat-completions
    `messages`.

    A tool-result `Content` in this codebase's graph always carries only
    `function_response` parts (never mixed with text), so it becomes one or
    more standalone `role: "tool"` messages rather than a single user/
    assistant message — OpenAI's format has no equivalent of "one turn,
    several tool results."

    Args:
        system_instruction: The system prompt, sent as the first message.
        contents: The running message history in `types.Content` shape.

    Returns:
        A list of OpenAI-shaped message dicts, ready for
        `chat.completions.create(messages=...)`.
    """
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
    """Translate google-genai `Tool`/`FunctionDeclaration` schemas into
    OpenAI's `tools` shape.

    Args:
        tools: The `types.Tool` list passed to `generate(...)`.

    Returns:
        A list of `{"type": "function", "function": {...}}` dicts, ready
        for `chat.completions.create(tools=...)`.
    """
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
    """Map an `openai` SDK exception into a typed AgentError.

    Args:
        exc: The exception raised by the `openai` client.

    Returns:
        `ProviderAuthError` for an authentication failure,
        `ProviderTransientError` for a rate limit, timeout, connection
        error, or server error (see `_TRANSIENT_OPENAI_EXCEPTIONS`),
        otherwise the generic `ProviderError`.
    """
    if isinstance(exc, openai.AuthenticationError):
        return ProviderAuthError(str(exc))
    if isinstance(exc, _TRANSIENT_OPENAI_EXCEPTIONS):
        return ProviderTransientError(str(exc))
    return ProviderError(str(exc))


def _to_genai_response(response) -> types.GenerateContentResponse:
    """Translate an OpenAI `ChatCompletion` into google-genai's response
    shape.

    Args:
        response: The `ChatCompletion` returned by
            `chat.completions.create(...)`.

    Returns:
        A `types.GenerateContentResponse` with a single candidate whose
        content mirrors the completion's text and/or tool calls.
    """
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
        """Initialize the OpenAI SDK client pointed at OpenRouter.

        Args:
            api_key: OpenRouter API key.
            model: OpenRouter model slug, e.g. `"openai/gpt-4o-mini"`.
        """
        # max_retries=0: bounded_backoff below is the sole retry policy, so
        # the SDK's own internal retries don't stack multiplicatively on top.
        self._client = openai.OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key, max_retries=0)
        self._model = model

    @bounded_backoff(retry=tenacity.retry_if_exception_type(_TRANSIENT_OPENAI_EXCEPTIONS), attempts=2, logger=logger)
    def _generate_raw(self, messages: list[dict], openai_tools: list[dict]):
        """Call OpenRouter directly, retrying a transient failure (any of
        `_TRANSIENT_OPENAI_EXCEPTIONS`) via the `bounded_backoff` decorator.
        Raises whatever the client itself raises, unclassified —
        classification happens once, in `generate`, after retries are
        resolved one way or the other.
        """
        return self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=openai_tools or openai.NOT_GIVEN,
        )

    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        """Send the conversation so far to OpenRouter and return its reply.

        Translates the request to and response from OpenAI's
        chat-completions shape. Transient failures are retried (see
        `_generate_raw`) before ever reaching the classification step here,
        so this only classifies what's left: an error `_generate_raw` never
        retried in the first place, or one that survived every retry
        attempt.

        Args:
            contents: The running message history.
            system_instruction: The system prompt for this call.
            tools: Function-calling tool schemas available to the model.

        Returns:
            The reply translated back into `types.GenerateContentResponse`.

        Raises:
            ProviderAuthError: The API key is invalid.
            ProviderTransientError: The call failed transiently on every
                retry attempt.
            ProviderError: Any other provider-side failure.
        """
        messages = _to_openai_messages(system_instruction, contents)
        openai_tools = _to_openai_tools(tools)
        try:
            response = self._generate_raw(messages, openai_tools)
        except openai.OpenAIError as exc:
            typed = _classify_openai_error(exc)
            logger.warning("provider_call_failed", extra={"provider": "openrouter", "error_class": type(typed).__name__})
            raise typed from exc
        return _to_genai_response(response)
