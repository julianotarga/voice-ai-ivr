"""
Configurações do módulo Realtime.

Referências:
- .context/docs/architecture.md: Network Ports
- .context/agents/devops-specialist.md: Variáveis de ambiente
"""

from pydantic_settings import BaseSettings
from typing import Optional


class RealtimeSettings(BaseSettings):
    """
    Configurações do servidor realtime.
    
    Carregadas de variáveis de ambiente.
    """
    
    # Server
    REALTIME_HOST: str = "0.0.0.0"
    REALTIME_PORT: int = 8080
    
    # Limits (Rate limiting conforme security.md)
    MAX_SESSIONS_PER_DOMAIN: int = 10
    MAX_TOTAL_SESSIONS: int = 100
    
    # Timeouts
    SESSION_IDLE_TIMEOUT_SECONDS: int = 30
    SESSION_MAX_DURATION_SECONDS: int = 600
    PROVIDER_CONNECT_TIMEOUT_SECONDS: int = 10
    
    # Audio
    FREESWITCH_SAMPLE_RATE: int = 16000
    CHUNK_SIZE_MS: int = 20  # 20ms chunks
    
    # Providers
    DEFAULT_REALTIME_PROVIDER: str = "openai"
    
    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_REALTIME_MODEL: str = "gpt-4o-realtime-preview"
    
    # ElevenLabs
    ELEVENLABS_API_KEY: Optional[str] = None
    ELEVENLABS_AGENT_ID: Optional[str] = None
    
    # Gemini
    GOOGLE_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-2.0-flash-exp"
    
    # VAD defaults
    VAD_THRESHOLD: float = 0.5
    SILENCE_DURATION_MS: int = 500
    PREFIX_PADDING_MS: int = 300
    
    # Metrics
    ENABLE_PROMETHEUS: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# Singleton
_settings: Optional[RealtimeSettings] = None


def get_realtime_settings() -> RealtimeSettings:
    global _settings
    if _settings is None:
        _settings = RealtimeSettings()
    return _settings
