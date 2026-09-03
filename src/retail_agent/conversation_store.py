import logging

import psycopg
from psycopg.rows import dict_row

from retail_agent.errors import ConversationStoreError

logger = logging.getLogger(__name__)

_DDL_CONVERSATIONS = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    turn_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, turn_index)
)
"""


class ConversationStore:
    """Postgres-backed store for the durable conversation transcript
    (docs/design.md §3 "Conversation Store"). Owns its own
    `psycopg.Connection` directly, the same way `ReportsStore` and
    `BigQueryTool` own theirs — no ORM.

    Deliberately shares `ReportsStore`'s connection string
    (`REPORTS_DATABASE_URL`) rather than introducing a second env var: the
    design places both stores on the same Cloud SQL instance, and there's
    nothing here that needs a separate database.

    Deliberately has no `resilience.bounded_backoff` retry, for the same
    reason `ReportsStore` doesn't: a local/managed Postgres instance in
    this deployment shape doesn't have the "brief rate-limit or server
    hiccup, retry the same call" failure mode a third-party network API
    does. See docs/implementation-notes.md.
    """

    def __init__(self, database_url: str) -> None:
        """Connect to Postgres (creating the `conversations`/`messages`
        tables if needed).

        Args:
            database_url: A `postgresql://user:password@host:port/dbname`
                connection string — the same one `ReportsStore` uses.
        """
        self._conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        self._call(lambda: self._conn.execute(_DDL_CONVERSATIONS), stage="init")
        self._call(lambda: self._conn.execute(_DDL_MESSAGES), stage="init")

    def _call(self, fn, *, stage: str):
        """Run a psycopg call, classifying any `psycopg.Error` into
        `ConversationStoreError`.

        Args:
            fn: Zero-argument callable performing the actual psycopg call.
            stage: Short label for the `conversation_store_call_failed` log
                line.

        Returns:
            Whatever `fn()` returns, unchanged, on success.

        Raises:
            ConversationStoreError: `fn()` raised a `psycopg.Error`.
        """
        try:
            return fn()
        except psycopg.Error as exc:
            logger.warning(
                "conversation_store_call_failed", extra={"stage": stage, "error_class": type(exc).__name__}
            )
            raise ConversationStoreError(str(exc)) from exc

    def _row_to_dict(self, row: dict) -> dict:
        """Convert one row into a dict, rendering any `datetime` columns as
        ISO 8601 strings.

        Args:
            row: A row as `psycopg`'s `dict_row` row factory returns it.

        Returns:
            A plain dict, timestamp columns as ISO 8601 strings.
        """
        data = dict(row)
        for key in ("created_at", "updated_at"):
            if key in data and data[key] is not None:
                data[key] = data[key].isoformat()
        return data

    def next_turn_index(self, conversation_id: str) -> int:
        """The next `turn_index` to use when appending to `conversation_id`.

        Args:
            conversation_id: The LangGraph thread id.

        Returns:
            One past the highest `turn_index` already stored for this
            conversation, or `0` if nothing has been stored yet.
        """

        def _select():
            row = self._conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_index FROM messages WHERE conversation_id = %s",
                (conversation_id,),
            ).fetchone()
            return row["next_index"]

        return self._call(_select, stage="next_turn_index")

    def append_message(self, *, conversation_id: str, owner: str, turn_index: int, role: str, content: str) -> None:
        """Append one message to a conversation's durable transcript.

        Creates the parent `conversations` row on first use (and bumps its
        `updated_at` on every call after that). Callers must only pass
        already PII-stripped `content` — this store applies no PII
        filtering of its own, the same trust boundary `ReportsStore`
        assumes for report content.

        Args:
            conversation_id: The LangGraph thread id this message belongs to.
            owner: The conversation's owner — scopes all later reads.
            turn_index: This message's position in the conversation;
                typically from `next_turn_index`, incremented by the caller
                for each subsequent message in the same turn.
            role: `"user"` or `"assistant"`.
            content: The message's plain-text content, already PII-stripped.
        """

        def _insert():
            self._conn.execute(
                "INSERT INTO conversations (conversation_id, owner) VALUES (%s, %s) "
                "ON CONFLICT (conversation_id) DO UPDATE SET updated_at = now()",
                (conversation_id, owner),
            )
            self._conn.execute(
                "INSERT INTO messages (conversation_id, turn_index, role, content) VALUES (%s, %s, %s, %s)",
                (conversation_id, turn_index, role, content),
            )

        self._call(_insert, stage="append_message")

    def get_messages(self, conversation_id: str, owner: str) -> list[dict]:
        """Fetch a conversation's transcript, in order.

        Ownership is re-asserted here from `owner`, exactly as
        `ReportsStore` re-asserts it at delete time — `conversation_id` is
        a key, never an authorization by itself.

        Args:
            conversation_id: The LangGraph thread id to fetch.
            owner: The requesting user's identity — a conversation
                belonging to a different owner returns no rows.

        Returns:
            A list of `{turn_index, role, content, created_at}` dicts,
            ordered by `turn_index` ascending (oldest first).
        """

        def _select():
            rows = self._conn.execute(
                "SELECT m.turn_index, m.role, m.content, m.created_at FROM messages m "
                "JOIN conversations c ON c.conversation_id = m.conversation_id "
                "WHERE m.conversation_id = %s AND c.owner = %s ORDER BY m.turn_index ASC",
                (conversation_id, owner),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

        return self._call(_select, stage="get_messages")

    def list_conversations(self, owner: str) -> list[dict]:
        """List every conversation belonging to `owner`.

        Args:
            owner: The requesting user's identity — results are scoped to
                this owner only, never cross-user.

        Returns:
            A list of `{conversation_id, created_at, updated_at}` dicts,
            most recently updated first.
        """

        def _select():
            rows = self._conn.execute(
                "SELECT conversation_id, created_at, updated_at FROM conversations "
                "WHERE owner = %s ORDER BY updated_at DESC",
                (owner,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

        return self._call(_select, stage="list_conversations")
