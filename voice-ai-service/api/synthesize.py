"""
Synthesize API endpoint (Text-to-Speech).

⚠️ MULTI-TENANT: domain_uuid é OBRIGATÓRIO em todas as requisições.
"""

import logging

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, status, Query

from models.request import SynthesizeRequest
from models.response import SynthesizeResponse, VoiceOption
from services.provider_manager import provider_manager

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize_text(request: SynthesizeRequest) -> SynthesizeResponse:
    """
    Synthesize text to speech.
    
    Args:
        request: SynthesizeRequest with domain_uuid, text, voice_id
        
    Returns:
        SynthesizeResponse with path to audio file
        
    Raises:
        HTTPException: If synthesis fails
    """
    # MULTI-TENANT: Validar domain_uuid
    if not request.domain_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="domain_uuid is required for multi-tenant isolation",
        )
    
    try:
        # Get provider from ProviderManager (loads from DB with fallback)
        provider = await provider_manager.get_tts_provider(
            domain_uuid=request.domain_uuid,
            provider_name=request.provider,
        )
        
        # Synthesize
        result = await provider.synthesize(
            text=request.text,
            voice_id=request.voice_id,
            speed=request.speed,
        )
        
        logger.info(
            f"Synthesized {len(request.text)} chars for domain {request.domain_uuid} "
            f"using {provider.provider_name}"
        )
        
        return SynthesizeResponse(
            audio_file=result.audio_file,
            duration_ms=result.duration_ms,
            format=result.format,
            provider=provider.provider_name,
        )
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"Synthesis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Synthesis failed: {str(e)}",
        )


@router.get("/tts/voices", response_model=List[VoiceOption])
async def list_tts_voices(
    domain_uuid: UUID = Query(..., description="Domain UUID (multi-tenant)"),
    provider: Optional[str] = Query(
        default=None,
        description="Optional TTS provider name (e.g., elevenlabs, openai_tts). If omitted, uses default.",
    ),
    language: str = Query(default="pt-BR", description="Language code (e.g., pt-BR)"),
) -> List[VoiceOption]:
    """
    List available TTS voices for a domain and provider.

    ⚠️ MULTI-TENANT: domain_uuid é OBRIGATÓRIO.
    """
    if not domain_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="domain_uuid is required for multi-tenant isolation",
        )

    try:
        tts = await provider_manager.get_tts_provider(
            domain_uuid=domain_uuid,
            provider_name=provider,
        )
        voices = await tts.list_voices(language=language)
        return [
            VoiceOption(
                voice_id=v.voice_id,
                name=v.name,
                language=v.language,
                gender=v.gender,
            )
            for v in voices
        ]
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"List voices failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"List voices failed: {str(e)}",
        )
