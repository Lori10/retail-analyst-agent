import logging

import tenacity
from langchain_core.exceptions import ModelAuthenticationError, ModelError, ModelPermissionDeniedError
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from retail_agent.errors import ProviderAuthError, ProviderError, ProviderTransientError
from retail_agent.resilience import bounded_backoff
from retail_agent.tools import TOOLS

logger = logging.getLogger(__name__)


def _is_transient_error(exc: Exception) -> bool:
    """Whether `exc` looks like a condition another attempt could clear.

    `langchain-google-genai` classifies every HTTP-level failure into a
    `langchain_core.exceptions.ModelError` subclass carrying its own
    `is_retryable` flag (rate limits and 5xx are retryable; auth/permission/
    invalid-request/not-found are not) — a real provider-agnostic taxonomy,
    not the raw `.code`-attribute juggling the old `google-genai`-only
    classifier needed. A bare `TimeoutError`/`ConnectionError` (a failure
    before any HTTP response — DNS, network down) never reaches that
    classification at all, so it's checked for directly, same as before.

    Args:
        exc: The exception raised by `self._model.invoke(...)`.

    Returns:
        Whether `exc` should be retried by `bounded_backoff`.
    """
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return isinstance(exc, ModelError) and exc.is_retryable


def _classify_error(exc: Exception) -> ProviderError:
    """Map a raw exception from `ChatGoogleGenerativeAI.invoke` into a typed
    `AgentError`.

    Args:
        exc: The exception raised by `self._model.invoke(...)`, after
            retries (if any) are resolved one way or the other.

    Returns:
        `ProviderAuthError` for an authentication/permission failure,
        `ProviderTransientError` for anything `_is_transient_error` matches,
        otherwise the generic `ProviderError`.
    """
    if isinstance(exc, (ModelAuthenticationError, ModelPermissionDeniedError)):
        return ProviderAuthError(str(exc))
    if _is_transient_error(exc):
        return ProviderTransientError(str(exc))
    return ProviderError(str(exc))


class GeminiProvider:
    """Gemini LLM provider, backed by `langchain-google-genai`'s
    `ChatGoogleGenerativeAI` rather than a raw `google-genai` SDK call —
    the sole provider, with no fallback provider and no circuit breaker
    (see docs/design.md §3 for why)."""

    def __init__(self, api_key: str, model: str) -> None:
        """Build the bound chat model.

        Args:
            api_key: Gemini (AI Studio) API key.
            model: Gemini model name.
        """
        # max_retries=1 (not 0!) disables the SDK's own retry loop — a
        # documented quirk of the underlying Google SDK where 0 means "use
        # the Google default" (5 retries) rather than "no retries". Keeping
        # `bounded_backoff` below as the single source of retry behavior.
        self._model = ChatGoogleGenerativeAI(model=model, api_key=api_key, max_retries=1).bind_tools(TOOLS)

    @bounded_backoff(retry=tenacity.retry_if_exception(_is_transient_error), attempts=2, logger=logger)
    def _generate_raw(self, messages: list[BaseMessage]) -> AIMessage:
        """Invoke the bound chat model once, retrying (via the
        `bounded_backoff` decorator) if the raw exception looks transient.
        Raises whatever the client itself raises, unclassified —
        classification happens once, in `generate`, after retries are
        resolved one way or the other.

        Args:
            messages: The full message list to send, including the leading
                `SystemMessage`.

        Returns:
            The model's reply.
        """
        return self._model.invoke(messages)

    def generate(self, messages: list[BaseMessage], system_instruction: str) -> AIMessage:
        """Send the message history to Gemini and return its reply.

        Args:
            messages: Conversation history (no system message included —
                this method prepends one).
            system_instruction: The system prompt for this call.

        Returns:
            The model's reply as an `AIMessage` (its `.tool_calls` is
            non-empty when the model chose to call a tool).

        Raises:
            ProviderAuthError: Authentication/permission failure.
            ProviderTransientError: Rate limit, server error, or a
                connection/timeout failure that survived every retry.
            ProviderError: Any other provider call failure.
        """
        try:
            return self._generate_raw([SystemMessage(content=system_instruction), *messages])
        except Exception as exc:
            typed = _classify_error(exc)
            logger.warning("provider_call_failed", extra={"error_class": type(typed).__name__})
            raise typed from exc
