"""Hermetic unit tests for the golden-set eval harness (src/retail_agent/eval.py).

No live Gemini/BigQuery calls anywhere in this file — `run_case`/`run_eval`
(the live-calling pieces) are exercised manually via `scripts/run_eval.py`,
not here. See tests/integration/ for this repo's convention on live-call
opt-in gating.
"""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from retail_agent.errors import ProviderTransientError
from retail_agent.eval import (
    GoldenCase,
    _build_judge_messages,
    _check_expected_keywords,
    _check_expected_tables,
    _check_expected_tool,
    _check_pii_leak,
    _extract_case_result,
    _score_case,
    judge_case,
    load_golden_set,
)


def _case(**overrides) -> GoldenCase:
    defaults = dict(
        id="case-1",
        category="test",
        question="What are our top products?",
        expects_tool="run_query",
        expected_tables=["orders"],
        expected_keywords=["top"],
        pii_sensitive=False,
    )
    defaults.update(overrides)
    return GoldenCase(**defaults)


class FakeJudgeModel:
    """Duck-typed judge double — `.invoke(messages) -> AIMessage`, same
    shape `test_graph.py`'s FakeProvider uses for `.generate`."""

    def __init__(self, response_text: str):
        self._response_text = response_text
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self._response_text)


# --- load_golden_set -------------------------------------------------------


def test_load_golden_set_parses_the_real_bundled_file():
    cases = load_golden_set("eval/golden_set.json")
    assert len(cases) >= 5
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    categories = {c.category for c in cases}
    assert {"customer_behavior", "pii_safety", "off_topic"} <= categories
    for case in cases:
        assert case.question.strip()
        assert case.expects_tool in ("run_query", "get_schema", None)


def test_load_golden_set_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "dup",
                    "category": "x",
                    "question": "q1",
                    "expects_tool": None,
                    "expected_tables": [],
                    "expected_keywords": [],
                },
                {
                    "id": "dup",
                    "category": "x",
                    "question": "q2",
                    "expects_tool": None,
                    "expected_tables": [],
                    "expected_keywords": [],
                },
            ]
        )
    )
    with pytest.raises(ValueError, match="duplicate case id"):
        load_golden_set(str(path))


def test_load_golden_set_rejects_invalid_expects_tool(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "bad",
                    "category": "x",
                    "question": "q1",
                    "expects_tool": "delete_reports",
                    "expected_tables": [],
                    "expected_keywords": [],
                }
            ]
        )
    )
    with pytest.raises(ValueError, match="invalid expects_tool"):
        load_golden_set(str(path))


def test_load_golden_set_rejects_empty_list(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text(json.dumps([]))
    with pytest.raises(ValueError, match="non-empty"):
        load_golden_set(str(path))


# --- _extract_case_result ---------------------------------------------------


def test_extract_case_result_run_query_round():
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "run_query", "args": {"sql": "SELECT * FROM orders"}, "id": "1"}],
        ),
        ToolMessage(content=json.dumps({"row_count": 3, "rows": []}), tool_call_id="1", name="run_query"),
        AIMessage(content="Here are the top orders."),
    ]
    result = _extract_case_result(messages, self_correct_attempts=0)
    assert result.tools_called == ["run_query"]
    assert result.sql_queries == ["SELECT * FROM orders"]
    assert result.tables_touched == {"orders"}
    assert result.row_counts == [3]
    assert result.had_error is False
    assert result.answer_text == "Here are the top orders."


def test_extract_case_result_get_schema_round():
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "get_schema", "args": {"table_name": "users"}, "id": "1"}],
        ),
        ToolMessage(content=json.dumps({"columns": []}), tool_call_id="1", name="get_schema"),
        AIMessage(content="Here's what we track about customers."),
    ]
    result = _extract_case_result(messages)
    assert result.tools_called == ["get_schema"]
    assert result.sql_queries == []
    assert result.tables_touched == set()


def test_extract_case_result_no_tool_call():
    messages = [AIMessage(content="I can only help with analysis questions.")]
    result = _extract_case_result(messages)
    assert result.tools_called == []
    assert result.answer_text == "I can only help with analysis questions."


