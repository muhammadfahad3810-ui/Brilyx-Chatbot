from sqlalchemy.orm import Session

from backend.app.ai.base import ChatMessage
from backend.app.models import Conversation, ConversationStatus, Message


class ConversationNotFoundError(Exception):
    """Raised when a conversation_id does not match any stored conversation."""


class ConversationClosedError(Exception):
    """Raised when a message is sent to a conversation that is already closed."""


class ConversationAccessDeniedError(Exception):
    """Raised when the caller does not present a valid access token for this conversation.

    Deliberately indistinguishable, at the HTTP layer, from
    ConversationNotFoundError (both map to 404 — see routers/conversations.py):
    a visitor probing conversation IDs must not be able to tell "wrong
    token" apart from "doesn't exist." See docs/security.md.
    """


def create_conversation(db: Session) -> Conversation:
    conversation = Conversation()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_conversation(db: Session, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise ConversationNotFoundError(conversation_id)
    return conversation


def verify_conversation_access(conversation: Conversation, token: str | None) -> None:
    """Verify the caller actually owns this conversation before returning its data.

    `Conversation.session_id` (a separate random UUID4 generated at
    creation, already returned alongside `conversation_id` by every
    endpoint that creates/reopens a conversation — see
    `ConversationStatusResponse`) doubles as this lightweight access
    token: `conversation_id` is the public resource identifier used in
    URLs, `session_id` is the secret companion that proves ownership. A
    visitor who only learns another visitor's `conversation_id` (e.g. via
    a leaked URL or log line) cannot access their conversation without
    also knowing this second, separately-issued value.

    This is intentionally not full authentication (Phase 9 spec: "Do not
    introduce full user authentication") — it's the minimum practical
    protection against IDOR for an anonymous public chatbot. See
    docs/security.md for the full threat-model discussion.
    """
    if not token or token != conversation.session_id:
        raise ConversationAccessDeniedError(conversation.id)


def ensure_active(conversation: Conversation) -> None:
    if conversation.status != ConversationStatus.ACTIVE.value:
        raise ConversationClosedError(conversation.id)


def close_conversation(db: Session, conversation: Conversation) -> Conversation:
    """Close a conversation. Idempotent: closing an already-closed conversation is a no-op."""
    if conversation.status != ConversationStatus.CLOSED.value:
        conversation.status = ConversationStatus.CLOSED.value
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
    return conversation


def get_recent_history(db: Session, conversation_id: str, limit: int) -> list[Message]:
    """Return up to `limit` most recent messages for a conversation, oldest first."""
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def add_message(db: Session, conversation: Conversation, role: str, content: str) -> Message:
    message = Message(conversation_id=conversation.id, role=role, content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def to_chat_messages(messages: list[Message]) -> list[ChatMessage]:
    return [ChatMessage(role=message.role, content=message.content) for message in messages]
