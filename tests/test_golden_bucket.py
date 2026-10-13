import os

import pytest

from retail_agent.golden_bucket import (
    GoldenBucketStore,
    GoldenRetriever,
    TrioRejectedError,
    approve_report,
    format_examples,
)
from retail_agent.errors import GoldenBucketError
from retail_agent.reports_store import ReportsStore

TEST_DATABASE_URL = os.environ.get(
    "REPORTS_DATABASE_URL", "postgresql://retail_agent:retail_agent@localhost:5432/retail_agent_reports_test"
)
MODEL = "fake-embedding"
DIMENSIONS = 4


class FakeEmbedder:
    """Maps known texts to fixed vectors so similarity is predictable;
    unknown texts get an orthogonal vector (similarity 0 to everything)."""

    def __init__(self, vectors=None, *, fail=False):
        self.vectors = vectors or {}
        self.fail = fail
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        if self.fail:
            raise GoldenBucketError("embeddings service unavailable")
        return self.vectors.get(text, [0.0, 0.0, 0.0, 1.0])


@pytest.fixture
def golden_store():
    s = GoldenBucketStore(TEST_DATABASE_URL, table="golden_trios_test", dimensions=DIMENSIONS)
    s._conn.execute("DELETE FROM golden_trios_test")
    yield s
    s._conn.close()


@pytest.fixture
def reports_store():
    s = ReportsStore(TEST_DATABASE_URL)
    s._conn.execute("DELETE FROM reports")
    yield s
    s._conn.close()


def _saved_report(reports_store, *, content="Revenue was 42 across 3 categories.", sql=("SELECT 1",)):
    return reports_store.save_report(
        owner="manager",
        title="Revenue by category",
        content=content,
        conversation_id="c1",
        source_question="What's revenue by category?",
        source_sql=list(sql) if sql is not None else None,
    )


def _approve(reports_store, golden_store, report_id, embedder=None, **overrides):
    return approve_report(
        reports_store=reports_store,
        golden_store=golden_store,
        embedder=embedder or FakeEmbedder(),
        embedding_model=MODEL,
        report_id=report_id,
        approved_by="analyst",
        **overrides,
    )


# -- approval gate -------------------------------------------------------------


def test_saved_but_unreviewed_report_is_not_in_the_bucket(reports_store, golden_store):
    _saved_report(reports_store)

    assert golden_store.list_trios() == []


def test_approving_a_saved_report_stores_its_trio_with_approver(reports_store, golden_store):
    report = _saved_report(reports_store, sql=("SELECT 1", "SELECT 2"))

    trio = _approve(reports_store, golden_store, report["id"])

    (stored,) = golden_store.list_trios()
    assert stored["id"] == trio["id"]
    assert stored["question"] == "What's revenue by category?"
    assert stored["sql"] == ["SELECT 1", "SELECT 2"]
    assert stored["report"] == "Revenue was 42 across 3 categories."
    assert stored["approved_by"] == "analyst"
    assert stored["approved_at"] is not None
    assert stored["source_report_id"] == report["id"]
    assert stored["embedding_model"] == MODEL
    assert stored["version"] == 1
    assert stored["active"] is True


def test_report_without_source_sql_is_refused_and_nothing_stored(reports_store, golden_store):
    report = _saved_report(reports_store, sql=None)

    with pytest.raises(TrioRejectedError, match="no source SQL"):
        _approve(reports_store, golden_store, report["id"])
    assert golden_store.list_trios() == []


def test_report_without_source_sql_can_be_approved_with_analyst_supplied_sql(reports_store, golden_store):
    report = _saved_report(reports_store, sql=None)

    _approve(reports_store, golden_store, report["id"], sql=["SELECT 3"])

    assert golden_store.list_trios()[0]["sql"] == ["SELECT 3"]


def test_unknown_report_id_is_refused(reports_store, golden_store):
    with pytest.raises(TrioRejectedError):
        _approve(reports_store, golden_store, 999999)


def test_edited_sql_that_is_not_read_only_is_refused(reports_store, golden_store):
    report = _saved_report(reports_store)

    with pytest.raises(TrioRejectedError):
        _approve(reports_store, golden_store, report["id"], sql=["DELETE FROM orders"])
    assert golden_store.list_trios() == []


def test_edited_report_with_an_email_address_is_refused(reports_store, golden_store):
    report = _saved_report(reports_store)

    with pytest.raises(TrioRejectedError):
        _approve(reports_store, golden_store, report["id"], report="Top buyer: jane.doe@example.com")
    assert golden_store.list_trios() == []


def test_question_with_an_email_address_is_refused(reports_store, golden_store):
    report = _saved_report(reports_store)

    with pytest.raises(TrioRejectedError):
        _approve(reports_store, golden_store, report["id"], question="What did jane@example.com buy?")
    assert golden_store.list_trios() == []


