"""
Factory para providers realtime.

Referências:
- .context/docs/architecture.md: Key Pattern #1 (Factory Pattern)
- .context/agents/backend-specialist.md: Factory Pattern (Providers)
- openspec/changes/voice-ai-realtime/design.md: Decision 4
"""

import json
import logging
from typing import Any, Dict, Type

from .base import BaseRealtimeProvider, RealtimeConfig
from .openai_realtime import OpenAIRealtimeProvider
from .elevenlabs_conv import ElevenLabsConversationalProvider
from .gemini_live import GeminiLiveProvider
from .custom_pipeline import CustomPipelineProvider

logger = logging.getLogger(__name__)


def _normalize_credentials(credentials: Any) -> Dict[str, Any]:
    """
    Normaliza credentials para dict.
    
    O config pode vir do banco como:
    - dict (JSONB parseado automaticamente)
    - str (JSON string)
    - None
    """
    if credentials is None:
        return {}
    
    if isinstance(credentials, dict):
        return credentials
    
    if isinstance(credentials, str):
        try:
            parsed = json.loads(credentials)
            if isinstance(parsed, dict):
                return parsed
            logger.warning(f"Credentials JSON parsed to non-dict: {type(parsed)}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse credentials JSON: {e}")
            return {}
    
    logger.warning(f"Unexpected credentials type: {type(credentials)}")
    return {}


class RealtimeProviderFactory:
    """
    Factory para criar providers realtime.
    
    Segue Factory Pattern conforme .context/agents/backend-specialist.md
    
    Providers disponíveis:
    - openai: OpenAI Realtime API (GPT-4o-realtime)
    - elevenlabs: ElevenLabs Conversational AI
    - gemini: Google Gemini 2.0 Flash Live
    - custom: Pipeline custom (Deepgram + Groq + Piper)
    """
    
    _providers: Dict[str, Type[BaseRealtimeProvider]] = {
        "openai": OpenAIRealtimeProvider,
        "openai_realtime": OpenAIRealtimeProvider,
        "elevenlabs": ElevenLabsConversationalProvider,
        "elevenlabs_conversational": ElevenLabsConversationalProvider,
        "gemini": GeminiLiveProvider,
        "gemini_live": GeminiLiveProvider,
        "custom": CustomPipelineProvider,
        "custom_pipeline": CustomPipelineProvider,
    }
    
    @classmethod
    def register_provider(cls, name: str, provider_class: Type[BaseRealtimeProvider]) -> None:
        """Registra novo provider."""
        cls._providers[name] = provider_class
        logger.info(f"Registered realtime provider: {name}")
    
    @classmethod
    def get_available_providers(cls) -> list[str]:
        """Lista providers disponíveis."""
        return list(cls._providers.keys())
    
    @classmethod
    def create(
        cls,
        provider_name: str,
        credentials: Any,
        config: RealtimeConfig,
    ) -> BaseRealtimeProvider:
        """
        Cria instância do provider.
        
        Args:
            provider_name: Nome do provider
            credentials: API keys e auth (dict, JSON string, ou None)
            config: Configuração da sessão
        """
        if provider_name not in cls._providers:
            available = ", ".join(cls._providers.keys())
            raise ValueError(f"Unknown provider: {provider_name}. Available: {available}")
        
        provider_class = cls._providers[provider_name]
        
        # Normalizar credentials para dict
        normalized_credentials = _normalize_credentials(credentials)
        
        logger.info("Creating realtime provider", extra={
            "provider": provider_name,
            "domain_uuid": config.domain_uuid,
            "credentials_keys": list(normalized_credentials.keys()) if normalized_credentials else [],
        })
        
        return provider_class(credentials=normalized_credentials, config=config)
