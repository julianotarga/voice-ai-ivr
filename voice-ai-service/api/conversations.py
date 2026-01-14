"""
Conversations API - Save and manage conversation history.

MULTI-TENANT: All operations require domain_uuid.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from services.database import db
from models.request import BaseRequest


async def get_db_pool():
    """Get database pool (compatibility wrapper)."""
    try:
        return await db.get_pool()
    except Exception:
        return None


router = APIRouter(prefix="/conversations", tags=["conversations"])


class MessageInput(BaseModel):
    """Single message in a conversation."""
    role: str = Field(..., description="Message role: user, assistant, system")
    content: str = Field(..., description="Message content")
    audio_file: Optional[str] = None
    audio_duration_ms: Optional[int] = None
    stt_provider: Optional[str] = None
    tts_provider: Optional[str] = None
    detected_intent: Optional[str] = None
    intent_confidence: Optional[float] = None


class SaveConversationRequest(BaseRequest):
    """Request to save a conversation."""
    session_id: str = Field(..., description="Call/session UUID")
    caller_id: str = Field(..., description="Caller phone number")
    secretary_uuid: Optional[str] = None
    messages: List[MessageInput] = Field(default_factory=list)
    final_action: str = Field(..., description="hangup, transfer, max_turns")
    transfer_target: Optional[str] = None
    duration_seconds: Optional[int] = None


class ConversationResponse(BaseModel):
    """Response after saving conversation."""
    conversation_uuid: str
    message_count: int


@router.post("", response_model=ConversationResponse)
async def save_conversation(request: SaveConversationRequest):
    """
    Save a completed conversation to the database.
    
    Called by the Lua script when a call ends.
    """
    if not request.domain_uuid:
        raise HTTPException(status_code=400, detail="domain_uuid is required")
    
    pool = await get_db_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    conversation_uuid = str(uuid.uuid4())
    
    async with pool.acquire() as conn:
        # Insert conversation
        # Usando nomes de colunas corretos conforme migration 004_create_conversations.sql
        await conn.execute(
            """
            INSERT INTO v_voice_conversations (
                voice_conversation_uuid,
                domain_uuid,
                voice_secretary_uuid,
                call_uuid,
                caller_id_number,
                final_action,
                transfer_extension,
                duration_seconds,
                start_time,
                insert_date
            ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, NOW(), NOW())
            """,
            conversation_uuid,
            request.domain_uuid,
            request.secretary_uuid,
            request.session_id,
            request.caller_id,
            request.final_action,
            request.transfer_target,
            request.duration_seconds,
        )
        
        # Insert messages
        # Usando nomes de colunas corretos conforme migration 004_create_conversations.sql
        for i, msg in enumerate(request.messages, start=1):
            message_uuid = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO v_voice_messages (
                    voice_message_uuid,
                    voice_conversation_uuid,
                    turn_number,
                    role,
                    content,
                    audio_file_path,
                    audio_duration_ms,
                    provider_used,
                    detected_intent,
                    insert_date
                ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, NOW())
                """,
                message_uuid,
                conversation_uuid,
                i,
                msg.role,
                msg.content,
                msg.audio_file,
                msg.audio_duration_ms,
                msg.stt_provider or msg.tts_provider,  # Use whichever is set
                msg.detected_intent,
            )
    
    return ConversationResponse(
        conversation_uuid=conversation_uuid,
        message_count=len(request.messages),
    )


class ConversationListItem(BaseModel):
    """Conversation summary for list view."""
    conversation_uuid: str
    caller_id: str
    final_action: str
    transfer_target: Optional[str]
    duration_seconds: Optional[int]
    message_count: int
    created_at: datetime


@router.get("")
async def list_conversations(
    domain_uuid: str,
    limit: int = 50,
    offset: int = 0,
    secretary_uuid: Optional[str] = None,
    action: Optional[str] = None,
):
    """
    List conversations for a domain.
    """
    if not domain_uuid:
        raise HTTPException(status_code=400, detail="domain_uuid is required")
    
    pool = await get_db_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    query = """
        SELECT c.voice_conversation_uuid as conversation_uuid, c.caller_id_number as caller_id, 
               c.final_action, c.transfer_extension as transfer_target,
               c.duration_seconds, c.insert_date as created_at,
               (SELECT COUNT(*) FROM v_voice_messages m WHERE m.voice_conversation_uuid = c.voice_conversation_uuid) as message_count
        FROM v_voice_conversations c
        WHERE c.domain_uuid = $1
    """
    params = [domain_uuid]
    param_idx = 2
    
    if secretary_uuid:
        query += f" AND c.voice_secretary_uuid = ${param_idx}"
        params.append(secretary_uuid)
        param_idx += 1
    
    if action:
        query += f" AND c.final_action = ${param_idx}"
        params.append(action)
        param_idx += 1
    
    query += f" ORDER BY c.created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
    params.extend([limit, offset])
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    
    return [
        ConversationListItem(
            conversation_uuid=str(row["conversation_uuid"]),
            caller_id=row["caller_id"],
            final_action=row["final_action"],
            transfer_target=row["transfer_target"],
            duration_seconds=row["duration_seconds"],
            message_count=row["message_count"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.get("/{conversation_uuid}")
async def get_conversation(conversation_uuid: str, domain_uuid: str):
    """
    Get full conversation with messages.
    """
    if not domain_uuid:
        raise HTTPException(status_code=400, detail="domain_uuid is required")
    
    pool = await get_db_pool()
    if not pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    async with pool.acquire() as conn:
        # Get conversation
        conv = await conn.fetchrow(
            """
            SELECT * FROM v_voice_conversations 
            WHERE voice_conversation_uuid = $1::uuid AND domain_uuid = $2::uuid
            """,
            conversation_uuid,
            domain_uuid,
        )
        
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Get messages
        messages = await conn.fetch(
            """
            SELECT * FROM v_voice_messages 
            WHERE voice_conversation_uuid = $1::uuid
            ORDER BY turn_number ASC
            """,
            conversation_uuid,
        )
    
    return {
        "conversation": dict(conv),
        "messages": [dict(m) for m in messages],
    }
