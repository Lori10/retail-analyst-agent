import logging
import time
from dataclasses import dataclass, field

from google.genai import types

from retail_agent.errors import ProviderError
from retail_agent.llm_provider import Provider

logger = logging.getLogger(__name__)


@dataclass
class ProviderCircuitBreaker:
    """Routes to `primary` until it fails `failure_threshold` times in a row,
    then routes to `fallback` for `cooldown_seconds` before trying the
    primary again. A single-process, in-memory breaker — no external state
    store needed for a synchronous CLI prototype.

    Attributes:
        primary: The provider used while the breaker is closed (Gemini).
        fallback: The provider used while the breaker is open (OpenRouter).
        failure_threshold: Consecutive `primary` failures before the breaker
            opens.
        cooldown_seconds: How long the breaker stays open before `primary`
            is tried again.
    """

    primary: Provider
    fallback: Provider
    failure_threshold: int = 2
    cooldown_seconds: float = 60.0
    _consecutive_failures: int = field(default=0, init=False)
    _open_until: float = field(default=0.0, init=False)

    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        """Generate via `primary` if the breaker is closed, else `fallback`.

        On a `primary` failure, bumps the consecutive-failure count and
        opens the breaker once `failure_threshold` is reached; any success
        on `primary` resets the count to 0. Failures on `fallback` are not
        tracked — there's no further fallback to route to.

        Args:
            contents: The running message history.
            system_instruction: The system prompt for this call.
            tools: Function-calling tool schemas available to the model.

        Returns:
            The chosen provider's response.

        Raises:
            ProviderError: The chosen provider's `generate(...)` raised one
                of its subclasses; re-raised unchanged after any breaker
                bookkeeping.
        """
        use_fallback = time.monotonic() < self._open_until
        provider, name = (self.fallback, "openrouter") if use_fallback else (self.primary, "gemini")

        try:
            response = provider.generate(contents, system_instruction, tools)
        except ProviderError as exc:
            if name == "gemini":
                self._consecutive_failures += 1
                logger.warning(
                    "provider_failure",
                    extra={"provider": name, "error_class": type(exc).__name__, "consecutive_failures": self._consecutive_failures},
                )
                if self._consecutive_failures >= self.failure_threshold:
                    self._open_until = time.monotonic() + self.cooldown_seconds
                    logger.warning(
                        "provider_failover",
                        extra={"from": "gemini", "to": "openrouter", "cooldown_seconds": self.cooldown_seconds},
                    )
            raise

        if name == "gemini":
            self._consecutive_failures = 0
        return response
