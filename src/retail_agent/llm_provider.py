import logging
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from retail_agent.errors import ProviderAuthError, ProviderError, ProviderTransientError
from retail_agent.resilience import bounded_backoff

logger = logging.getLogger(__name__)


class Provider(Protocol):
    """Structural interface both `GeminiProvider` and `OpenRouterProvider`
    satisfy, so `graph.py` and `ProviderCircuitBreaker` can treat either one
    (or a breaker wrapping both) identically."""

    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        """Send the conversation so far to the model and return its reply.

        Args:
            contents: The running message history, in `google-genai`'s
                native `types.Content` shape.
            system_instruction: The system prompt for this call.
            tools: Function-calling tool schemas available to the model.

        Returns:
            The model's response, including any function call(s) it made.

        Raises:
            ProviderAuthError: The API key/credentials are invalid.
            ProviderTransientError: The call failed transiently on every
                retry attempt (rate limit or server error).
            ProviderError: Any other provider-side failure.
        """
        ...


def _classify_genai_error(exc: genai_errors.APIError) -> ProviderError:
    """Map a `google-genai` API error into a typed AgentError by HTTP status.

    Args:
        exc: The exception raised by the `google-genai` client.

    Returns:
        `ProviderAuthError` for 401/403, `ProviderTransientError` for 429 or
        5xx, otherwise the generic `ProviderError`.
    """
    if exc.code in (401, 403):
        return ProviderAuthError(str(exc))
    if exc.code == 429 or exc.code >= 500:
        return ProviderTransientError(str(exc))
    return ProviderError(str(exc))


class GeminiProvider:
    """Thin wrapper around google-genai. Built as one implementation behind a
    common shape (generate(contents, system_instruction, tools)) so an
    OpenRouter provider can be added later without touching the graph."""

    def __init__(self, api_key: str, model: str) -> None:
        """Initialize the provider with an AI Studio API key.

        Args:
            api_key: Gemini API key (AI Studio).
            model: Model name, e.g. `"gemini-3.6-flash"`.
        """
        self._client = genai.Client(api_key=api_key)
        self._model = model

    @bounded_backoff(retry_on=ProviderTransientError, attempts=2, logger=logger)
    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        """Send the conversation so far to Gemini and return its reply.

        Classifies failures via `_classify_genai_error` and retries a
        transient one once (via the `bounded_backoff` decorator) before
        letting it propagate.

        Args:
            contents: The running message history.
            system_instruction: The system prompt for this call.
            tools: Function-calling tool schemas available to the model.

        Returns:
            The raw `GenerateContentResponse` from `google-genai`.

        Raises:
            ProviderAuthError: The API key is invalid.
            ProviderTransientError: The call failed transiently on every
                retry attempt.
            ProviderError: Any other provider-side failure.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            return self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except genai_errors.APIError as exc:
            typed = _classify_genai_error(exc)
            logger.warning("provider_call_failed", extra={"provider": "gemini", "error_class": type(typed).__name__})
            raise typed from exc
        except (TimeoutError, ConnectionError) as exc:
            logger.warning("provider_call_failed", extra={"provider": "gemini", "error_class": "ProviderTransientError"})
            raise ProviderTransientError(str(exc)) from exc
