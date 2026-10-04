import asyncio
import json

import pandas as pd
from mcp import Client

from retail_agent.errors import QueryPermissionError, QuerySyntaxError
from retail_agent.mcp_server import LazyResource, build_server
from retail_agent.startup import StartupError
from test_graph import FakeBigQueryTool, FakeReportsStore

OWNER = "alice"


class FakeClock:
    """Manually advanced stand-in for `time.monotonic`, so token expiry is
    tested without sleeping."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _call(server, name, args=None):
    """Call one tool over a real in-process MCP client session.

    Goes through the full protocol path (JSON-RPC, input-schema validation,
    result serialization), not a direct Python function call.

    Returns:
        `(is_error, payload)` — payload is the parsed JSON result on
        success, or the raw error text on failure.
    """

    async def _run():
        async with Client(server) as client:
            return await client.call_tool(name, args or {})

    result = asyncio.run(_run())
    text = result.content[0].text
    return result.is_error, (text if result.is_error else json.loads(text))


def _list_tool_names(server):
    async def _run():
        async with Client(server) as client:
            return await client.list_tools()

    return sorted(tool.name for tool in asyncio.run(_run()).tools)


def _server(bq_tool=None, reports_store=None, owner=OWNER, **kwargs):
    return build_server(bq_tool or FakeBigQueryTool(), reports_store or FakeReportsStore(), owner, **kwargs)


def _seed(store, *titles, owner=OWNER, conversation_id="mcp-session-alice"):
    return [
        store.save_report(owner=owner, title=title, content="...", conversation_id=conversation_id)["id"]
        for title in titles
    ]


# --- Delete confirmation (two-step token flow) ---------------------------------


def test_preview_delete_deletes_nothing_and_returns_candidates_with_token():
    store = FakeReportsStore()
    _seed(store, "Client X Q1", "Client X Q2", "Other")
    server = _server(reports_store=store)

    is_error, payload = _call(server, "preview_delete", {"scope": "all", "title_contains": "Client X"})

    assert not is_error
    assert sorted(r["title"] for r in payload["candidates"]) == ["Client X Q1", "Client X Q2"]
    assert payload["confirmation_token"]
    assert payload["expires_in_seconds"] > 0
    assert store.deleted_ids == []
    assert len(store.list_reports(OWNER)) == 3


def test_confirm_delete_with_valid_token_deletes_exactly_the_previewed_reports():
    store = FakeReportsStore()
    ids = _seed(store, "Client X Q1", "Client X Q2", "Other")
    server = _server(reports_store=store)

    _, preview = _call(server, "preview_delete", {"scope": "all", "title_contains": "Client X"})
    is_error, payload = _call(server, "confirm_delete", {"confirmation_token": preview["confirmation_token"]})

    assert not is_error
    assert payload["deleted"] == 2
    assert sorted(store.deleted_ids) == sorted(ids[:2])
    assert [r["title"] for r in store.list_reports(OWNER)] == ["Other"]


def test_report_added_after_preview_is_not_deleted_by_confirm():
    store = FakeReportsStore()
    _seed(store, "Client X Q1")
    server = _server(reports_store=store)

    _, preview = _call(server, "preview_delete", {"scope": "all", "title_contains": "Client X"})
    _seed(store, "Client X Q3")  # matches the same filter, but was never shown to the user
    _call(server, "confirm_delete", {"confirmation_token": preview["confirmation_token"]})

    assert [r["title"] for r in store.list_reports(OWNER)] == ["Client X Q3"]


def test_confirmation_token_is_single_use():
    store = FakeReportsStore()
    _seed(store, "Client X Q1")
    server = _server(reports_store=store)

    _, preview = _call(server, "preview_delete", {"scope": "all"})
    _call(server, "confirm_delete", {"confirmation_token": preview["confirmation_token"]})
    is_error, message = _call(server, "confirm_delete", {"confirmation_token": preview["confirmation_token"]})

    assert is_error
    assert "preview_delete" in message
    assert len(store.deleted_ids) == 1


def test_expired_confirmation_token_is_rejected_and_deletes_nothing():
    store = FakeReportsStore()
    _seed(store, "Client X Q1")
    clock = FakeClock()
    server = _server(reports_store=store, token_ttl_seconds=60, clock=clock)

    _, preview = _call(server, "preview_delete", {"scope": "all"})
    clock.now += 61
    is_error, message = _call(server, "confirm_delete", {"confirmation_token": preview["confirmation_token"]})

    assert is_error
    assert "expired" in message.lower()
    assert store.deleted_ids == []


def test_unknown_confirmation_token_is_rejected_and_deletes_nothing():
    store = FakeReportsStore()
    _seed(store, "Client X Q1")
    server = _server(reports_store=store)

    is_error, _ = _call(server, "confirm_delete", {"confirmation_token": "not-a-real-token"})

    assert is_error
    assert store.deleted_ids == []


def test_preview_with_zero_candidates_returns_no_token():
    server = _server()

    is_error, payload = _call(server, "preview_delete", {"scope": "all", "title_contains": "nothing"})

    assert not is_error
    assert payload["candidates"] == []
    assert payload["confirmation_token"] is None


def test_preview_delete_never_returns_another_owners_reports():
    store = FakeReportsStore()
    _seed(store, "Bob's report", owner="bob")
    server = _server(reports_store=store)

    _, payload = _call(server, "preview_delete", {"scope": "all"})

    assert payload["candidates"] == []


def test_preview_delete_conversation_scope_only_covers_this_mcp_session():
    store = FakeReportsStore()
    _seed(store, "From MCP")
    _seed(store, "From CLI", conversation_id="cli-session-alice")
    server = _server(reports_store=store)

    _, payload = _call(server, "preview_delete", {"scope": "conversation"})

    assert [r["title"] for r in payload["candidates"]] == ["From MCP"]


def test_no_single_step_delete_tool_is_exposed():
    names = _list_tool_names(_server())

    assert names == sorted(
        ["get_schema", "run_query", "save_report", "list_reports", "preview_delete", "confirm_delete"]
    )


# --- Query / schema tools -------------------------------------------------------


def test_run_query_returns_rows_and_passes_redacted_columns_through():
    bq = FakeBigQueryTool(
        query_script=[
            {
                "dataframe": pd.DataFrame({"country": ["US"], "n": [3]}),
                "row_count": 1,
                "bytes_processed": 10,
                "redacted_columns": ["email"],
            }
        ]
    )
    server = _server(bq_tool=bq)

    is_error, payload = _call(server, "run_query", {"sql": "SELECT email, country, COUNT(*) n FROM users"})

    assert not is_error
    assert payload == {"row_count": 1, "redacted_columns": ["email"], "rows": [{"country": "US", "n": 3}]}


def test_run_query_agent_error_comes_back_as_tool_error_with_its_message():
    bq = FakeBigQueryTool(query_script=[QuerySyntaxError("Unrecognized name: revnue")])
    server = _server(bq_tool=bq)

    is_error, message = _call(server, "run_query", {"sql": "SELECT revnue FROM orders"})

    assert is_error
    assert "Unrecognized name: revnue" in message


def test_non_self_correctable_error_also_comes_back_as_tool_error():
    bq = FakeBigQueryTool(schema_script=[QueryPermissionError("403 Forbidden")])
    server = _server(bq_tool=bq)

    is_error, message = _call(server, "get_schema", {"table_name": "orders"})

    assert is_error
    assert "403 Forbidden" in message


def test_get_schema_rejects_table_outside_the_dataset_enum():
    bq = FakeBigQueryTool()
    server = _server(bq_tool=bq)

    is_error, _ = _call(server, "get_schema", {"table_name": "secret_table"})

    assert is_error
    assert bq.schema_calls == []


def test_unexpected_exception_does_not_leak_its_message():
    bq = FakeBigQueryTool(query_script=[RuntimeError("internal detail: db password=hunter2")])
    server = _server(bq_tool=bq)

    is_error, message = _call(server, "run_query", {"sql": "SELECT 1"})

    assert is_error
    assert "hunter2" not in message


# --- Reports tools --------------------------------------------------------------


def test_save_report_then_list_reports_scoped_to_owner():
    store = FakeReportsStore()
    _seed(store, "Bob's report", owner="bob")
    server = _server(reports_store=store)

    _, saved = _call(server, "save_report", {"title": "Q1", "content": "Revenue up.", "tags": ["q1"]})
    _, listed = _call(server, "list_reports")

    assert saved["saved"] is True
    assert [r["title"] for r in listed["reports"]] == ["Q1"]
    assert listed["reports"][0]["conversation_id"] == "mcp-session-alice"


# --- Observability --------------------------------------------------------------


def test_each_tool_call_emits_a_tool_call_trace_event(trace_events):
    bq = FakeBigQueryTool(query_script=[QuerySyntaxError("bad")])
    server = _server(bq_tool=bq)

    _call(server, "get_schema", {"table_name": "orders"})
    _call(server, "run_query", {"sql": "SELECT x"})

    tool_events = [e for e in trace_events if e["message"] == "tool_call"]
    assert [(e["tool"], e["transport"], e["error_class"]) for e in tool_events] == [
        ("get_schema", "mcp", None),
        ("run_query", "mcp", "QuerySyntaxError"),
    ]
    assert tool_events[1]["sql"] == "SELECT x"
    assert all("latency_ms" in e for e in tool_events)


# --- Lazy startup -----------------------------------------------------------------


def test_lazy_resource_builds_nothing_until_first_use_then_builds_once():
    builds = []

    def factory():
        builds.append(1)
        return FakeReportsStore()

    store = LazyResource(factory)
    server = _server(reports_store=store)
    assert builds == []  # building the server connects to nothing

    _call(server, "list_reports")
    _call(server, "list_reports")

    assert builds == [1]


def test_lazy_resource_startup_error_reaches_client_and_is_retried_next_call():
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise StartupError("Could not connect to the saved reports database. Is Postgres running?")
        return FakeReportsStore()

    server = _server(reports_store=LazyResource(factory))

    is_error, message = _call(server, "list_reports")
    assert is_error
    assert "Is Postgres running?" in message  # readable, not the SDK's generic failure

    is_error, payload = _call(server, "list_reports")
    assert not is_error
    assert payload == {"reports": []}
    assert len(attempts) == 2


def test_importing_the_server_loads_no_heavy_dependencies():
    """The stdio handshake must finish inside a client's connect timeout, so
    `retail_agent.mcp_server` may not import BigQuery, pandas, psycopg or
    LangChain/LangGraph at module level. Checked in a fresh interpreter, since
    this test process has already imported all of them."""
    import subprocess
    import sys

    heavy = ["pandas", "google.cloud.bigquery", "psycopg", "langchain_core", "langgraph"]
    code = (
        "import sys, retail_agent.mcp_server; "
        f"print([m for m in {heavy!r} if m in sys.modules])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout

    assert out.strip() == "[]"
