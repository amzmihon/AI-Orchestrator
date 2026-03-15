"""
Session & Memory API endpoints.

Provides:
  GET    /api/sessions               — list user's sessions
  POST   /api/sessions               — create new session
  GET    /api/sessions/{id}          — get session with messages
  PATCH  /api/sessions/{id}          — rename session
  DELETE /api/sessions/{id}          — delete session
  GET    /api/memory                 — list user's memories
  POST   /api/memory                 — manually add a memory
  DELETE /api/memory/{id}            — delete a memory
  DELETE /api/memory                 — clear all memories
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from auth.dependencies import get_current_user
from auth.token_verify import VerifiedUser
from chat_history import (
    create_session,
    list_sessions,
    get_session,
    get_messages,
    update_session_title,
    delete_session,
    list_memories,
    store_memory,
    delete_memory,
    clear_memories,
)

router = APIRouter()


# ── Request / Response Models ────────────────────────────

class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Chat", max_length=120)


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class SessionResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    sql: str | None = None
    data_summary: str | None = None
    intent: str | None = None
    created_at: str


class SessionDetailResponse(BaseModel):
    session: SessionResponse
    messages: list[MessageResponse]


class CreateMemoryRequest(BaseModel):
    content: str = Field(..., min_length=3, max_length=500)
    category: str = Field(default="fact", pattern="^(preference|fact|context|instruction)$")


class MemoryResponse(BaseModel):
    id: str
    category: str
    content: str
    source: str
    importance: float
    created_at: str
    last_accessed: str
    access_count: int


# ── Session Endpoints ────────────────────────────────────

@router.get("/sessions", response_model=list[SessionResponse])
async def api_list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: VerifiedUser = Depends(get_current_user),
):
    """List all chat sessions for the current user."""
    sessions = await list_sessions(user.user_id, limit=limit, offset=offset)
    return [
        SessionResponse(
            id=s.id, title=s.title, created_at=s.created_at,
            updated_at=s.updated_at, message_count=s.message_count,
        )
        for s in sessions
    ]


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def api_create_session(
    body: CreateSessionRequest = CreateSessionRequest(),
    user: VerifiedUser = Depends(get_current_user),
):
    """Create a new chat session."""
    s = await create_session(user.user_id, title=body.title)
    return SessionResponse(
        id=s.id, title=s.title, created_at=s.created_at,
        updated_at=s.updated_at, message_count=0,
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def api_get_session(
    session_id: str,
    user: VerifiedUser = Depends(get_current_user),
):
    """Get a session and all its messages."""
    s = await get_session(session_id, user.user_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = await get_messages(session_id)
    return SessionDetailResponse(
        session=SessionResponse(
            id=s.id, title=s.title, created_at=s.created_at,
            updated_at=s.updated_at, message_count=s.message_count,
        ),
        messages=[
            MessageResponse(
                id=m.id, role=m.role, content=m.content,
                sql=m.sql, data_summary=m.data_summary,
                intent=m.intent, created_at=m.created_at,
            )
            for m in msgs
        ],
    )


@router.patch("/sessions/{session_id}", response_model=dict)
async def api_rename_session(
    session_id: str,
    body: RenameSessionRequest,
    user: VerifiedUser = Depends(get_current_user),
):
    """Rename a chat session."""
    ok = await update_session_title(session_id, user.user_id, body.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "renamed", "title": body.title}


@router.delete("/sessions/{session_id}", response_model=dict)
async def api_delete_session(
    session_id: str,
    user: VerifiedUser = Depends(get_current_user),
):
    """Delete a chat session and all its messages."""
    ok = await delete_session(session_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


# ── Memory Endpoints ─────────────────────────────────────

@router.get("/memory", response_model=list[MemoryResponse])
async def api_list_memories(
    user: VerifiedUser = Depends(get_current_user),
):
    """List all long-term memories for the current user."""
    mems = await list_memories(user.user_id)
    return [
        MemoryResponse(
            id=m.id, category=m.category, content=m.content,
            source=m.source, importance=m.importance,
            created_at=m.created_at, last_accessed=m.last_accessed,
            access_count=m.access_count,
        )
        for m in mems
    ]


@router.post("/memory", response_model=MemoryResponse, status_code=201)
async def api_add_memory(
    body: CreateMemoryRequest,
    user: VerifiedUser = Depends(get_current_user),
):
    """Manually add a long-term memory."""
    m = await store_memory(
        user_id=user.user_id,
        content=body.content,
        category=body.category,
        source="manual",
        importance=0.8,   # Manual memories are considered important
    )
    return MemoryResponse(
        id=m.id, category=m.category, content=m.content,
        source=m.source, importance=m.importance,
        created_at=m.created_at, last_accessed=m.last_accessed,
        access_count=m.access_count,
    )


@router.delete("/memory/{memory_id}", response_model=dict)
async def api_delete_memory(
    memory_id: str,
    user: VerifiedUser = Depends(get_current_user),
):
    """Delete a specific memory."""
    ok = await delete_memory(memory_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted"}


@router.delete("/memory", response_model=dict)
async def api_clear_memories(
    user: VerifiedUser = Depends(get_current_user),
):
    """Clear ALL long-term memories for the current user."""
    count = await clear_memories(user.user_id)
    return {"status": "cleared", "count": count}
