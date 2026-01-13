"""
Response models for Voice AI Service.
"""

from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field


class TranscribeResponse(BaseModel):
    """Response from transcription (STT)."""
    
    text: str = Field(
        ...,
        description="Transcribed text",
    )
    language: str = Field(
        ...,
        description="Detected language",
    )
    confidence: Optional[float] = Field(
        default=None,
        description="Confidence score (0-1)",
    )
    duration_ms: int = Field(
        ...,
        description="Audio duration in milliseconds",
    )
    provider: str = Field(
        ...,
        description="Provider used for transcription",
    )


class SynthesizeResponse(BaseModel):
    """Response from synthesis (TTS)."""
    
    audio_file: str = Field(
        ...,
        description="Path to generated audio file",
    )
    duration_ms: int = Field(
        ...,
        description="Audio duration in milliseconds",
    )
    format: str = Field(
        ...,
        description="Audio format (wav, mp3)",
    )
    provider: str = Field(
        ...,
        description="Provider used for synthesis",
    )


class ChatResponse(BaseModel):
    """Response from chat with AI secretary."""
    
    text: str = Field(
        ...,
        description="AI response text",
    )
    action: str = Field(
        default="continue",
        pattern="^(continue|transfer|hangup)$",
        description="Action to take: continue, transfer, hangup",
    )
    transfer_extension: Optional[str] = Field(
        default=None,
        description="Extension to transfer to (if action=transfer)",
    )
    transfer_department: Optional[str] = Field(
        default=None,
        description="Department name for transfer",
    )
    intent: Optional[str] = Field(
        default=None,
        description="Detected intent from user message",
    )
    rag_sources: Optional[List[str]] = Field(
        default=None,
        description="Document sources used for RAG",
    )
    provider: str = Field(
        ...,
        description="LLM provider used",
    )
    tokens_used: Optional[int] = Field(
        default=None,
        description="Number of tokens used",
    )


class DocumentUploadResponse(BaseModel):
    """Response from document upload."""
    
    document_id: UUID = Field(
        ...,
        description="Created document UUID",
    )
    document_name: str = Field(
        ...,
        description="Document name",
    )
    chunk_count: int = Field(
        ...,
        description="Number of chunks created",
    )
    status: str = Field(
        default="processed",
        description="Processing status",
    )


class ProviderInfo(BaseModel):
    """Information about a configured provider."""
    
    provider_type: str
    provider_name: str
    is_default: bool
    is_enabled: bool
    config_keys: List[str] = Field(
        description="Available config keys (without values for security)",
    )


class HealthResponse(BaseModel):
    """Health check response."""
    
    status: str = "healthy"
    service: str = "voice-ai-service"
    version: str = "1.0.0"
    providers: Optional[dict] = None


class ErrorResponse(BaseModel):
    """Error response."""
    
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


class VoiceOption(BaseModel):
    """Voice option for TTS providers."""

    voice_id: str = Field(..., description="Provider-specific voice identifier")
    name: str = Field(..., description="Human-readable voice name")
    language: str = Field(..., description="Language code (e.g., pt-BR)")
    gender: Optional[str] = Field(default=None, description="Gender if provided by provider")
