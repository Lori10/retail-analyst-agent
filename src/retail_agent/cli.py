import logging
import sys

from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery
from google.genai import types

from retail_agent.bq_tool import BigQueryTool
from retail_agent.circuit_breaker import ProviderCircuitBreaker
from retail_agent.config import ConfigError, load_config
from retail_agent.errors import AgentError
from retail_agent.graph import build_graph
from retail_agent.llm_provider import GeminiProvider
from retail_agent.openrouter_provider import OpenRouterProvider


class StartupError(Exception):
    """Raised when the agent can't be built at all (bad credentials, no
    network, etc.) — distinct from AgentError, which covers failures during
    a conversation turn after the agent is already running."""

SYSTEM_INSTRUCTION = (
    "You are a data analysis assistant for retail Store and Regional Managers. "
    "Answer questions about sales, customers, products, and orders using the "
    "run_query and get_schema tools against the thelook_ecommerce dataset. "
    "Only answer analysis questions about this data — politely decline anything "
    "else. Never claim to know a customer's name, email, or address; those "
    "columns are not available to you."
)

THREAD_CONFIG = {"configurable": {"thread_id": "cli-session"}}


def _response_text(content: types.Content) -> str:
    """Concatenate the text parts of a model message.

    Args:
        content: A `types.Content` message, typically the final message in
            a turn's result.

    Returns:
        The concatenated text of all text parts (empty string if none).
    """
    return "".join(part.text for part in content.parts if part.text)


def _build_graph(config):
    """Construct the BigQuery client, LLM provider(s), and compiled graph.

    Args:
        config: A loaded `Config`.

    Returns:
        A compiled LangGraph graph ready for `.invoke(...)`.

    Raises:
        StartupError: BigQuery credentials are missing/invalid, or the
            client otherwise fails to construct.
    """
    try:
        client = bigquery.Client(project=config.project_id)
    except DefaultCredentialsError as exc:
        raise StartupError(
            "No Google Cloud credentials found. Run "
            "'gcloud auth application-default login' and try again."
        ) from exc
    except Exception as exc:
        raise StartupError(f"Could not connect to BigQuery: {exc}") from exc

    bq_tool = BigQueryTool(
        client=client,
        max_bytes_billed=config.max_bytes_billed,
        row_limit=config.row_limit,
        timeout_seconds=config.query_timeout_seconds,
    )
    gemini = GeminiProvider(api_key=config.gemini_api_key, model=config.gemini_model)
    if config.openrouter_api_key:
        provider = ProviderCircuitBreaker(
            primary=gemini,
            fallback=OpenRouterProvider(api_key=config.openrouter_api_key, model=config.openrouter_model),
            failure_threshold=config.provider_failure_threshold,
            cooldown_seconds=config.provider_cooldown_seconds,
        )
    else:
        provider = gemini
    return build_graph(provider, bq_tool, SYSTEM_INSTRUCTION)


def main() -> None:
    """Entry point for the `retail-agent` CLI: load config, build the
    graph, then run the REPL loop until the user exits or input closes.

    Prints a plain-language message and exits(1) on a config/startup
    failure; catches `AgentError`/any other exception per turn inside the
    loop so a single bad question never crashes the session.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        graph = _build_graph(config)
    except StartupError as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Retail Data Analysis Agent. Type 'exit' to quit.")
    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        user_message = types.Content(role="user", parts=[types.Part(text=user_input)])
        turn_state = {
            "messages": [user_message],
            # Reset every turn: a "turn" is exactly one graph.invoke() call,
            # so self-correct/empty-result budgets never leak across turns.
            "self_correct_attempts": 0,
            "empty_result_sanity_checked": False,
            "last_tool_errors": [],
        }
        try:
            result = graph.invoke(turn_state, config=THREAD_CONFIG)
        except AgentError as exc:
            print(f"Agent: Sorry, I couldn't complete that — {exc}")
            continue
        except Exception as exc:
            print(f"Agent: Something went wrong on my end ({type(exc).__name__}). Please try again.")
            continue

        print(f"Agent: {_response_text(result['messages'][-1])}")


if __name__ == "__main__":
    main()
