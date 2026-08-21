import pandas as pd
from google.genai import types

from retail_agent.graph import build_graph


class FakeProvider:
    """Returns canned responses in sequence, ignoring the actual contents sent
    to it — enough to exercise the graph's tool-calling loop without a live
    Gemini call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def generate(self, contents, system_instruction, tools):
        self.calls += 1
        return self._responses.pop(0)


class FakeBigQueryTool:
    def __init__(self):
        self.queries = []

    def run_query(self, sql):
        self.queries.append(sql)
        return {
            "dataframe": pd.DataFrame({"id": [1], "total_revenue": [42.0]}),
            "row_count": 1,
            "bytes_processed": 123,
            "redacted_columns": [],
        }

    def get_schema(self, table_name):
        return [{"name": "id", "type": "INTEGER"}]


def _model_response(*, text=None, function_call=None):
    parts = []
    if function_call is not None:
        parts.append(types.Part(function_call=function_call))
    if text is not None:
        parts.append(types.Part(text=text))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


def test_graph_runs_tool_call_then_final_answer():
    tool_call = types.FunctionCall(id="call-1", name="run_query", args={"sql": "SELECT 1"})
    provider = FakeProvider(
        [
            _model_response(function_call=tool_call),
            _model_response(text="Total revenue is 42."),
        ]
    )
    bq_tool = FakeBigQueryTool()
    graph = build_graph(provider, bq_tool, system_instruction="test")

    result = graph.invoke(
        {"messages": [types.Content(role="user", parts=[types.Part(text="What's total revenue?")])]},
        config={"configurable": {"thread_id": "t1"}},
    )

    assert provider.calls == 2
    assert bq_tool.queries == ["SELECT 1"]
    final_text = "".join(p.text for p in result["messages"][-1].parts if p.text)
    assert final_text == "Total revenue is 42."


def test_graph_answers_directly_without_tool_call():
    provider = FakeProvider([_model_response(text="I can help with sales, orders, and product data.")])
    graph = build_graph(provider, FakeBigQueryTool(), system_instruction="test")

    result = graph.invoke(
        {"messages": [types.Content(role="user", parts=[types.Part(text="What can you do?")])]},
        config={"configurable": {"thread_id": "t2"}},
    )

    assert provider.calls == 1
    final_text = "".join(p.text for p in result["messages"][-1].parts if p.text)
    assert "sales" in final_text
