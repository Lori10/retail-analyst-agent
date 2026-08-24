import logging
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from retail_agent.errors import ProviderAuthError, ProviderError, ProviderTransientError
from retail_agent.resilience import bounded_backoff

logger = logging.getLogger(__name__)


class Provider(Protocol):
    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse: ...


def _classify_genai_error(exc: genai_errors.APIError) -> ProviderError:
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
        self._client = genai.Client(api_key=api_key)
        self._model = model

    @bounded_backoff(retry_on=ProviderTransientError, attempts=2, logger=logger)
    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
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
