"""Live regression test for the circuit breaker actually failing a real
graph turn over to OpenRouter — not just OpenRouterProvider.generate() in
isolation (see test_live_openrouter_smoke.py), but the full LangGraph
tool-calling loop (call_model -> tools -> call_model) driven by a real
OpenRouter response, including real function-calling translation
(_to_openai_messages/_to_openai_tools/_to_genai_response) against
OpenRouter's actual API output rather than a mocked one.

The primary Gemini provider is deliberately constructed with an invalid API
key so the breaker opens after exactly one failure — this is not simulating
failure, it's a real Gemini auth failure against the real API, only the
*fallback* (OpenRouter) is the one expected to succeed.

Per design.md Sec.5, `call_model` does not catch provider errors itself, so
a `ProviderError` from the first (Gemini) call propagates out of that
`graph.invoke()` entirely -- exactly like cli.py's top-level `except
AgentError` handles it for a real user hitting "try again". Two invokes on
the same thread_id are required to see the failover: the first opens the
breaker and fails, the second is what actually routes to OpenRouter.

Skipped unless GOOGLE_CLOUD_PROJECT, GEMINI_API_KEY, and OPENROUTER_API_KEY
are set as real exported environment variables (a .env file alone is not
enough -- see conftest.py).
"""

import os
import time

import pytest
from google.cloud import bigquery
from google.genai import types

from retail_agent.bq_tool import BigQueryTool
from retail_agent.circuit_breaker import ProviderCircuitBreaker
from retail_agent.cli import SYSTEM_INSTRUCTION
from retail_agent.config import load_config
from retail_agent.errors import ProviderError
from retail_agent.graph import build_graph
from retail_agent.llm_provider import GeminiProvider
from retail_agent.openrouter_provider import OpenRouterProvider

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        and os.environ.get("GEMINI_API_KEY")
        and os.environ.get("OPENROUTER_API_KEY")
    ),
    reason="requires live GOOGLE_CLOUD_PROJECT + GEMINI_API_KEY + OPENROUTER_API_KEY credentials",
)


def _turn_state(text):
    return {
        "messages": [types.Content(role="user", parts=[types.Part(text=text)])],
        "self_correct_attempts": 0,
        "empty_result_sanity_checked": False,
        "last_tool_errors": [],
    }


def test_live_graph_turn_routes_through_openrouter_after_breaker_opens():
    config = load_config()
    client = bigquery.Client(project=config.project_id)
    bq_tool = BigQueryTool(
        client=client,
        max_bytes_billed=config.max_bytes_billed,
        row_limit=config.row_limit,
        timeout_seconds=config.query_timeout_seconds,
    )

    broken_gemini = GeminiProvider(api_key="invalid-test-key", model=config.gemini_model)
    real_openrouter = OpenRouterProvider(api_key=config.openrouter_api_key, model=config.openrouter_model)
    breaker = ProviderCircuitBreaker(
        primary=broken_gemini,
        fallback=real_openrouter,
        failure_threshold=1,
        cooldown_seconds=60,
    )

    graph = build_graph(breaker, bq_tool, SYSTEM_INSTRUCTION)
    thread_config = {"configurable": {"thread_id": "integration-openrouter-fallback"}}

    with pytest.raises(ProviderError):
        graph.invoke(_turn_state("How many orders are in the dataset?"), config=thread_config)

    assert time.monotonic() < breaker._open_until, "breaker should be open after the Gemini failure"

    result = graph.invoke(_turn_state("How many orders are in the dataset?"), config=thread_config)

    final = result["messages"][-1]
    text = "".join(part.text for part in final.parts if part.text)
    assert text.strip()
