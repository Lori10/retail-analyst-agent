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
