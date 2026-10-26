import logging

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from retail_agent.errors import ReportsStoreError

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tags TEXT NOT NULL DEFAULT ''
)
"""

# Added by the Golden Bucket change: the question and SQL that produced a
# report, recorded by the graph at save time (never supplied by the model)
# so an analyst can later promote the report into a Golden Bucket trio.
# Separate ALTERs so databases created before the change pick them up.
_DDL_SOURCE_COLUMNS = (
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS source_question TEXT",
    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS source_sql JSONB",
)

_SOURCE_COLUMNS = ("source_question", "source_sql")

# `reports.id` is SERIAL (int4); a larger id would make Postgres raise,
# which `_call` would misreport as the store being unreachable.
_MAX_REPORT_ID = 2**31 - 1

RENAME_BLANK_TITLE = "The new title can't be blank."
RENAME_NOT_FOUND = "Report not found."


def _coerce_report_id(value) -> int | None:
    """Accept only a value that unambiguously names one report id.

    Model tool arguments arrive as JSON, so a whole-number float (`2.0`) or
    a digit string (`"2"`) is accepted. Anything that would need rounding
    or truncation (`2.7`), a bool (`True` is an `int` in Python), or an id
    outside the column's range returns `None`, so a malformed argument can
    never rename a report the user didn't pick.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        value = int(value) if value.is_integer() else None
    elif isinstance(value, str):
        value = int(value) if value.isdecimal() else None
    if not isinstance(value, int) or not 1 <= value <= _MAX_REPORT_ID:
        return None
    return value


def rename_report_payload(store: "ReportsStore", owner: str, report_id, new_title) -> dict:
    """Run a model- or client-requested rename and describe the outcome.

    Shared by the CLI graph and the MCP server so both validate arguments
    and word their errors the same way. Blank and not-found are returned,
    not raised: for the graph they are ordinary results the model relays,
    and an `AgentError` would end the turn or spend the SQL self-correct
    budget. `ReportsStoreError` still propagates.

    Args:
        store: The reports store (or a test double with `rename_report`).
        owner: Requesting user's identity.
        report_id: The id exactly as the tool call supplied it.
        new_title: The title exactly as the tool call supplied it; `None`
            or a non-string counts as blank.

    Returns:
        `{"renamed": True, "id", "old_title", "title"}`, or
        `{"error": RENAME_BLANK_TITLE | RENAME_NOT_FOUND}`.
    """
    if not isinstance(new_title, str) or not new_title.strip():
        return {"error": RENAME_BLANK_TITLE}
    coerced_id = _coerce_report_id(report_id)
    if coerced_id is None:
        return {"error": RENAME_NOT_FOUND}
    renamed = store.rename_report(owner, coerced_id, new_title)
    if renamed is None:
        return {"error": RENAME_NOT_FOUND}
    return {"renamed": True, **renamed}


