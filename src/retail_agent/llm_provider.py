import logging
from typing import Protocol

import tenacity
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


def _is_transient_status(code: int) -> bool:
    """Whether an HTTP status code represents a plausibly-transient failure.

    Args:
        code: HTTP status code from a provider response.

    Returns:
        True for 429 (rate limit) or any 5xx (server error).
    """
    return code == 429 or code >= 500


def _classify_genai_error(exc: genai_errors.APIError) -> ProviderError:
    """Map a `google-genai` API error into a typed AgentError by HTTP status.

    Args:
        exc: The exception raised by the `google-genai` client.

    Returns:
        `ProviderAuthError` for 401/403, `ProviderTransientError` for a
        transient status (see `_is_transient_status`), otherwise the
        generic `ProviderError`.
    """
    if exc.code in (401, 403):
        return ProviderAuthError(str(exc))
    if _is_transient_status(exc.code):
        return ProviderTransientError(str(exc))
    return ProviderError(str(exc))


def _is_transient_genai_error(exc: BaseException) -> bool:
    """Retry predicate for `bounded_backoff`.

    `google.genai.errors` lumps every 4xx into one `ClientError` type and
    every 5xx into one `ServerError` type — both only distinguishable by
    their `.code` attribute — so retryability here can't be expressed as a
    plain exception type the way it can for BigQuery or OpenRouter; it has
    to inspect the exception's contents.

    Args:
        exc: The exception raised by the `google-genai` client.

    Returns:
        True for a client-side timeout/connection error, or a
        `genai_errors.APIError` with a transient status code.
    """
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return isinstance(exc, genai_errors.APIError) and _is_transient_status(exc.code)


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

    @bounded_backoff(retry=tenacity.retry_if_exception(_is_transient_genai_error), attempts=2, logger=logger)
    def _generate_raw(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        """Call Gemini directly, retrying a transient failure (see
        `_is_transient_genai_error`) via the `bounded_backoff` decorator.

        Args:
            contents: The running message history.
            system_instruction: The system prompt for this call.
            tools: Function-calling tool schemas available to the model.

        Returns:
            The raw `GenerateContentResponse` from `google-genai`.

        Raises:
            Exception: Whatever the client itself raises, unclassified —
                classification happens once, in `generate`, after retries
                are resolved one way or the other.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        return self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        """Send the conversation so far to Gemini and return its reply.

        Transient failures are retried (see `_generate_raw`) before ever
        reaching the classification step here, so this only classifies
        what's left: an error `_generate_raw` never retried in the first
        place, or one that survived every retry attempt.

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
        try:
            return self._generate_raw(contents, system_instruction, tools)
        except genai_errors.APIError as exc:
            typed = _classify_genai_error(exc)
            logger.warning("provider_call_failed", extra={"provider": "gemini", "error_class": type(typed).__name__})
            raise typed from exc
        except (TimeoutError, ConnectionError) as exc:
            logger.warning("provider_call_failed", extra={"provider": "gemini", "error_class": "ProviderTransientError"})
            raise ProviderTransientError(str(exc)) from exc
