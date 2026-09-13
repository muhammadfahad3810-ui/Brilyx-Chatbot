from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.ai.base import AIProviderError
from backend.app.ai.knowledge import KnowledgeLoadError
from backend.app.ai.orchestrator import AIOrchestrator, get_orchestrator
from backend.app.ai.prompts import build_context_section
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.rate_limit import InMemoryRateLimiter
from backend.app.events.notifications import NotificationService
from backend.app.events.service import evaluate_and_persist_events, get_notification_service
from backend.app.intelligence.service import build_context_lines, run_intelligence
from backend.app.leads.service import run_lead_capture
from backend.app.qualification.service import run_qualification
from backend.app.services.conversation_service import (
    ConversationClosedError,
    ConversationNotFoundError,
    add_message,
    create_conversation,
    ensure_active,
    get_conversation,
    get_recent_history,
    to_chat_messages,
)

router = APIRouter()

AI_UNAVAILABLE_MESSAGE = "Brilyx AI is temporarily unavailable. Please try again shortly."
CONVERSATION_NOT_FOUND_MESSAGE = "Conversation not found."
CONVERSATION_CLOSED_MESSAGE = "This conversation is closed. Please start a new conversation."
SERVER_ERROR_MESSAGE = "Something went wrong. Please try again shortly."
RATE_LIMITED_MESSAGE = "Too many requests. Please slow down and try again shortly."

# Module-level singleton, mirroring get_orchestrator's @lru_cache pattern —
# one limiter instance for the process lifetime. See
# backend/app/rate_limit.py for why this is single-process-only, and
# tests/conftest.py for how tests reset it between runs.
CHAT_RATE_LIMITER = InMemoryRateLimiter(
    max_requests=settings.CHAT_RATE_LIMIT_MAX_REQUESTS,
    window_seconds=settings.CHAT_RATE_LIMIT_WINDOW_SECONDS,
)


def _client_key(request: Request) -> str:
    """The ASGI-observed TCP source address — never a client-supplied header."""
    return request.client.host if request.client else "unknown"


def enforce_chat_rate_limit(request: Request) -> None:
    if not CHAT_RATE_LIMITER.allow(_client_key(request)):
        raise HTTPException(status_code=429, detail=RATE_LIMITED_MESSAGE)


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., min_length=1, max_length=settings.CHAT_MAX_MESSAGE_LENGTH)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class ChatResponse(BaseModel):
    conversation_id: str
    session_id: str
    response: str
    provider: str
    model: str


@router.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(enforce_chat_rate_limit)])
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    orchestrator: AIOrchestrator = Depends(get_orchestrator),
    notification_service: NotificationService = Depends(get_notification_service),
) -> ChatResponse:
    # Resolve the conversation. Missing conversation_id auto-creates a new
    # conversation (see docs/conversations.md for the rationale) so the
    # endpoint always returns a usable conversation_id, whether the caller
    # started one explicitly via POST /api/conversations or not.
    #
    # Deliberately not access-token-gated (see docs/security.md "Conversation
    # Access Control" for the full reasoning): this preserves the Phase 3
    # contract every existing client already relies on — continuing a chat
    # only requires knowing conversation_id. The `session_id` returned below
    # is what gates the separate read/inspection/close endpoints.
    try:
        if request.conversation_id is None:
            conversation = create_conversation(db)
        else:
            conversation = get_conversation(db, request.conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail=SERVER_ERROR_MESSAGE)

    try:
        ensure_active(conversation)
    except ConversationClosedError:
        raise HTTPException(status_code=409, detail=CONVERSATION_CLOSED_MESSAGE)

    # Fetch history *before* persisting the current message, so the
    # orchestrator (which appends `message` as the final turn itself) never
    # sees the current message twice.
    try:
        history = to_chat_messages(get_recent_history(db, conversation.id, settings.CHAT_HISTORY_MAX_MESSAGES))
        add_message(db, conversation, role="user", content=request.message)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail=SERVER_ERROR_MESSAGE)

    # Structured conversation intelligence: extract signals from this
    # message, merge them into the persisted ConversationState, and turn
    # the result into a short context block for the response prompt. This
    # never raises — extraction failures leave the state as-is (see
    # backend/app/intelligence/service.py).
    try:
        state = run_intelligence(db, conversation, request.message, orchestrator)
        context = build_context_section(build_context_lines(state))
        # Deterministic lead-qualification scoring (Phase 5): recomputed and
        # cached from the state just persisted above. Internal business
        # intelligence only — never included in the response returned below.
        qualification = run_qualification(db, conversation, state)
        # Natural lead capture (Phase 6): creates/updates this conversation's
        # single Lead row only when real intent exists. Contact extraction
        # reads only `request.message` (the visitor's own text) — never the
        # assistant's reply generated below.
        lead = run_lead_capture(db, conversation, state, qualification, request.message)
        # Business events + owner notifications (Phase 8): deterministic,
        # backend-decided — the AI never triggers a notification directly.
        # A notification failure here is recorded on the event row and
        # never propagates; only a genuine database failure would.
        evaluate_and_persist_events(db, conversation, state, qualification, lead, notification_service)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail=SERVER_ERROR_MESSAGE)

    try:
        result = orchestrator.chat(message=request.message, history=history, context=context)
    except (AIProviderError, KnowledgeLoadError):
        raise HTTPException(status_code=503, detail=AI_UNAVAILABLE_MESSAGE)
    except Exception:
        raise HTTPException(status_code=503, detail=AI_UNAVAILABLE_MESSAGE)

    try:
        add_message(db, conversation, role="assistant", content=result.text)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail=SERVER_ERROR_MESSAGE)

    return ChatResponse(
        conversation_id=conversation.id,
        session_id=conversation.session_id,
        response=result.text,
        provider=result.provider,
        model=result.model,
    )
