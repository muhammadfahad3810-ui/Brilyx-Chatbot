from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.intelligence.service import get_state_or_default
from backend.app.models import Conversation
from backend.app.qualification.service import get_qualification_or_default
from backend.app.rate_limit import InMemoryRateLimiter
from backend.app.services.conversation_service import (
    ConversationAccessDeniedError,
    ConversationNotFoundError,
    close_conversation,
    create_conversation,
    get_conversation,
    verify_conversation_access,
)

router = APIRouter()

CONVERSATION_NOT_FOUND_MESSAGE = "Conversation not found."
LEAD_NOT_FOUND_MESSAGE = "No lead has been captured for this conversation yet."
SERVER_ERROR_MESSAGE = "Something went wrong. Please try again shortly."
RATE_LIMITED_MESSAGE = "Too many requests. Please slow down and try again shortly."

# See backend/app/routers/chat.py::CHAT_RATE_LIMITER for the same pattern
# and backend/app/rate_limit.py for the mechanism/limitations.
CONVERSATION_CREATE_RATE_LIMITER = InMemoryRateLimiter(
    max_requests=settings.CONVERSATION_CREATE_RATE_LIMIT_MAX_REQUESTS,
    window_seconds=settings.CONVERSATION_CREATE_RATE_LIMIT_WINDOW_SECONDS,
)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_conversation_create_rate_limit(request: Request) -> None:
    if not CONVERSATION_CREATE_RATE_LIMITER.allow(_client_key(request)):
        raise HTTPException(status_code=429, detail=RATE_LIMITED_MESSAGE)


def _get_owned_conversation(
    conversation_id: str,
    db: Session,
    x_conversation_token: str | None,
) -> Conversation:
    """Resolve a conversation and verify the caller's access token in one step.

    Both "conversation doesn't exist" and "wrong/missing token" raise
    HTTPException(404) with the identical message — see
    ConversationAccessDeniedError's docstring for why that's deliberate.
    """
    try:
        conversation = get_conversation(db, conversation_id)
        verify_conversation_access(conversation, x_conversation_token)
    except (ConversationNotFoundError, ConversationAccessDeniedError):
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail=SERVER_ERROR_MESSAGE)
    return conversation


class ConversationStatusResponse(BaseModel):
    conversation_id: str
    session_id: str
    status: Literal["active", "closed"]


class ConversationHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationDetailResponse(BaseModel):
    conversation_id: str
    session_id: str
    status: Literal["active", "closed"]
    messages: list[ConversationHistoryMessage]


class ConversationStateResponse(BaseModel):
    conversation_id: str
    intent: str
    business_type: str
    business_name: str | None
    country: str | None
    currency: str | None
    current_channel: str | None
    requested_service: str
    requested_package: str | None
    problem_summary: str | None
    requirements: list[str]
    demo_requested: bool
    human_handoff_requested: bool
    handoff_recommended: bool
    confidence: float
    updated_at: datetime | None


@router.post(
    "/api/conversations",
    response_model=ConversationStatusResponse,
    status_code=201,
    dependencies=[Depends(enforce_conversation_create_rate_limit)],
)
def create_conversation_endpoint(db: Session = Depends(get_db)) -> ConversationStatusResponse:
    try:
        conversation = create_conversation(db)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail=SERVER_ERROR_MESSAGE)

    return ConversationStatusResponse(
        conversation_id=conversation.id,
        session_id=conversation.session_id,
        status=conversation.status,
    )


@router.get("/api/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation_endpoint(
    conversation_id: str,
    db: Session = Depends(get_db),
    x_conversation_token: str | None = Header(default=None),
) -> ConversationDetailResponse:
    conversation = _get_owned_conversation(conversation_id, db, x_conversation_token)

    return ConversationDetailResponse(
        conversation_id=conversation.id,
        session_id=conversation.session_id,
        status=conversation.status,
        messages=[
            ConversationHistoryMessage(role=message.role, content=message.content, created_at=message.created_at)
            for message in conversation.messages
        ],
    )