def test_extract_case_result_with_tool_error():
    messages = [
        AIMessage(content="", tool_calls=[{"name": "run_query", "args": {"sql": "SELECT bad"}, "id": "1"}]),
        ToolMessage(
            content=json.dumps({"error": "syntax error", "error_class": "QuerySyntaxError"}),
            tool_call_id="1",
            name="run_query",
        ),
        AIMessage(content="Sorry, I couldn't complete that."),
    ]
    result = _extract_case_result(messages, self_correct_attempts=1)
    assert result.had_error is True
    assert result.self_correct_attempts == 1


def test_extract_case_result_multiple_run_query_calls_union_tables():
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "run_query", "args": {"sql": "SELECT * FROM orders"}, "id": "1"},
                {"name": "run_query", "args": {"sql": "SELECT * FROM order_items"}, "id": "2"},
            ],
        ),
        ToolMessage(content=json.dumps({"row_count": 1}), tool_call_id="1", name="run_query"),
        ToolMessage(content=json.dumps({"row_count": 2}), tool_call_id="2", name="run_query"),
        AIMessage(content="Combined answer."),
    ]
    result = _extract_case_result(messages)
    assert result.tables_touched == {"orders", "order_items"}
    assert result.row_counts == [1, 2]


# --- deterministic checks ----------------------------------------------------


def test_check_expected_tool_pass_and_fail():
    case = _case(expects_tool="run_query")
    ok = _extract_case_result(
        [AIMessage(content="", tool_calls=[{"name": "run_query", "args": {"sql": "x"}, "id": "1"}])]
    )
    bad = _extract_case_result([AIMessage(content="no tool call here")])
    assert _check_expected_tool(case, ok).passed
    assert not _check_expected_tool(case, bad).passed


def test_check_expected_tool_none_expected():
    case = _case(expects_tool=None)
    ok = _extract_case_result([AIMessage(content="I can't help with that.")])
    bad = _extract_case_result(
        [AIMessage(content="", tool_calls=[{"name": "run_query", "args": {"sql": "x"}, "id": "1"}])]
    )
    assert _check_expected_tool(case, ok).passed
    assert not _check_expected_tool(case, bad).passed


def test_check_expected_tables_pass_and_fail():
    case = _case(expected_tables=["orders"])
    ok = _extract_case_result(
        [AIMessage(content="", tool_calls=[{"name": "run_query", "args": {"sql": "SELECT * FROM orders"}, "id": "1"}])]
    )
    bad = _extract_case_result(
        [AIMessage(content="", tool_calls=[{"name": "run_query", "args": {"sql": "SELECT * FROM products"}, "id": "1"}])]
    )
    assert _check_expected_tables(case, ok).passed
    assert not _check_expected_tables(case, bad).passed


def test_check_expected_tables_noop_when_none_configured():
    case = _case(expected_tables=[])
    result = _extract_case_result([AIMessage(content="anything")])
    assert _check_expected_tables(case, result).passed


def test_check_expected_keywords_case_insensitive():
    case = _case(expected_keywords=["Top Spenders"])
    result = _extract_case_result([AIMessage(content="Here are your top spenders this month.")])
    assert _check_expected_keywords(case, result).passed


def test_check_expected_keywords_fail_when_absent():
    case = _case(expected_keywords=["top spenders"])
    result = _extract_case_result([AIMessage(content="Revenue was flat.")])
    assert not _check_expected_keywords(case, result).passed


def test_check_pii_leak_flags_email():
    case = _case(pii_sensitive=True)
    result = _extract_case_result([AIMessage(content="Sure, reach jane.doe@example.com for details.")])
    outcome = _check_pii_leak(case, result)
    assert not outcome.passed
    assert "jane.doe@example.com" in outcome.detail


def test_check_pii_leak_passes_clean_answer():
    case = _case(pii_sensitive=True)
    result = _extract_case_result([AIMessage(content="I can't share customer contact details.")])
    assert _check_pii_leak(case, result).passed


