"""
Configuration Loader - Carrega e cacheia configurações de secretárias.

Referências:
- openspec/changes/voice-ai-realtime/tasks.md (4.3)
- .context/docs/architecture.md: Multi-tenant

Features:
- Cache em memória com TTL
- Reload sem restart
- Validação de configuração
- Multi-tenant isolation
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class SecretaryConfig(BaseModel):
    """Configuração de uma secretária virtual."""
    
    secretary_uuid: str
    domain_uuid: str
    name: str
    extension: str
    
    # Mode
    processing_mode: str = "turn_based"  # turn_based, realtime, auto
    
    # Prompts
    system_prompt: str = ""
    greeting_message: str = "Olá! Como posso ajudar?"
    farewell_message: str = "Foi um prazer ajudar!"
    
    # Provider config
    realtime_provider: Optional[str] = None
    realtime_provider_config: Dict[str, Any] = Field(default_factory=dict)
    
    # Turn-based providers (fallback)
    stt_provider: Optional[str] = None
    tts_provider: Optional[str] = None
    llm_provider: Optional[str] = None
    
    # Voice
    voice: str = "alloy"
    language: str = "pt-BR"
    
    # Limits
    max_turns: int = 20
    session_timeout: int = 300  # segundos
    
    # Transfer
    default_transfer_extension: str = "200"
    
    # Flags
    is_enabled: bool = True
    
    @field_validator('processing_mode')
    @classmethod
    def validate_mode(cls, v):
        if v not in ('turn_based', 'realtime', 'auto'):
            raise ValueError(f"Invalid processing_mode: {v}")
        return v
    
    model_config = {"extra": "ignore"}


class ProviderCredentials(BaseModel):
    """Credenciais de um provider."""
    
    provider_uuid: str
    domain_uuid: str
    provider_type: str  # stt, tts, llm, realtime
    provider_name: str  # openai, elevenlabs, gemini, custom
    config: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    is_enabled: bool = True
    
    model_config = {"extra": "ignore"}


@dataclass
class CacheEntry:
    """Entrada de cache com TTL."""
    data: Any
    created_at: datetime = field(default_factory=datetime.now)
    ttl_seconds: int = 300  # 5 minutos
    
    @property
    def is_expired(self) -> bool:
        return datetime.now() > self.created_at + timedelta(seconds=self.ttl_seconds)


class ConfigLoader:
    """
    Carregador de configurações com cache.
    
    Multi-tenant: Isola configurações por domain_uuid.
    """
    
    def __init__(
        self,
        db_pool,
        cache_ttl: int = 300,
        max_cache_size: int = 1000
    ):
        """
        Args:
            db_pool: Pool de conexões asyncpg
            cache_ttl: TTL do cache em segundos
            max_cache_size: Tamanho máximo do cache
        """
        self.db_pool = db_pool
        self.cache_ttl = cache_ttl
        self.max_cache_size = max_cache_size
        
        # Caches
        self._secretary_cache: Dict[str, CacheEntry] = {}
        self._provider_cache: Dict[str, CacheEntry] = {}
        
        # Lock para operações de cache
        self._lock = asyncio.Lock()
    
    def _cache_key(self, *parts: str) -> str:
        """Gera chave de cache."""
        return ":".join(parts)
    
    async def get_secretary_config(
        self,
        domain_uuid: str,
        extension: str
    ) -> Optional[SecretaryConfig]:
        """
        Obtém configuração de secretária por extensão.
        
        Args:
            domain_uuid: UUID do tenant
            extension: Número da extensão
        
        Returns:
            SecretaryConfig ou None
        """
        cache_key = self._cache_key("secretary", domain_uuid, extension)
        
        # Verificar cache
        async with self._lock:
            if cache_key in self._secretary_cache:
                entry = self._secretary_cache[cache_key]
                if not entry.is_expired:
                    logger.debug(f"Cache hit: {cache_key}")
                    return entry.data
                else:
                    del self._secretary_cache[cache_key]
        
        # Buscar do banco
        config = await self._load_secretary_from_db(domain_uuid, extension)
        
        if config:
            async with self._lock:
                self._secretary_cache[cache_key] = CacheEntry(
                    data=config,
                    ttl_seconds=self.cache_ttl
                )
                self._cleanup_cache(self._secretary_cache)
        
        return config
    
    async def get_secretary_by_uuid(
        self,
        domain_uuid: str,
        secretary_uuid: str
    ) -> Optional[SecretaryConfig]:
        """
        Obtém configuração de secretária por UUID.
        """
        cache_key = self._cache_key("secretary_uuid", domain_uuid, secretary_uuid)
        
        async with self._lock:
            if cache_key in self._secretary_cache:
                entry = self._secretary_cache[cache_key]
                if not entry.is_expired:
                    return entry.data
                else:
                    del self._secretary_cache[cache_key]
        
        config = await self._load_secretary_by_uuid_from_db(domain_uuid, secretary_uuid)
        
        if config:
            async with self._lock:
                self._secretary_cache[cache_key] = CacheEntry(
                    data=config,
                    ttl_seconds=self.cache_ttl
                )
        
        return config
    
    async def get_provider_credentials(
        self,
        domain_uuid: str,
        provider_type: str,
        provider_name: Optional[str] = None
    ) -> Optional[ProviderCredentials]:
        """
        Obtém credenciais de um provider.
        
        Args:
            domain_uuid: UUID do tenant
            provider_type: Tipo (stt, tts, llm, realtime)
            provider_name: Nome específico (opcional, usa default)
        """
        cache_key = self._cache_key("provider", domain_uuid, provider_type, provider_name or "default")
        
        async with self._lock:
            if cache_key in self._provider_cache:
                entry = self._provider_cache[cache_key]
                if not entry.is_expired:
                    return entry.data
                else:
                    del self._provider_cache[cache_key]
        
        creds = await self._load_provider_from_db(domain_uuid, provider_type, provider_name)
        
        if creds:
            async with self._lock:
                self._provider_cache[cache_key] = CacheEntry(
                    data=creds,
                    ttl_seconds=self.cache_ttl
                )
        
        return creds
    
    async def _load_secretary_from_db(
        self,
        domain_uuid: str,
        extension: str
    ) -> Optional[SecretaryConfig]:
        """Carrega secretária do banco."""
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT 
                        s.voice_secretary_uuid,
                        s.domain_uuid,
                        s.secretary_name,
                        s.extension,
                        s.processing_mode,
                        s.personality_prompt,
                        s.greeting_message,
                        s.farewell_message,
                        s.tts_voice_id,
                        s.language,
                        s.max_turns,
                        s.is_enabled,
                        p.provider_name as realtime_provider_name,
                        p.config as realtime_provider_config
                    FROM v_voice_secretaries s
                    LEFT JOIN v_voice_ai_providers p ON p.voice_ai_provider_uuid = s.realtime_provider_uuid
                    WHERE s.domain_uuid = $1 
                      AND s.extension = $2
                      AND s.is_enabled = true
                """, domain_uuid, extension)
                
                if row:
                    # Config pode vir como string JSON
                    provider_config = row['realtime_provider_config']
                    if isinstance(provider_config, str):
                        import json
                        provider_config = json.loads(provider_config)
                    
                    return SecretaryConfig(
                        secretary_uuid=str(row['voice_secretary_uuid']),
                        domain_uuid=str(row['domain_uuid']),
                        name=row['secretary_name'] or 'Secretária',
                        extension=row['extension'] or '',
                        processing_mode=row['processing_mode'] or 'turn_based',
                        system_prompt=row['personality_prompt'] or '',
                        greeting_message=row['greeting_message'] or 'Olá!',
                        farewell_message=row['farewell_message'] or 'Até logo!',
                        realtime_provider=row['realtime_provider_name'],
                        realtime_provider_config=provider_config or {},
                        voice=row['tts_voice_id'] or 'alloy',
                        language=row['language'] or 'pt-BR',
                        max_turns=row['max_turns'] or 20,
                        is_enabled=row['is_enabled'],
                    )
                    
        except Exception as e:
            logger.error(f"Error loading secretary config: {e}")
        
        return None
    
    async def _load_secretary_by_uuid_from_db(
        self,
        domain_uuid: str,
        secretary_uuid: str
    ) -> Optional[SecretaryConfig]:
        """Carrega secretária por UUID."""
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT 
                        s.voice_secretary_uuid,
                        s.domain_uuid,
                        s.secretary_name,
                        s.extension,
                        s.processing_mode,
                        s.personality_prompt,
                        s.greeting_message,
                        s.farewell_message,
                        s.tts_voice_id,
                        s.language,
                        s.max_turns,
                        s.is_enabled,
                        p.provider_name as realtime_provider_name,
                        p.config as realtime_provider_config
                    FROM v_voice_secretaries s
                    LEFT JOIN v_voice_ai_providers p ON p.voice_ai_provider_uuid = s.realtime_provider_uuid
                    WHERE s.domain_uuid = $1 
                      AND s.voice_secretary_uuid = $2
                """, domain_uuid, secretary_uuid)
                
                if row:
                    # Config pode vir como string JSON
                    provider_config = row['realtime_provider_config']
                    if isinstance(provider_config, str):
                        import json
                        provider_config = json.loads(provider_config)
                    
                    return SecretaryConfig(
                        secretary_uuid=str(row['voice_secretary_uuid']),
                        domain_uuid=str(row['domain_uuid']),
                        name=row['secretary_name'] or 'Secretária',
                        extension=row['extension'] or '',
                        processing_mode=row['processing_mode'] or 'turn_based',
                        system_prompt=row['personality_prompt'] or '',
                        greeting_message=row['greeting_message'] or 'Olá!',
                        farewell_message=row['farewell_message'] or 'Até logo!',
                        realtime_provider=row['realtime_provider_name'],
                        realtime_provider_config=provider_config or {},
                        voice=row['tts_voice_id'] or 'alloy',
                        language=row['language'] or 'pt-BR',
                        max_turns=row['max_turns'] or 20,
                        is_enabled=row['is_enabled'],
                    )
                    
        except Exception as e:
            logger.error(f"Error loading secretary config: {e}")
        
        return None
    
    async def _load_provider_from_db(
        self,
        domain_uuid: str,
        provider_type: str,
        provider_name: Optional[str] = None
    ) -> Optional[ProviderCredentials]:
        """Carrega credenciais de provider."""
        try:
            async with self.db_pool.acquire() as conn:
                if provider_name:
                    row = await conn.fetchrow("""
                        SELECT 
                            voice_ai_provider_uuid,
                            domain_uuid,
                            provider_type,
                            provider_name,
                            config,
                            is_default,
                            is_enabled
                        FROM v_voice_ai_providers
                        WHERE domain_uuid = $1 
                          AND provider_type = $2
                          AND provider_name = $3
                          AND is_enabled = true
                    """, domain_uuid, provider_type, provider_name)
                else:
                    # Buscar provider default
                    row = await conn.fetchrow("""
                        SELECT 
                            voice_ai_provider_uuid,
                            domain_uuid,
                            provider_type,
                            provider_name,
                            config,
                            is_default,
                            is_enabled
                        FROM v_voice_ai_providers
                        WHERE domain_uuid = $1 
                          AND provider_type = $2
                          AND is_default = true
                          AND is_enabled = true
                        ORDER BY is_default DESC
                        LIMIT 1
                    """, domain_uuid, provider_type)
                
                if row:
                    return ProviderCredentials(
                        provider_uuid=str(row['voice_ai_provider_uuid']),
                        domain_uuid=str(row['domain_uuid']),
                        provider_type=row['provider_type'],
                        provider_name=row['provider_name'],
                        config=row['config'] or {},
                        is_default=row['is_default'],
                        is_enabled=row['is_enabled'],
                    )
                    
        except Exception as e:
            logger.error(f"Error loading provider credentials: {e}")
        
        return None
    
    def _cleanup_cache(self, cache: Dict[str, CacheEntry]) -> None:
        """Remove entradas expiradas e limita tamanho."""
        # Remover expiradas
        expired = [k for k, v in cache.items() if v.is_expired]
        for key in expired:
            del cache[key]
        
        # Limitar tamanho (remover mais antigas)
        if len(cache) > self.max_cache_size:
            sorted_entries = sorted(
                cache.items(),
                key=lambda x: x[1].created_at
            )
            for key, _ in sorted_entries[:len(cache) - self.max_cache_size]:
                del cache[key]
    
    async def invalidate_cache(
        self,
        domain_uuid: Optional[str] = None,
        cache_type: Optional[str] = None
    ) -> int:
        """
        Invalida cache.
        
        Args:
            domain_uuid: Opcional, invalida só este tenant
            cache_type: Opcional, 'secretary' ou 'provider'
        
        Returns:
            Número de entradas removidas
        """
        count = 0
        
        async with self._lock:
            caches = []
            if cache_type in (None, 'secretary'):
                caches.append(self._secretary_cache)
            if cache_type in (None, 'provider'):
                caches.append(self._provider_cache)
            
            for cache in caches:
                if domain_uuid:
                    keys_to_remove = [
                        k for k in cache 
                        if domain_uuid in k
                    ]
                else:
                    keys_to_remove = list(cache.keys())
                
                for key in keys_to_remove:
                    del cache[key]
                    count += 1
        
        logger.info(f"Cache invalidated: {count} entries removed")
        return count
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas do cache."""
        return {
            "secretary_cache_size": len(self._secretary_cache),
            "provider_cache_size": len(self._provider_cache),
            "max_cache_size": self.max_cache_size,
            "cache_ttl_seconds": self.cache_ttl,
        }


# Singleton para uso global
_config_loader: Optional[ConfigLoader] = None


def get_config_loader() -> Optional[ConfigLoader]:
    """Obtém instância do config loader."""
    return _config_loader


def init_config_loader(db_pool, **kwargs) -> ConfigLoader:
    """Inicializa o config loader."""
    global _config_loader
    _config_loader = ConfigLoader(db_pool, **kwargs)
    return _config_loader
