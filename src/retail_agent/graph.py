import json
import logging
import operator
from typing import Annotated, TypedDict

from google.genai import types
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from retail_agent.bq_tool import BigQueryTool
from retail_agent.errors import AgentError, graceful_message_for
from retail_agent.llm_provider import Provider
from retail_agent.tools import TOOLS

logger = logging.getLogger(__name__)

MAX_SELF_CORRECT_ATTEMPTS = 2


class AgentState(TypedDict):
    messages: Annotated[list[types.Content], operator.add]
    self_correct_attempts: int
    empty_result_sanity_checked: bool
    last_tool_errors: list[dict]


def _dataframe_to_records(df) -> list[dict]:
    # Routes through JSON (not .to_dict()) so numpy/Timestamp values become
    # plain JSON-safe types before they hit the genai SDK's proto Struct.
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _run_tool(bq_tool: BigQueryTool, name: str, args: dict) -> dict:
    if name == "run_query":
        result = bq_tool.run_query(args["sql"])
        return {
            "row_count": result["row_count"],
            "redacted_columns": result["redacted_columns"],
            "rows": _dataframe_to_records(result["dataframe"]),
        }
    if name == "get_schema":
        return {"columns": bq_tool.get_schema(args["table_name"])}
    return {"error": f"Unknown tool: {name}"}


def build_graph(provider: Provider, bq_tool: BigQueryTool, system_instruction: str):
    def call_model(state: AgentState) -> dict:
        response = provider.generate(
            contents=state["messages"],
            system_instruction=system_instruction,
            tools=TOOLS,
        )
        return {"messages": [response.candidates[0].content]}

    def call_tools(state: AgentState) -> dict:
        last = state["messages"][-1]
        response_parts = []
        self_correct_attempts = state.get("self_correct_attempts", 0)
        empty_result_checked = state.get("empty_result_sanity_checked", False)
        errors: list[dict] = []

        for part in last.parts:
            if part.function_call is None:
                continue
            call = part.function_call
            try:
                payload = _run_tool(bq_tool, call.name, call.args or {})
            except AgentError as exc:
                payload = {"error": str(exc), "error_class": type(exc).__name__}
                errors.append(
                    {
                        "error_class": type(exc).__name__,
                        "message": str(exc),
                        "self_correctable": exc.self_correctable,
                    }
                )
                logger.warning("tool_call_error", extra={"tool": call.name, "error_class": type(exc).__name__})
                if exc.self_correctable:
                    self_correct_attempts += 1
            else:
                if call.name == "run_query" and payload.get("row_count") == 0 and not empty_result_checked:
                    payload = {
                        **payload,
                        "note": (
                            "Zero rows returned — double-check filters/joins/date range "
                            "before treating this as the final answer."
                        ),
                    }
                    empty_result_checked = True

            response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call.name,
                        response=payload,
                        id=call.id,
                    )
                )
            )

        return {
            "messages": [types.Content(role="user", parts=response_parts)],
            "self_correct_attempts": self_correct_attempts,
            "empty_result_sanity_checked": empty_result_checked,
            "last_tool_errors": errors,
        }

    def give_up(state: AgentState) -> dict:
        errors = state["last_tool_errors"]
        chosen = next((e for e in errors if not e["self_correctable"]), errors[0])
        message = graceful_message_for(chosen["error_class"])
        logger.warning("agent_terminal_error", extra={"error_class": chosen["error_class"]})
        return {"messages": [types.Content(role="model", parts=[types.Part(text=message)])]}

    def route_after_model(state: AgentState) -> str:
        last = state["messages"][-1]
        if any(part.function_call is not None for part in last.parts):
            return "tools"
        return END

    def route_after_tools(state: AgentState) -> str:
        errors = state["last_tool_errors"]
        if not errors:
            return "call_model"
        if any(not e["self_correctable"] for e in errors):
            return "give_up"
        if state["self_correct_attempts"] > MAX_SELF_CORRECT_ATTEMPTS:
            return "give_up"
        return "call_model"

    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_node("give_up", give_up)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", route_after_model, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", route_after_tools, {"call_model": "call_model", "give_up": "give_up"})
    graph.add_edge("give_up", END)

    return graph.compile(checkpointer=InMemorySaver())