@router.get("/api/conversations/{conversation_id}/state", response_model=ConversationStateResponse)
def get_conversation_state_endpoint(
    conversation_id: str,
    db: Session = Depends(get_db),
    x_conversation_token: str | None = Header(default=None),
) -> ConversationStateResponse:
    """Development-oriented inspection of the structured conversation state.

    Exposes only the understood business/intent fields — never the system
    prompt, raw model output, or any other internal implementation detail.
    """
    conversation = _get_owned_conversation(conversation_id, db, x_conversation_token)

    state = get_state_or_default(db, conversation)

    return ConversationStateResponse(
        conversation_id=conversation.id,
        intent=state.intent,
        business_type=state.business_type,
        business_name=state.business_name,
        country=state.country,
        currency=state.currency,
        current_channel=state.current_channel,
        requested_service=state.requested_service,
        requested_package=state.requested_package,
        problem_summary=state.problem_summary,
        requirements=state.requirements or [],
        demo_requested=state.demo_requested,
        human_handoff_requested=state.human_handoff_requested,
        handoff_recommended=state.handoff_recommended,
        confidence=state.confidence,
        updated_at=state.updated_at,
    )


class QualificationResponse(BaseModel):
    conversation_id: str
    score: int
    level: str
    reasons: list[str]


@router.get("/api/conversations/{conversation_id}/qualification", response_model=QualificationResponse)
def get_conversation_qualification_endpoint(
    conversation_id: str,
    db: Session = Depends(get_db),
    x_conversation_token: str | None = Header(default=None),
) -> QualificationResponse:
    """Internal/debug view of the deterministic lead-qualification score.

    Not part of the visitor-facing chat contract — see
    docs/lead-qualification.md. score/level/reasons are always recomputed
    deterministically from ConversationState and the conversation's own
    message history; never model-generated, never client-supplied.
    """
    conversation = _get_owned_conversation(conversation_id, db, x_conversation_token)

    qualification = get_qualification_or_default(db, conversation)

    return QualificationResponse(
        conversation_id=conversation.id,
        score=qualification.score,
        level=qualification.level.value,
        reasons=qualification.reasons,
    )


class LeadResponse(BaseModel):
    conversation_id: str
    name: str | None
    business_name: str | None
    business_type: str
    country: str | None
    preferred_currency: str | None
    email: str | None
    whatsapp: str | None
    website: str | None
    requested_service: str
    problem_summary: str | None
    requirements: list[str]
    estimated_package: str | None
    lead_score: int
    lead_level: str
    lead_status: str
    source: str
    notes: list[str]
    created_at: datetime
    updated_at: datetime


@router.get("/api/conversations/{conversation_id}/lead", response_model=LeadResponse)
def get_conversation_lead_endpoint(
    conversation_id: str,
    db: Session = Depends(get_db),
    x_conversation_token: str | None = Header(default=None),
) -> LeadResponse:
    """Internal/debug view of the captured Lead, if one exists.

    Not part of the visitor-facing chat contract — see
    docs/lead-capture.md. There is no lookup by email and no "list all
    leads" endpoint: this only ever returns the single lead (if any) tied
    to one already-known conversation_id.
    """
    conversation = _get_owned_conversation(conversation_id, db, x_conversation_token)

    lead = conversation.lead
    if lead is None:
        raise HTTPException(status_code=404, detail=LEAD_NOT_FOUND_MESSAGE)

    return LeadResponse(
        conversation_id=conversation.id,
        name=lead.name,
        business_name=lead.business_name,
        business_type=lead.business_type,
        country=lead.country,
        preferred_currency=lead.preferred_currency,
        email=lead.email,
        whatsapp=lead.whatsapp,
        website=lead.website,
        requested_service=lead.requested_service,
        problem_summary=lead.problem_summary,
        requirements=lead.requirements or [],
        estimated_package=lead.estimated_package,
        lead_score=lead.lead_score,
        lead_level=lead.lead_level,
        lead_status=lead.lead_status,
        source=lead.source,
        notes=lead.notes or [],
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


@router.post("/api/conversations/{conversation_id}/close", response_model=ConversationStatusResponse)
def close_conversation_endpoint(
    conversation_id: str,
    db: Session = Depends(get_db),
    x_conversation_token: str | None = Header(default=None),
) -> ConversationStatusResponse:
    conversation = _get_owned_conversation(conversation_id, db, x_conversation_token)
    try:
        conversation = close_conversation(db, conversation)
    except SQLAlchemyError:
        raise HTTPException(status_code=500, detail=SERVER_ERROR_MESSAGE)

    return ConversationStatusResponse(
        conversation_id=conversation.id,
        session_id=conversation.session_id,
        status=conversation.status,
    )
