"""
Factory para providers realtime.

Referências:
- .context/docs/architecture.md: Key Pattern #1 (Factory Pattern)
- .context/agents/backend-specialist.md: Factory Pattern (Providers)
- openspec/changes/voice-ai-realtime/design.md: Decision 4
"""

import logging
from typing import Any, Dict, Type

from .base import BaseRealtimeProvider, RealtimeConfig
from .openai_realtime import OpenAIRealtimeProvider

logger = logging.getLogger(__name__)


class RealtimeProviderFactory:
    """
    Factory para criar providers realtime.
    
    Segue Factory Pattern conforme .context/agents/backend-specialist.md
    """
    
    _providers: Dict[str, Type[BaseRealtimeProvider]] = {
        "openai": OpenAIRealtimeProvider,
        "openai_realtime": OpenAIRealtimeProvider,
        # Futuros:
        # "elevenlabs": ElevenLabsConversationalProvider,
        # "gemini": GeminiLiveProvider,
        # "custom": CustomPipelineProvider,
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
        credentials: Dict[str, Any],
        config: RealtimeConfig,
    ) -> BaseRealtimeProvider:
        """
        Cria instância do provider.
        
        Args:
            provider_name: Nome do provider
            credentials: API keys e auth
            config: Configuração da sessão
        """
        if provider_name not in cls._providers:
            available = ", ".join(cls._providers.keys())
            raise ValueError(f"Unknown provider: {provider_name}. Available: {available}")
        
        provider_class = cls._providers[provider_name]
        
        logger.info("Creating realtime provider", extra={
            "provider": provider_name,
            "domain_uuid": config.domain_uuid,
        })
        
        return provider_class(credentials=credentials, config=config)
