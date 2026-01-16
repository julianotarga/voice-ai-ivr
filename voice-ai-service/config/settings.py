"""
Configuration settings for Voice AI Service.

Load from environment variables or .env file.
"""

import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""
    
    # ============================================
    # SERVER
    # ============================================
    HOST: str = "127.0.0.1"  # Localhost only for security
    # Default alinhado ao docker-compose (porta publicada 8100).
    # Pode ser sobrescrito por env PORT.
    PORT: int = 8100
    DEBUG: bool = False
    
    # ============================================
    # DATABASE (PostgreSQL - FusionPBX)
    # ============================================
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "fusionpbx"
    DB_USER: str = "fusionpbx"
    DB_PASS: str = ""
    
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
    
    # ============================================
    # PATHS
    # ============================================
    DATA_DIR: Path = Path(__file__).parent.parent / "data"
    WHISPER_MODEL_DIR: Path = DATA_DIR / "whisper"
    PIPER_VOICE_DIR: Path = DATA_DIR / "piper"
    EMBEDDINGS_DIR: Path = DATA_DIR / "embeddings"
    TEMP_DIR: Path = Path("/tmp/voice-ai")
    
    # ============================================
    # DEFAULT PROVIDERS
    # ============================================
    DEFAULT_STT_PROVIDER: str = "whisper_local"
    DEFAULT_TTS_PROVIDER: str = "piper_local"
    DEFAULT_LLM_PROVIDER: str = "openai"
    DEFAULT_EMBEDDINGS_PROVIDER: str = "openai"
    
    # ============================================
    # STT - WHISPER LOCAL
    # ============================================
    WHISPER_MODEL: str = "base"  # tiny, base, small, medium, large
    WHISPER_DEVICE: str = "cpu"  # cpu, cuda
    WHISPER_COMPUTE_TYPE: str = "int8"  # float16, int8
    
    # ============================================
    # TTS - PIPER LOCAL
    # ============================================
    PIPER_MODEL: str = "pt_BR-faber-medium"
    PIPER_SPEAKER: int = 0
    
    # ============================================
    # API KEYS (provider-specific, loaded from DB per tenant)
    # These are fallback/default values only
    # ============================================
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    AZURE_SPEECH_KEY: Optional[str] = None
    AZURE_SPEECH_REGION: str = "brazilsouth"
    AZURE_OPENAI_KEY: Optional[str] = None
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    ELEVENLABS_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    DEEPGRAM_API_KEY: Optional[str] = None
    COHERE_API_KEY: Optional[str] = None
    
    # ============================================
    # LLM SETTINGS
    # ============================================
    LLM_DEFAULT_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 500
    
    # ============================================
    # RAG SETTINGS
    # ============================================
    RAG_CHUNK_SIZE: int = 500
    RAG_CHUNK_OVERLAP: int = 50
    RAG_TOP_K: int = 5
    EMBEDDINGS_MODEL: str = "text-embedding-3-small"
    EMBEDDINGS_DIMENSIONS: int = 1536
    
    # ============================================
    # AUDIO SETTINGS
    # ============================================
    AUDIO_SAMPLE_RATE: int = 16000
    AUDIO_FORMAT: str = "wav"
    MAX_RECORDING_SECONDS: int = 30
    SILENCE_THRESHOLD_MS: int = 3000
    
    # ============================================
    # OLLAMA (LOCAL LLM)
    # ============================================
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"
    
    # ============================================
    # RATE LIMITING
    # ============================================
    RATE_LIMIT_RPM: int = 60  # Requests per minute
    
    # ============================================
    # ESL (FREESWITCH EVENT SOCKET)
    # ============================================
    ESL_HOST: str = "127.0.0.1"
    ESL_PORT: int = 8021
    ESL_PASSWORD: str = "ClueCon"
    ESL_CONNECT_TIMEOUT: float = 5.0
    ESL_READ_TIMEOUT: float = 30.0
    
    # ============================================
    # OMNIPLAY INTEGRATION
    # ============================================
    OMNIPLAY_API_URL: str = "http://host.docker.internal:8080"
    OMNIPLAY_API_TIMEOUT_MS: int = 10000
    VOICE_AI_SERVICE_TOKEN: Optional[str] = None
    
    # ============================================
    # TRANSFER SETTINGS
    # ============================================
    TRANSFER_DEFAULT_TIMEOUT: int = 30
    TRANSFER_ANNOUNCE_ENABLED: bool = True
    TRANSFER_MUSIC_ON_HOLD: str = "local_stream://moh"
    TRANSFER_CACHE_TTL_SECONDS: int = 300
    
    # ============================================
    # CALLBACK SETTINGS
    # ============================================
    CALLBACK_ENABLED: bool = True
    CALLBACK_EXPIRATION_HOURS: int = 24
    CALLBACK_MAX_NOTIFICATIONS: int = 5
    CALLBACK_MIN_INTERVAL_MINUTES: int = 10
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()

# Ensure directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
settings.PIPER_VOICE_DIR.mkdir(parents=True, exist_ok=True)
settings.EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