class ReportsStore:
    """Postgres-backed store for the "Saved Reports" library (the OpenSpec
    baseline design). Owns its own `psycopg.Connection` directly, the same way
    `BigQueryTool` owns its own `bigquery.Client`. Uses `psycopg` (v3)
    directly, with no ORM — matches this codebase's existing preference for
    a thin wrapper over the driver rather than an abstraction layer on top
    of it.

    Deliberately has no `resilience.bounded_backoff` retry, unlike
    `bq_tool.py`/`llm_provider.py`: a local/managed Postgres instance in
    this deployment shape doesn't have the same "brief rate-limit or
    server hiccup, retry the same call" failure mode a third-party network
    API does. See the OpenSpec baseline design.
    """

    def __init__(self, database_url: str) -> None:
        """Connect to Postgres (creating the `reports` table if needed).

        Args:
            database_url: A `postgresql://user:password@host:port/dbname`
                connection string.
        """
        self._conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)

        def _init():
            self._conn.execute(_DDL)
            for ddl in _DDL_SOURCE_COLUMNS:
                self._conn.execute(ddl)

        self._call(_init, stage="init")

    def _call(self, fn, *, stage: str):
        """Run a psycopg call, classifying any `psycopg.Error` into
        `ReportsStoreError`.

        Args:
            fn: Zero-argument callable performing the actual psycopg call.
            stage: Short label for the `reports_store_call_failed` log line.

        Returns:
            Whatever `fn()` returns, unchanged, on success.

        Raises:
            ReportsStoreError: `fn()` raised a `psycopg.Error`.
        """
        try:
            return fn()
        except psycopg.Error as exc:
            logger.warning("reports_store_call_failed", extra={"stage": stage, "error_class": type(exc).__name__})
            raise ReportsStoreError(str(exc)) from exc

    def _row_to_dict(self, row: dict) -> dict:
        """Convert one `reports` row into a dict, splitting `tags` back into
        a list and rendering `created_at` as an ISO 8601 string.

        Args:
            row: A row from the `reports` table, as `psycopg`'s
                `dict_row` row factory returns it.

        Returns:
            A dict with `tags` as `list[str]` (empty list for an empty
            string) and `created_at` as a plain ISO 8601 string, rather
            than the raw comma-joined column value / `datetime` object.
            `source_question`/`source_sql` are left out — they're review
            material for `get_report_for_review`, not part of what the
            agent or an MCP client sees.
        """
        data = {k: v for k, v in row.items() if k not in _SOURCE_COLUMNS}
        data["tags"] = data["tags"].split(",") if data["tags"] else []
        data["created_at"] = data["created_at"].isoformat()
        return data

    def save_report(
        self,
        *,
        owner: str,
        title: str,
        content: str,
        conversation_id: str,
        tags: list[str] | None = None,
        source_question: str | None = None,
        source_sql: list[str] | None = None,
    ) -> dict:
        """Persist a new report.

        Args:
            owner: The saving user's identity (scopes all later reads/deletes).
            title: Report title.
            content: Report body text.
            conversation_id: The LangGraph thread id this report was created in.
            tags: Optional list of tag strings. Stored as a comma-joined
                string — a tag containing a literal comma isn't supported,
                an accepted prototype trade-off (the OpenSpec baseline design).
            source_question: The user message that led to the report, as
                captured by the graph; `None` when unknown (MCP saves).
            source_sql: The successful queries behind the report, in run
                order, as captured by the graph; `None` when there were
                none or they're unknown (MCP saves).

        Returns:
            The saved report as a dict, including its assigned `id` and the
            server-assigned `created_at`.
        """
        tags_str = ",".join(tags) if tags else ""
        sql_json = Jsonb(source_sql) if source_sql else None

        def _insert():
            row = self._conn.execute(
                "INSERT INTO reports (owner, title, content, conversation_id, tags, source_question, source_sql) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (owner, title, content, conversation_id, tags_str, source_question, sql_json),
            ).fetchone()
            return self._row_to_dict(row)

        return self._call(_insert, stage="save_report")

    def list_reports(self, owner: str) -> list[dict]:
        """List every report belonging to `owner`.

        Args:
            owner: The requesting user's identity — results are scoped to
                this owner only, never cross-user.

        Returns:
            A list of report dicts, most recently created first.
        """

        def _select():
            rows = self._conn.execute(
                "SELECT * FROM reports WHERE owner = %s ORDER BY created_at DESC", (owner,)
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

        return self._call(_select, stage="list_reports")

    def get_report_for_review(self, report_id: int) -> dict | None:
        """Fetch one report, including its source question and SQL, for
        analyst review before Golden Bucket approval.

        Deliberately not owner-scoped: the analyst curating the Golden
        Bucket is a different person from the manager who saved the report.
        Only `scripts/approve_trio.py` calls this — it is never exposed as
        an agent or MCP tool, so the owner scoping of everything a manager
        can reach is unchanged.

        Args:
            report_id: The report's id.

        Returns:
            The report as a dict with `source_question` (str or `None`) and
            `source_sql` (list of str or `None`) added, or `None` if no
            report has that id.
        """

        def _select():
            row = self._conn.execute("SELECT * FROM reports WHERE id = %s", (report_id,)).fetchone()
            if row is None:
                return None
            data = self._row_to_dict(row)
            data["source_question"] = row["source_question"]
            data["source_sql"] = row["source_sql"]
            return data

        return self._call(_select, stage="get_report_for_review")

    def list_reports_for_review(self, limit: int = 50) -> list[dict]:
        """List recent reports across all owners for an analyst choosing
        what to promote into the Golden Bucket. Same caveat as
        `get_report_for_review`: never exposed to the agent or MCP.

        Args:
            limit: Maximum number of reports, most recent first.

        Returns:
            Dicts with `id`, `owner`, `title`, `created_at` and
            `has_source_sql`.
        """

        def _select():
            rows = self._conn.execute(
                "SELECT id, owner, title, created_at, source_sql IS NOT NULL AS has_source_sql "
                "FROM reports ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
            return [{**row, "created_at": row["created_at"].isoformat()} for row in rows]

        return self._call(_select, stage="list_reports_for_review")

    def rename_report(self, owner: str, report_id: int, new_title: str) -> dict | None:
        """Change one report's title, scoped to `owner`.

        Owner scoping is applied in the `UPDATE` itself, as in
        `delete_reports`, so a missing id and another owner's id are the
        same "not found" case and the caller can't tell them apart. The old
        title comes back from the same statement, so no concurrent rename
        can land between a read and the write.

        Args:
            owner: Requesting user's identity.
            report_id: The report to rename.
            new_title: The new title; leading/trailing whitespace is
                trimmed. Duplicates of another report's title are allowed.

        Returns:
            `{"id", "old_title", "title"}`, or `None` if `owner` has no
            report with that id.

        Raises:
            ValueError: `new_title` is blank after trimming. Raised before
                any database call, so nothing changes.
        """
        title = new_title.strip()
        if not title:
            raise ValueError(RENAME_BLANK_TITLE)

        def _update():
            return self._conn.execute(
                "WITH old AS (SELECT id, title FROM reports WHERE owner = %s AND id = %s FOR UPDATE) "
                "UPDATE reports r SET title = %s FROM old WHERE r.id = old.id "
                "RETURNING r.id, old.title AS old_title, r.title",
                (owner, report_id, title),
            ).fetchone()

        return self._call(_update, stage="rename_report")

    def find_candidates(
        self,
        owner: str,
        *,
        scope: str,
        conversation_id: str | None = None,
        title_contains: str | None = None,
    ) -> list[dict]:
        """Resolve a delete request into the exact reports it would remove.

        Args:
            owner: Requesting user's identity — results are always scoped
                to this owner, never cross-user.
            scope: `"conversation"` to restrict to `conversation_id`, or
                `"all"` to search every report this owner has saved.
            conversation_id: Required when `scope == "conversation"`.
            title_contains: Optional case-insensitive substring filter on
                title (`ILIKE`).

        Returns:
            The matching reports, most recently created first.
        """
        clauses = ["owner = %s"]
        params: list = [owner]
        if scope == "conversation":
            clauses.append("conversation_id = %s")
            params.append(conversation_id)
        if title_contains:
            clauses.append("title ILIKE %s")
            params.append(f"%{title_contains}%")

        query = f"SELECT * FROM reports WHERE {' AND '.join(clauses)} ORDER BY created_at DESC"

        def _select():
            rows = self._conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]

        return self._call(_select, stage="find_candidates")

    def delete_reports(self, owner: str, ids: list[int]) -> int:
        """Delete the given reports, scoped to `owner`.

        Re-asserts owner scoping here too, not just at candidate selection —
        defense in depth against a bug upstream ever passing an id that
        doesn't actually belong to `owner` (the OpenSpec baseline design).

        Args:
            owner: Requesting user's identity — an id belonging to a
                different owner is silently not deleted.
            ids: Report ids to delete.

        Returns:
            The number of rows actually deleted.
        """
        if not ids:
            return 0

        def _delete():
            cursor = self._conn.execute("DELETE FROM reports WHERE owner = %s AND id = ANY(%s)", (owner, ids))
            return cursor.rowcount

        return self._call(_delete, stage="delete_reports")
