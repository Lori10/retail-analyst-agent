import json
import operator
from typing import Annotated, TypedDict

from google.genai import types
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from retail_agent.bq_tool import BigQueryTool
from retail_agent.errors import AgentError
from retail_agent.llm_provider import GeminiProvider
from retail_agent.tools import TOOLS


class AgentState(TypedDict):
    messages: Annotated[list[types.Content], operator.add]


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


def build_graph(provider: GeminiProvider, bq_tool: BigQueryTool, system_instruction: str):
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
        for part in last.parts:
            if part.function_call is None:
                continue
            call = part.function_call
            try:
                payload = _run_tool(bq_tool, call.name, call.args or {})
            except AgentError as exc:
                payload = {"error": str(exc)}
            response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call.name,
                        response=payload,
                        id=call.id,
                    )
                )
            )
        return {"messages": [types.Content(role="user", parts=response_parts)]}

    def route_after_model(state: AgentState) -> str:
        last = state["messages"][-1]
        if any(part.function_call is not None for part in last.parts):
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", route_after_model, {"tools": "tools", END: END})
    graph.add_edge("tools", "call_model")

    return graph.compile(checkpointer=InMemorySaver())