def test_check_pii_leak_noop_when_not_pii_sensitive():
    case = _case(pii_sensitive=False)
    result = _extract_case_result([AIMessage(content="jane.doe@example.com")])
    assert _check_pii_leak(case, result).passed


# --- judge -------------------------------------------------------------------


def test_build_judge_messages_includes_question_and_answer():
    case = _case(question="How many orders happened last month?")
    result = _extract_case_result([AIMessage(content="There were 42 orders last month.")])
    messages = _build_judge_messages(case, result)
    combined = " ".join(str(m.content) for m in messages)
    assert "How many orders happened last month?" in combined
    assert "There were 42 orders last month." in combined


def test_judge_case_parses_well_formed_json():
    judge = FakeJudgeModel(
        '{"answers_question": true, "plausible": true, "no_pii": true, "reasoning": "Looks good."}'
    )
    case = _case()
    result = _extract_case_result([AIMessage(content="42 orders.")])
    verdict = judge_case(judge, case, result)
    assert verdict.passed
    assert verdict.parse_error is False
    assert verdict.reasoning == "Looks good."


def test_judge_case_handles_code_fenced_json():
    judge = FakeJudgeModel(
        '```json\n{"answers_question": true, "plausible": true, "no_pii": true, "reasoning": "ok"}\n```'
    )
    case = _case()
    result = _extract_case_result([AIMessage(content="42 orders.")])
    verdict = judge_case(judge, case, result)
    assert verdict.passed


def test_judge_case_flags_failing_dimension():
    judge = FakeJudgeModel(
        '{"answers_question": false, "plausible": true, "no_pii": true, "reasoning": "Off topic."}'
    )
    case = _case()
    result = _extract_case_result([AIMessage(content="Here's a lasagna recipe.")])
    verdict = judge_case(judge, case, result)
    assert not verdict.passed
    assert verdict.answers_question is False


def test_judge_case_handles_malformed_json_without_raising():
    judge = FakeJudgeModel("not valid json at all")
    case = _case()
    result = _extract_case_result([AIMessage(content="42 orders.")])
    verdict = judge_case(judge, case, result)
    assert verdict.parse_error is True
    assert verdict.passed is False


def test_judge_case_handles_missing_keys_without_raising():
    judge = FakeJudgeModel('{"answers_question": true}')
    case = _case()
    result = _extract_case_result([AIMessage(content="42 orders.")])
    verdict = judge_case(judge, case, result)
    assert verdict.parse_error is True


# --- _score_case: containing a live run-time failure to one case -----------


class FakeGraph:
    """Duck-typed compiled-graph double — `.invoke(input, config) -> dict`."""

    def __init__(self, *, raises=None, messages=None):
        self._raises = raises
        self._messages = messages or []

    def invoke(self, input, config):
        if self._raises is not None:
            raise self._raises
        return {"messages": self._messages, "self_correct_attempts": 0}


def test_score_case_contains_a_run_case_exception():
    graph = FakeGraph(raises=ProviderTransientError("rate limited"))
    judge = FakeJudgeModel('{"answers_question": true, "plausible": true, "no_pii": true, "reasoning": "n/a"}')
    case = _case()

    report = _score_case(graph, judge, case)

    assert report.passed is False
    assert report.result is None
    assert report.checks == []
    assert report.verdict.parse_error is True
    assert "ProviderTransientError" in report.run_error
    assert "rate limited" in report.run_error
    assert judge.calls == []  # the judge is never called for a case that never ran


def test_score_case_runs_normally_when_graph_succeeds():
    graph = FakeGraph(messages=[AIMessage(content="Here are the top spenders.")])
    judge = FakeJudgeModel('{"answers_question": true, "plausible": true, "no_pii": true, "reasoning": "good"}')
    case = _case(expects_tool=None, expected_tables=[], expected_keywords=[])

    report = _score_case(graph, judge, case)

    assert report.run_error is None
    assert report.result is not None
    assert report.passed is True
    assert len(judge.calls) == 1
