"""Manual debug/demo utility: run one question through the real agent graph
and print every message in the resulting state — every tool call, every
tool response, every model turn — plus the resilience-depth bookkeeping
(self-correct attempts, the errors that drove them) — not just the final
answer the CLI shows.

For live debugging ("why did it answer that?", "why did it give up?") and
for demoing the internals of the agentic loop (tool-calling, PII stripping,
bounded self-correct in action) without reading logs — this is a checked-in
version of the ad-hoc script used to capture the traces in the Skeleton
Ledger. Not a substitute for build-order step 4's structured logging, which
will cover this for every call automatically; this is a manual, on-demand
alternative until then.

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


def _print_resilience_summary(result: dict) -> None:
    """Surface the resilience-depth bookkeeping the CLI never shows — how
    many self-correct attempts this turn used, and what error(s) drove
    them, since that's exactly what this script exists to make visible."""
    attempts = result.get("self_correct_attempts", 0)
    errors = result.get("last_tool_errors", [])
    print(f"\nself_correct_attempts: {attempts}")
    if errors:
        print("last_tool_errors:")
        for err in errors:
            print(f"    {err['error_class']} (self_correctable={err['self_correctable']}): {err['message']}")
    else:
        print("last_tool_errors: none")


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
    turn_state = {
        "messages": [message],
        # Same per-turn reset cli.py does — a fresh thread_id per run means
        # this would work fine without it too (call_tools defaults missing
        # keys to 0/False/[] via state.get(...)), but seeding it explicitly
        # keeps this script's invoke call honest about the real state shape
        # instead of relying on those defaults implicitly.
        "self_correct_attempts": 0,
        "empty_result_sanity_checked": False,
        "last_tool_errors": [],
    }
    result = graph.invoke(
        turn_state,
        config={"configurable": {"thread_id": f"trace-{uuid.uuid4().hex[:8]}"}},
    )

    for i, content in enumerate(result["messages"]):
        _print_message(i, content)

    _print_resilience_summary(result)


if __name__ == "__main__":
    main()
