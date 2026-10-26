import os

import psycopg
import pytest

from retail_agent.errors import ReportsStoreError
from retail_agent.reports_store import ReportsStore

TEST_DATABASE_URL = os.environ.get(
    "REPORTS_DATABASE_URL", "postgresql://retail_agent:retail_agent@localhost:5432/retail_agent_reports_test"
)


@pytest.fixture
def store():
    s = ReportsStore(TEST_DATABASE_URL)
    s._conn.execute("DELETE FROM reports")
    yield s
    s._conn.close()


def test_save_then_list_round_trips_a_report_including_tags(store):
    saved = store.save_report(
        owner="alice", title="Q1 Report", content="Revenue up 10%.",
        conversation_id="conv-1", tags=["q1", "revenue"],
    )
    assert saved["title"] == "Q1 Report"
    assert saved["id"] is not None

    reports = store.list_reports("alice")
    assert len(reports) == 1
    assert reports[0]["title"] == "Q1 Report"
    assert reports[0]["content"] == "Revenue up 10%."
    assert reports[0]["conversation_id"] == "conv-1"
    assert reports[0]["tags"] == ["q1", "revenue"]


def test_save_report_with_no_tags_round_trips_an_empty_list(store):
    store.save_report(owner="alice", title="T", content="C", conversation_id="c1")
    assert store.list_reports("alice")[0]["tags"] == []


def test_list_reports_never_returns_another_owners_reports(store):
    store.save_report(owner="alice", title="Alice's", content="x", conversation_id="c1")
    store.save_report(owner="bob", title="Bob's", content="y", conversation_id="c2")

    alice_reports = store.list_reports("alice")
    assert len(alice_reports) == 1
    assert alice_reports[0]["title"] == "Alice's"


def test_find_candidates_scope_conversation_filters_by_conversation_id(store):
    store.save_report(owner="alice", title="A", content="x", conversation_id="conv-1")
    store.save_report(owner="alice", title="B", content="y", conversation_id="conv-2")

    candidates = store.find_candidates("alice", scope="conversation", conversation_id="conv-1")

    assert [c["title"] for c in candidates] == ["A"]


def test_find_candidates_scope_all_ignores_conversation_id(store):
    store.save_report(owner="alice", title="A", content="x", conversation_id="conv-1")
    store.save_report(owner="alice", title="B", content="y", conversation_id="conv-2")

    candidates = store.find_candidates("alice", scope="all", conversation_id="conv-1")

    assert {c["title"] for c in candidates} == {"A", "B"}


def test_find_candidates_title_contains_matches_substring_case_insensitively(store):
    store.save_report(owner="alice", title="Client X Quarterly", content="x", conversation_id="c1")
    store.save_report(owner="alice", title="Unrelated", content="y", conversation_id="c1")

    candidates = store.find_candidates("alice", scope="all", title_contains="client x")

    assert [c["title"] for c in candidates] == ["Client X Quarterly"]


def test_find_candidates_scoped_to_owner_never_returns_another_owners_reports(store):
    store.save_report(owner="alice", title="Alice's", content="x", conversation_id="c1")
    store.save_report(owner="bob", title="Bob's", content="y", conversation_id="c1")

    candidates = store.find_candidates("alice", scope="all")

    assert [c["title"] for c in candidates] == ["Alice's"]


def test_delete_reports_removes_exactly_the_matching_rows_and_returns_the_count(store):
    a = store.save_report(owner="alice", title="A", content="x", conversation_id="c1")
    b = store.save_report(owner="alice", title="B", content="y", conversation_id="c1")
    store.save_report(owner="alice", title="C", content="z", conversation_id="c1")

    deleted = store.delete_reports("alice", [a["id"], b["id"]])

    assert deleted == 2
    remaining = store.list_reports("alice")
    assert [r["title"] for r in remaining] == ["C"]


def test_delete_reports_scoped_to_wrong_owner_deletes_nothing(store):
    report = store.save_report(owner="alice", title="Alice's", content="x", conversation_id="c1")

    deleted = store.delete_reports("bob", [report["id"]])

    assert deleted == 0
    assert len(store.list_reports("alice")) == 1


def test_delete_reports_with_empty_id_list_is_a_noop(store):
    store.save_report(owner="alice", title="A", content="x", conversation_id="c1")

    deleted = store.delete_reports("alice", [])

    assert deleted == 0
    assert len(store.list_reports("alice")) == 1


def test_underlying_psycopg_error_raises_reports_store_error(store):
    store._conn.close()

    with pytest.raises(ReportsStoreError):
        store.list_reports("alice")


