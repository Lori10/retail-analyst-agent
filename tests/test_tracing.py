import json

from retail_agent.tracing import configure_tracing, log_event


def test_log_event_carries_expected_fields(trace_events):
    log_event("llm_call", conversation_id="c1", tokens=5)

    (event,) = trace_events
    assert event["message"] == "llm_call"
    assert event["conversation_id"] == "c1"
    assert event["tokens"] == 5
    assert event["severity"] == "INFO"


def test_log_event_sets_error_severity_when_error_class_present(trace_events):
    log_event("tool_call", error_class="QuerySyntaxError")

    (event,) = trace_events
    assert event["severity"] == "ERROR"


def test_two_events_are_captured_in_order(trace_events):
    log_event("llm_call", turn_id="t1")
    log_event("tool_call", turn_id="t1")

    assert [event["message"] for event in trace_events] == ["llm_call", "tool_call"]


def test_log_event_falls_back_to_str_for_non_serializable_fields(trace_events):
    class Weird:
        def __str__(self):
            return "weird-value"

    log_event("turn", extra=Weird())

    (event,) = trace_events
    assert event["extra"] == "weird-value"


def test_configure_tracing_writes_json_lines_to_a_real_file(tmp_path):
    path = tmp_path / "trace.jsonl"
    configure_tracing(str(path))
    log_event("turn", outcome="answered")
    log_event("turn", outcome="blocked")

    lines = path.read_text().strip().splitlines()
    assert [json.loads(line)["outcome"] for line in lines] == ["answered", "blocked"]


def test_reconfiguring_tracing_does_not_leak_handlers(tmp_path):
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"

    configure_tracing(str(first_path))
    log_event("turn", outcome="answered")

    configure_tracing(str(second_path))
    log_event("turn", outcome="blocked")

    assert len(first_path.read_text().strip().splitlines()) == 1
    assert len(second_path.read_text().strip().splitlines()) == 1
