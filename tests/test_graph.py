import pandas as pd
from google.genai import types

from retail_agent.errors import QueryPermissionError, QuerySyntaxError
from retail_agent.graph import build_graph

INITIAL_STATE_EXTRAS = {
    "self_correct_attempts": 0,
    "empty_result_sanity_checked": False,
    "last_tool_errors": [],
}


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
    def __init__(self, *, query_script=None):
        self.queries = []
        self._query_script = list(query_script) if query_script is not None else None

    def run_query(self, sql):
        self.queries.append(sql)
        if self._query_script is not None:
            item = self._query_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
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


def _invoke(graph, thread_id, text):
    return graph.invoke(
        {"messages": [types.Content(role="user", parts=[types.Part(text=text)])], **INITIAL_STATE_EXTRAS},
        config={"configurable": {"thread_id": thread_id}},
    )


def _final_text(result):
    return "".join(p.text for p in result["messages"][-1].parts if p.text)


def _sql_call(id_):
    return types.FunctionCall(id=id_, name="run_query", args={"sql": "SELECT 1"})


def test_graph_runs_tool_call_then_final_answer():
    tool_call = _sql_call("call-1")
    provider = FakeProvider(
        [
            _model_response(function_call=tool_call),
            _model_response(text="Total revenue is 42."),
        ]
    )
    bq_tool = FakeBigQueryTool()
    graph = build_graph(provider, bq_tool, system_instruction="test")

    result = _invoke(graph, "t1", "What's total revenue?")

    assert provider.calls == 2
    assert bq_tool.queries == ["SELECT 1"]
    assert _final_text(result) == "Total revenue is 42."


def test_graph_answers_directly_without_tool_call():
    provider = FakeProvider([_model_response(text="I can help with sales, orders, and product data.")])
    graph = build_graph(provider, FakeBigQueryTool(), system_instruction="test")

    result = _invoke(graph, "t2", "What can you do?")

    assert provider.calls == 1
    assert "sales" in _final_text(result)


def test_self_correctable_error_retries_and_then_succeeds():
    bq_tool = FakeBigQueryTool(
        query_script=[
            QuerySyntaxError("bad column"),
            {
                "dataframe": pd.DataFrame({"id": [1], "total_revenue": [42.0]}),
                "row_count": 1,
                "bytes_processed": 123,
                "redacted_columns": [],
            },
        ]
    )
    provider = FakeProvider(
        [
            _model_response(function_call=_sql_call("call-1")),
            _model_response(function_call=_sql_call("call-2")),  # model self-corrects
            _model_response(text="Total revenue is 42."),
        ]
    )
    graph = build_graph(provider, bq_tool, system_instruction="test")

    result = _invoke(graph, "t3", "What's total revenue?")

    assert provider.calls == 3
    assert _final_text(result) == "Total revenue is 42."


def test_self_correctable_error_exhausts_budget_and_gives_up_gracefully():
    bq_tool = FakeBigQueryTool(
        query_script=[
            QuerySyntaxError("bad column"),
            QuerySyntaxError("still bad"),
            QuerySyntaxError("still bad again"),
        ]
    )
    provider = FakeProvider(
        [
            _model_response(function_call=_sql_call("call-1")),
            _model_response(function_call=_sql_call("call-2")),
            _model_response(function_call=_sql_call("call-3")),
        ]
    )
    graph = build_graph(provider, bq_tool, system_instruction="test")

    result = _invoke(graph, "t4", "What's total revenue?")

    # exactly 2 self-correct retries -> 3 total SQL attempts, then give up
    assert provider.calls == 3
    assert len(bq_tool.queries) == 3
    assert "rephrase" in _final_text(result).lower()


def test_non_self_correctable_error_gives_up_immediately_without_retry():
    bq_tool = FakeBigQueryTool(query_script=[QueryPermissionError("no access")])
    provider = FakeProvider([_model_response(function_call=_sql_call("call-1"))])
    graph = build_graph(provider, bq_tool, system_instruction="test")

    result = _invoke(graph, "t5", "What's total revenue?")

    assert provider.calls == 1  # no self-correct attempt for a terminal error
    assert len(bq_tool.queries) == 1
    assert "permission" in _final_text(result).lower()


def test_empty_result_gets_one_extra_sanity_check_pass():
    bq_tool = FakeBigQueryTool(
        query_script=[
            {"dataframe": pd.DataFrame(), "row_count": 0, "bytes_processed": 10, "redacted_columns": []},
            {
                "dataframe": pd.DataFrame({"id": [1], "total_revenue": [42.0]}),
                "row_count": 1,
                "bytes_processed": 123,
                "redacted_columns": [],
            },
        ]
    )
    provider = FakeProvider(
        [
            _model_response(function_call=_sql_call("call-1")),
            _model_response(function_call=_sql_call("call-2")),  # model revises after seeing 0 rows
            _model_response(text="Total revenue is 42."),
        ]
    )
    graph = build_graph(provider, bq_tool, system_instruction="test")

    result = _invoke(graph, "t6", "What's total revenue?")

    assert provider.calls == 3
    assert _final_text(result) == "Total revenue is 42."
