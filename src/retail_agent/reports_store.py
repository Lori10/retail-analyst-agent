import logging

import psycopg
from psycopg.rows import dict_row

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


class ReportsStore:
    """Postgres-backed store for the "Saved Reports" library (docs/design.md
    §3). Owns its own `psycopg.Connection` directly, the same way
    `BigQueryTool` owns its own `bigquery.Client`. Uses `psycopg` (v3)
    directly, with no ORM — matches this codebase's existing preference for
    a thin wrapper over the driver rather than an abstraction layer on top
    of it.

    Deliberately has no `resilience.bounded_backoff` retry, unlike
    `bq_tool.py`/`llm_provider.py`: a local/managed Postgres instance in
    this deployment shape doesn't have the same "brief rate-limit or
    server hiccup, retry the same call" failure mode a third-party network
    API does. See docs/implementation-notes.md.
    """

    def __init__(self, database_url: str) -> None:
        """Connect to Postgres (creating the `reports` table if needed).

        Args:
            database_url: A `postgresql://user:password@host:port/dbname`
                connection string.
        """
        self._conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        self._call(lambda: self._conn.execute(_DDL), stage="init")

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
        """
        data = dict(row)
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
    ) -> dict:
        """Persist a new report.

        Args:
            owner: The saving user's identity (scopes all later reads/deletes).
            title: Report title.
            content: Report body text.
            conversation_id: The LangGraph thread id this report was created in.
            tags: Optional list of tag strings. Stored as a comma-joined
                string — a tag containing a literal comma isn't supported,
                an accepted prototype trade-off (docs/implementation-notes.md).

        Returns:
            The saved report as a dict, including its assigned `id` and the
            server-assigned `created_at`.
        """
        tags_str = ",".join(tags) if tags else ""

        def _insert():
            row = self._conn.execute(
                "INSERT INTO reports (owner, title, content, conversation_id, tags) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                (owner, title, content, conversation_id, tags_str),
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
        doesn't actually belong to `owner` (docs/implementation-notes.md).

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
