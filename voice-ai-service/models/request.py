"""
Request models for Voice AI Service.

⚠️ MULTI-TENANT: Todos os requests MUST incluir domain_uuid como campo obrigatório.
"""

from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class BaseRequest(BaseModel):
    """Base request with required domain_uuid for multi-tenant."""
    
    domain_uuid: UUID = Field(
        ...,
        description="Domain UUID for multi-tenant isolation. OBRIGATÓRIO.",
    )
    
    @field_validator('domain_uuid', mode='before')
    @classmethod
    def validate_domain_uuid(cls, v):
        """Validate domain_uuid is a valid UUID."""
        if v is None:
            raise ValueError('domain_uuid is required for multi-tenant isolation')
        if isinstance(v, str):
            try:
                return UUID(v)
            except ValueError as e:
                raise ValueError(f'Invalid UUID format: {e}') from e
        return v
    
    model_config = {
        'extra': 'forbid',  # Reject unknown fields
        'str_strip_whitespace': True,  # Strip whitespace from strings
    }


class TranscribeRequest(BaseRequest):
    """Request to transcribe audio to text (STT)."""
    
    audio_file: Optional[str] = Field(
        default=None,
        description="Path to the audio file to transcribe",
    )
    audio_base64: Optional[str] = Field(
        default=None,
        description="Audio content as base64 string (alternative to audio_file)",
    )
    format: str = Field(
        default="wav",
        description="Audio format when using audio_base64 (wav, mp3, webm)",
    )
    language: str = Field(
        default="pt",
        description="Language code (pt, en, es, etc.)",
    )
    provider: Optional[str] = Field(
        default=None,
        description="STT provider to use (whisper_local, whisper_api, azure_speech, etc.)",
    )
    
    @field_validator('audio_file', 'audio_base64', mode='before')
    @classmethod
    def validate_audio_source(cls, v, info):
        """Validate that at least one audio source is provided."""
        return v


class SynthesizeRequest(BaseRequest):
    """Request to synthesize text to speech (TTS)."""
    
    text: str = Field(
        ...,
        description="Text to synthesize",
        max_length=5000,
    )
    voice_id: Optional[str] = Field(
        default=None,
        description="Voice ID for the TTS provider",
    )
    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="Speech speed (0.5 to 2.0)",
    )
    provider: Optional[str] = Field(
        default=None,
        description="TTS provider to use (piper_local, openai_tts, elevenlabs, etc.)",
    )
    output_format: str = Field(
        default="wav",
        description="Output audio format (wav, mp3)",
    )


class ChatMessage(BaseModel):
    """Single chat message."""
    
    role: str = Field(
        ...,
        pattern="^(user|assistant|system)$",
        description="Message role: user, assistant, or system",
    )
    content: str = Field(
        ...,
        description="Message content",
    )


class ChatRequest(BaseRequest):
    """Request to chat with the AI secretary."""
    
    session_id: str = Field(
        ...,
        description="Unique session ID for the conversation",
    )
    secretary_id: UUID = Field(
        ...,
        description="Voice secretary UUID",
    )
    user_message: str = Field(
        ...,
        description="User's message (transcribed from audio)",
    )
    conversation_history: Optional[List[ChatMessage]] = Field(
        default=None,
        description="Previous messages in the conversation",
    )
    use_rag: bool = Field(
        default=True,
        description="Whether to use RAG (knowledge base) for context",
    )
    provider: Optional[str] = Field(
        default=None,
        description="LLM provider to use (openai, anthropic, azure_openai, etc.)",
    )


class DocumentUploadRequest(BaseRequest):
    """Request to upload a document to the knowledge base."""
    
    secretary_id: Optional[UUID] = Field(
        default=None,
        description="Secretary UUID (null = available for all secretaries in domain)",
    )
    document_name: str = Field(
        ...,
        description="Document name",
    )
    document_type: str = Field(
        ...,
        pattern="^(pdf|txt|docx|faq)$",
        description="Document type: pdf, txt, docx, faq",
    )
    content: Optional[str] = Field(
        default=None,
        description="Text content (for txt/faq types)",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="File path (for pdf/docx types)",
    )


class ProviderConfigRequest(BaseRequest):
    """Request to get provider configuration."""
    
    provider_type: str = Field(
        ...,
        pattern="^(stt|tts|llm|embeddings)$",
        description="Provider type: stt, tts, llm, embeddings",
    )
    provider_name: Optional[str] = Field(
        default=None,
        description="Specific provider name (optional, returns default if not specified)",
    )
