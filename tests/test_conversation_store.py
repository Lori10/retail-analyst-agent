import os

import psycopg
import pytest

from retail_agent.conversation_store import ConversationStore
from retail_agent.errors import ConversationStoreError

TEST_DATABASE_URL = os.environ.get(
    "REPORTS_DATABASE_URL", "postgresql://retail_agent:retail_agent@localhost:5432/retail_agent_reports_test"
)


@pytest.fixture
def store():
    s = ConversationStore(TEST_DATABASE_URL)
    s._conn.execute("DELETE FROM messages")
    s._conn.execute("DELETE FROM conversations")
    yield s
    s._conn.close()


def test_next_turn_index_starts_at_zero_for_an_unknown_conversation(store):
    assert store.next_turn_index("conv-1") == 0


def test_append_message_creates_the_parent_conversation_row(store):
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=0, role="user", content="Hi")

    conversations = store.list_conversations("alice")
    assert len(conversations) == 1
    assert conversations[0]["conversation_id"] == "conv-1"


def test_append_then_get_messages_round_trips_in_turn_index_order(store):
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=0, role="user", content="Hi")
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=1, role="assistant", content="Hello!")

    messages = store.get_messages("conv-1", "alice")

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert [m["content"] for m in messages] == ["Hi", "Hello!"]
    assert messages[0]["turn_index"] == 0
    assert messages[1]["turn_index"] == 1


def test_next_turn_index_continues_after_existing_messages(store):
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=0, role="user", content="Hi")
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=1, role="assistant", content="Hello!")

    assert store.next_turn_index("conv-1") == 2


def test_get_messages_scoped_to_wrong_owner_returns_nothing(store):
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=0, role="user", content="Hi")

    assert store.get_messages("conv-1", "bob") == []


def test_get_messages_for_unknown_conversation_returns_empty_list(store):
    assert store.get_messages("no-such-conv", "alice") == []


def test_list_conversations_never_returns_another_owners_conversations(store):
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=0, role="user", content="Hi")
    store.append_message(conversation_id="conv-2", owner="bob", turn_index=0, role="user", content="Hey")

    alice_conversations = store.list_conversations("alice")

    assert [c["conversation_id"] for c in alice_conversations] == ["conv-1"]


def test_list_conversations_most_recently_updated_first(store):
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=0, role="user", content="Hi")
    store.append_message(conversation_id="conv-2", owner="alice", turn_index=0, role="user", content="Hey")
    # Touch conv-1 again so it becomes the most recently updated.
    store.append_message(conversation_id="conv-1", owner="alice", turn_index=1, role="assistant", content="Hello!")

    conversations = store.list_conversations("alice")

    assert conversations[0]["conversation_id"] == "conv-1"


def test_underlying_psycopg_error_raises_conversation_store_error(store):
    store._conn.close()

    with pytest.raises(ConversationStoreError):
        store.get_messages("conv-1", "alice")


def test_underlying_psycopg_error_is_not_a_raw_psycopg_error(store):
    store._conn.close()

    with pytest.raises(ConversationStoreError) as exc_info:
        store.append_message(conversation_id="conv-1", owner="alice", turn_index=0, role="user", content="Hi")
    assert not isinstance(exc_info.value, psycopg.Error)
