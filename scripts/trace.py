"""Manual debug/demo utility: run one question through the real agent graph
and print every message in the resulting state — every tool call, every
tool response, every model turn — not just the final answer the CLI shows.

For live debugging ("why did it answer that?") and for demoing the
internals of the agentic loop (tool-calling, PII stripping in action)
without reading logs — this is a checked-in version of the ad-hoc script
used to capture the traces in the Skeleton Ledger. Not a substitute for
build-order step 4's structured logging, which will cover this for every
call automatically; this is a manual, on-demand alternative until then.

Usage:
    uv run python scripts/trace.py "What are the top 5 product categories by revenue?"

Requires the same credentials as the CLI (GOOGLE_CLOUD_PROJECT, GEMINI_API_KEY).
"""

import json
import sys
import uuid

from google.genai import types

from retail_agent.cli import StartupError, _build_graph
from retail_agent.config import ConfigError, load_config


def _print_message(index: int, content: types.Content) -> None:
    print(f"\n[{index}] {content.role}")
    for part in content.parts:
        if part.text:
            print(f"    text: {part.text}")
        if part.function_call:
            args = dict(part.function_call.args or {})
            print(f"    function_call: {part.function_call.name}({json.dumps(args)})")
        if part.function_response:
            payload = part.function_response.response
            pretty = json.dumps(payload, indent=2).replace("\n", "\n      ")
            print(f"    function_response[{part.function_response.name}]:\n      {pretty}")


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: uv run python scripts/trace.py "your question here"', file=sys.stderr)
        sys.exit(1)
    question = " ".join(sys.argv[1:])

    try:
        graph = _build_graph(load_config())
    except (ConfigError, StartupError) as exc:
        print(f"Could not start the agent: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"QUESTION: {question}")
    message = types.Content(role="user", parts=[types.Part(text=question)])
    result = graph.invoke(
        {"messages": [message]},
        config={"configurable": {"thread_id": f"trace-{uuid.uuid4().hex[:8]}"}},
    )

    for i, content in enumerate(result["messages"]):
        _print_message(i, content)


if __name__ == "__main__":
    main()
