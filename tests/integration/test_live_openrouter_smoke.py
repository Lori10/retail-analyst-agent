"""One live smoke test against the real OpenRouter API — not asserting
exact output, just that a real call through `OpenRouterProvider.generate`
produces a real, non-empty answer without raising.

Skipped unless OPENROUTER_API_KEY is set as a real exported environment
variable (a .env file alone is not enough — see conftest.py) — so
`uv run pytest` never runs this by accident, and a clean checkout or CI
without live credentials never fails here. Also skipped in the default
Gemini-only setup, where no fallback provider is configured at all.
"""

import os

import pytest
from google.genai import types

from retail_agent.openrouter_provider import OpenRouterProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="requires a live OPENROUTER_API_KEY credential",
)


def test_live_openrouter_generate_returns_a_real_answer():
    provider = OpenRouterProvider(
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
    )
    message = types.Content(role="user", parts=[types.Part(text="Reply with the single word: pong")])

    response = provider.generate(contents=[message], system_instruction="You are a terse assistant.", tools=[])

    final = response.candidates[0].content
    text = "".join(part.text for part in final.parts if part.text)
    assert text.strip()
