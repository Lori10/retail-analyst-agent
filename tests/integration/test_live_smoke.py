"""One end-to-end smoke test through the real LangGraph loop, real Gemini,
and real BigQuery — not asserting an exact trace (the model's tool-call
choices aren't fully deterministic, see the Skeleton Ledger's Example A vs.
B), just that a real question produces a real, non-empty answer without
raising.

Skipped unless GOOGLE_CLOUD_PROJECT and GEMINI_API_KEY are set as real
exported environment variables (a .env file alone is not enough — see
conftest.py) — so `uv run pytest` never runs these by accident, and a
clean checkout or CI without live credentials never fails here.
"""

import os

import pytest
from langchain_core.messages import HumanMessage

from retail_agent.cli import _build_graph
from retail_agent.config import load_config

pytestmark = pytest.mark.skipif(
    not (os.environ.get("GOOGLE_CLOUD_PROJECT") and os.environ.get("GEMINI_API_KEY")),
    reason="requires live GOOGLE_CLOUD_PROJECT + GEMINI_API_KEY credentials",
)


def test_live_question_returns_a_real_answer():
    graph, _conversation_store, _owner = _build_graph(load_config())
    message = HumanMessage(content="How many orders are in the dataset?")

    result = graph.invoke(
        {"messages": [message]},
        config={"configurable": {"thread_id": "integration-smoke"}},
    )

    final = result["messages"][-1]
    assert final.content.strip()