def test_underlying_psycopg_error_is_not_a_raw_psycopg_error(store):
    store._conn.close()

    with pytest.raises(ReportsStoreError) as exc_info:
        store.save_report(owner="alice", title="A", content="x", conversation_id="c1")
    assert not isinstance(exc_info.value, psycopg.Error)


# -- source question / SQL (openspec change add-golden-bucket) ---------------


def test_save_report_records_source_question_and_sql_for_review(store):
    saved = store.save_report(
        owner="alice", title="T", content="C", conversation_id="c1",
        source_question="Revenue by category?", source_sql=["SELECT 1", "SELECT 2"],
    )

    review = store.get_report_for_review(saved["id"])

    assert review["source_question"] == "Revenue by category?"
    assert review["source_sql"] == ["SELECT 1", "SELECT 2"]
    assert review["content"] == "C"


def test_save_report_without_source_records_none(store):
    saved = store.save_report(owner="alice", title="T", content="C", conversation_id="c1")

    review = store.get_report_for_review(saved["id"])

    assert review["source_question"] is None
    assert review["source_sql"] is None


def test_list_reports_output_is_unchanged_by_source_columns(store):
    store.save_report(
        owner="alice", title="T", content="C", conversation_id="c1",
        source_question="Q?", source_sql=["SELECT 1"],
    )

    (listed,) = store.list_reports("alice")

    assert "source_sql" not in listed
    assert "source_question" not in listed


def test_get_report_for_review_unknown_id_returns_none(store):
    assert store.get_report_for_review(999999) is None


def test_list_reports_for_review_spans_owners_and_flags_source_sql(store):
    store.save_report(owner="alice", title="A", content="x", conversation_id="c1", source_sql=["SELECT 1"])
    store.save_report(owner="bob", title="B", content="y", conversation_id="c2")

    listed = {r["title"]: r for r in store.list_reports_for_review()}

    assert set(listed) == {"A", "B"}
    assert listed["A"]["has_source_sql"] is True
    assert listed["B"]["has_source_sql"] is False
    assert listed["A"]["owner"] == "alice"


# -- rename (npx @fission-ai/openspec@1.14.1 change add-report-rename) --------------------------------


def test_rename_report_changes_only_the_title_and_returns_old_and_new(store):
    report = store.save_report(
        owner="alice", title="Weekly sales", content="Revenue up.", conversation_id="c1", tags=["sales"]
    )
    before = store.list_reports("alice")[0]

    result = store.rename_report("alice", report["id"], "Q3 weekly sales")

    assert result == {"id": report["id"], "old_title": "Weekly sales", "title": "Q3 weekly sales"}
    after = store.list_reports("alice")[0]
    assert after == {**before, "title": "Q3 weekly sales"}


def test_rename_report_trims_surrounding_whitespace(store):
    report = store.save_report(owner="alice", title="Old", content="x", conversation_id="c1")

    result = store.rename_report("alice", report["id"], "  Q3 churn  ")

    assert result["title"] == "Q3 churn"
    assert store.list_reports("alice")[0]["title"] == "Q3 churn"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_rename_report_rejects_a_blank_title_and_changes_nothing(store, blank):
    report = store.save_report(owner="alice", title="Old", content="x", conversation_id="c1")

    with pytest.raises(ValueError):
        store.rename_report("alice", report["id"], blank)

    assert store.list_reports("alice")[0]["title"] == "Old"


def test_rename_report_allows_a_duplicate_title(store):
    store.save_report(owner="alice", title="Sales", content="x", conversation_id="c1")
    other = store.save_report(owner="alice", title="Other", content="y", conversation_id="c1")

    result = store.rename_report("alice", other["id"], "Sales")

    assert result["title"] == "Sales"
    assert [r["title"] for r in store.list_reports("alice")] == ["Sales", "Sales"]


def test_rename_report_of_another_owners_report_returns_none_and_changes_nothing(store):
    bobs = store.save_report(owner="bob", title="Bob's", content="y", conversation_id="c2")

    assert store.rename_report("alice", bobs["id"], "Mine now") is None
    assert store.list_reports("bob")[0]["title"] == "Bob's"


def test_rename_report_of_a_missing_id_returns_none(store):
    report = store.save_report(owner="alice", title="A", content="x", conversation_id="c1")

    assert store.rename_report("alice", report["id"] + 1000, "New") is None


def test_rename_report_underlying_psycopg_error_raises_reports_store_error(store):
    report = store.save_report(owner="alice", title="A", content="x", conversation_id="c1")
    store._conn.close()

    with pytest.raises(ReportsStoreError):
        store.rename_report("alice", report["id"], "New")
