from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.models import Conversation, ConversationStatus, Message, MessageRole


def test_conversation_created_with_expected_defaults(db_session):
    conversation = Conversation()
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    assert conversation.id
    assert conversation.session_id
    assert conversation.id != conversation.session_id
    assert conversation.status == ConversationStatus.ACTIVE.value
    assert isinstance(conversation.created_at, datetime)
    assert isinstance(conversation.updated_at, datetime)


def test_conversation_session_ids_are_unique_per_instance(db_session):
    a = Conversation()
    b = Conversation()
    db_session.add_all([a, b])
    db_session.commit()

    assert a.id != b.id
    assert a.session_id != b.session_id


def test_duplicate_session_id_is_rejected(db_session):
    a = Conversation(session_id="same-session-id")
    db_session.add(a)
    db_session.commit()

    b = Conversation(session_id="same-session-id")
    db_session.add(b)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_message_belongs_to_conversation_via_relationship(db_session):
    conversation = Conversation()
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    message = Message(conversation_id=conversation.id, role=MessageRole.USER.value, content="Hello")
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    db_session.refresh(conversation)

    assert message.conversation_id == conversation.id
    assert message.conversation.id == conversation.id
    assert len(conversation.messages) == 1
    assert conversation.messages[0].content == "Hello"


def test_messages_are_ordered_chronologically_on_the_relationship(db_session):
    conversation = Conversation()
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    first = Message(conversation_id=conversation.id, role=MessageRole.USER.value, content="first")
    db_session.add(first)
    db_session.commit()

    second = Message(conversation_id=conversation.id, role=MessageRole.ASSISTANT.value, content="second")
    db_session.add(second)
    db_session.commit()

    db_session.refresh(conversation)
    contents = [m.content for m in conversation.messages]
    assert contents == ["first", "second"]


def test_message_timestamp_is_recent_utc(db_session):
    conversation = Conversation()
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    before = datetime.now(timezone.utc)
    message = Message(conversation_id=conversation.id, role=MessageRole.USER.value, content="hi")
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    after = datetime.now(timezone.utc)

    # SQLite drops tzinfo on round-trip, so compare naive-UTC values.
    created = message.created_at
    if created.tzinfo is not None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)

    assert before.replace(tzinfo=None) - timedelta(seconds=5) <= created <= after.replace(tzinfo=None) + timedelta(seconds=5)