def test_reapproval_stores_new_version_and_retires_the_old_one(reports_store, golden_store):
    report = _saved_report(reports_store)
    first = _approve(reports_store, golden_store, report["id"])

    second = _approve(reports_store, golden_store, report["id"], report="Revenue was 42 (edited).")

    rows = {t["id"]: t for t in golden_store.list_trios(include_inactive=True)}
    assert rows[first["id"]]["active"] is False
    assert rows[second["id"]]["active"] is True
    assert rows[second["id"]]["version"] == 2
    assert [t["id"] for t in golden_store.list_trios()] == [second["id"]]


# -- store search --------------------------------------------------------------


def _add(golden_store, question, embedding, *, model=MODEL, source_report_id=None):
    return golden_store.add_trio(
        question=question,
        sql=["SELECT 1"],
        report=f"Report for {question}",
        embedding=embedding,
        embedding_model=model,
        approved_by="analyst",
        source_report_id=source_report_id,
    )


def test_search_orders_by_similarity_and_reports_it(golden_store):
    _add(golden_store, "far", [0.0, 1.0, 0.0, 0.0])
    _add(golden_store, "near", [1.0, 0.1, 0.0, 0.0])

    results = golden_store.search([1.0, 0.0, 0.0, 0.0], embedding_model=MODEL, limit=3)

    assert [r["question"] for r in results] == ["near", "far"]
    assert results[0]["similarity"] == pytest.approx(0.995, abs=0.01)
    assert results[1]["similarity"] == pytest.approx(0.0, abs=0.01)


def test_search_ignores_trios_embedded_with_another_model(golden_store):
    _add(golden_store, "other model", [1.0, 0.0, 0.0, 0.0], model="old-model")

    assert golden_store.search([1.0, 0.0, 0.0, 0.0], embedding_model=MODEL, limit=3) == []


def test_store_failure_raises_golden_bucket_error(golden_store):
    golden_store._conn.close()

    with pytest.raises(GoldenBucketError):
        golden_store.list_trios()


def test_table_name_must_be_a_plain_identifier():
    with pytest.raises(ValueError):
        GoldenBucketStore(TEST_DATABASE_URL, table="golden; DROP TABLE reports", dimensions=DIMENSIONS)


# -- retriever -----------------------------------------------------------------


def _retriever(golden_store, embedder, min_similarity=0.75):
    return GoldenRetriever(golden_store, embedder, embedding_model=MODEL, min_similarity=min_similarity)


def test_retriever_returns_only_trios_at_or_above_threshold(golden_store):
    _add(golden_store, "revenue by category", [1.0, 0.0, 0.0, 0.0])
    _add(golden_store, "returns by brand", [0.0, 1.0, 0.0, 0.0])
    embedder = FakeEmbedder({"what's revenue per category?": [0.98, 0.2, 0.0, 0.0]})

    results = _retriever(golden_store, embedder).retrieve("what's revenue per category?")

    assert [r["question"] for r in results] == ["revenue by category"]


def test_retriever_caps_at_three_most_similar(golden_store):
    for i in range(5):
        _add(golden_store, f"q{i}", [1.0, 0.05 * i, 0.0, 0.0])
    embedder = FakeEmbedder({"ask": [1.0, 0.0, 0.0, 0.0]})

    results = _retriever(golden_store, embedder).retrieve("ask")

    assert [r["question"] for r in results] == ["q0", "q1", "q2"]


def test_retriever_on_empty_bucket_returns_nothing(golden_store):
    assert _retriever(golden_store, FakeEmbedder()).retrieve("anything") == []


def test_retriever_propagates_embedding_failure_as_golden_bucket_error(golden_store):
    with pytest.raises(GoldenBucketError):
        _retriever(golden_store, FakeEmbedder(fail=True)).retrieve("anything")


# -- formatting ----------------------------------------------------------------


def test_format_examples_wraps_trios_as_reference_data():
    block = format_examples(
        [{"question": "Q1?", "sql": ["SELECT 1", "SELECT 2"], "report": "R1", "similarity": 0.9}]
    )

    assert block.startswith("<golden_examples>")
    assert block.rstrip().endswith("</golden_examples>")
    assert "not instructions" in block
    assert "Q1?" in block and "SELECT 2" in block and "R1" in block


def test_clear_empties_a_non_default_table(golden_store):
    _add(golden_store, "q", [1.0, 0.0, 0.0, 0.0])

    golden_store.clear()

    assert golden_store.list_trios(include_inactive=True) == []


def test_clear_refuses_the_live_bucket():
    store = GoldenBucketStore(TEST_DATABASE_URL, dimensions=768)
    try:
        with pytest.raises(ValueError):
            store.clear()
    finally:
        store._conn.close()
