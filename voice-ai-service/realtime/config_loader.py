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


class TransferRule(BaseModel):
    """
    Regra de transferência para handoff baseado em intenção.
    
    Ref: openspec/changes/add-realtime-handoff-omni/design.md (Decision 1)
    Tabela: v_voice_transfer_rules
    """
    
    transfer_rule_uuid: str
    voice_secretary_uuid: Optional[str] = None
    domain_uuid: str
    
    # Detecção de intenção
    department_name: str
    intent_keywords: List[str] = Field(default_factory=list)
    
    # Destino - pode ser ramal, ring-group ou fila
    transfer_extension: str
    transfer_message: Optional[str] = None
    
    # Prioridade (menor = maior prioridade)
    priority: int = 0
    
    is_enabled: bool = True
    
    model_config = {"extra": "ignore"}
    
    def matches_text(self, text: str) -> bool:
        """Verifica se texto contém alguma keyword desta regra."""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self.intent_keywords)


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
        self._transfer_rules_cache: Dict[str, CacheEntry] = {}
        
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
    
    async def get_transfer_rules(
        self,
        domain_uuid: str,
        secretary_uuid: Optional[str] = None
    ) -> List[TransferRule]:
        """
        Obtém regras de transferência para uma secretária ou domain.
        
        Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (3.1-3.3)
        
        Args:
            domain_uuid: UUID do tenant
            secretary_uuid: UUID da secretária (opcional, busca regras globais se None)
        
        Returns:
            Lista de TransferRule ordenada por prioridade
        """
        cache_key = self._cache_key("transfer_rules", domain_uuid, secretary_uuid or "global")
        
        # Verificar cache
        async with self._lock:
            if cache_key in self._transfer_rules_cache:
                entry = self._transfer_rules_cache[cache_key]
                if not entry.is_expired:
                    logger.debug(f"Transfer rules cache hit: {cache_key}")
                    return entry.data
                else:
                    del self._transfer_rules_cache[cache_key]
        
        # Buscar do banco
        rules = await self._load_transfer_rules_from_db(domain_uuid, secretary_uuid)
        
        # Armazenar em cache (mesmo se lista vazia, para evitar queries repetidas)
        async with self._lock:
            self._transfer_rules_cache[cache_key] = CacheEntry(
                data=rules,
                ttl_seconds=self.cache_ttl
            )
            self._cleanup_cache(self._transfer_rules_cache)
        
        logger.info(f"Transfer rules loaded: {len(rules)} rules for domain={domain_uuid}, secretary={secretary_uuid}")
        return rules
    
    async def _load_transfer_rules_from_db(
        self,
        domain_uuid: str,
        secretary_uuid: Optional[str] = None
    ) -> List[TransferRule]:
        """
        Carrega regras de transferência do banco.
        
        Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (3.2)
        Tabela: v_voice_transfer_rules
        
        Comportamento:
        - Se secretary_uuid fornecido: busca regras específicas + globais
        - Se secretary_uuid None: busca apenas regras globais do domain
        """
        rules: List[TransferRule] = []
        
        try:
            async with self.db_pool.acquire() as conn:
                # Query busca regras específicas da secretária OU regras globais (secretary_uuid IS NULL)
                if secretary_uuid:
                    rows = await conn.fetch("""
                        SELECT 
                            transfer_rule_uuid,
                            voice_secretary_uuid,
                            domain_uuid,
                            department_name,
                            intent_keywords,
                            transfer_extension,
                            transfer_message,
                            priority,
                            is_enabled
                        FROM v_voice_transfer_rules
                        WHERE domain_uuid = $1
                          AND (voice_secretary_uuid = $2 OR voice_secretary_uuid IS NULL)
                          AND is_enabled = true
                        ORDER BY priority ASC, department_name ASC
                    """, domain_uuid, secretary_uuid)
                else:
                    # Apenas regras globais do domain
                    rows = await conn.fetch("""
                        SELECT 
                            transfer_rule_uuid,
                            voice_secretary_uuid,
                            domain_uuid,
                            department_name,
                            intent_keywords,
                            transfer_extension,
                            transfer_message,
                            priority,
                            is_enabled
                        FROM v_voice_transfer_rules
                        WHERE domain_uuid = $1
                          AND voice_secretary_uuid IS NULL
                          AND is_enabled = true
                        ORDER BY priority ASC, department_name ASC
                    """, domain_uuid)
                
                for row in rows:
                    # intent_keywords pode vir como array do PostgreSQL ou JSON
                    keywords = row['intent_keywords']
                    if keywords is None:
                        keywords = []
                    elif isinstance(keywords, str):
                        import json
                        try:
                            keywords = json.loads(keywords)
                        except json.JSONDecodeError:
                            # Se não for JSON, tratar como string separada por vírgula
                            keywords = [k.strip() for k in keywords.split(',') if k.strip()]
                    
                    rules.append(TransferRule(
                        transfer_rule_uuid=str(row['transfer_rule_uuid']),
                        voice_secretary_uuid=str(row['voice_secretary_uuid']) if row['voice_secretary_uuid'] else None,
                        domain_uuid=str(row['domain_uuid']),
                        department_name=row['department_name'] or '',
                        intent_keywords=keywords if isinstance(keywords, list) else list(keywords),
                        transfer_extension=row['transfer_extension'] or '',
                        transfer_message=row['transfer_message'],
                        priority=row['priority'] or 0,
                        is_enabled=row['is_enabled'],
                    ))
                
        except Exception as e:
            logger.error(f"Error loading transfer rules: {e}", extra={
                "domain_uuid": domain_uuid,
                "secretary_uuid": secretary_uuid
            })
        
        return rules
    
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
            if cache_type in (None, 'transfer_rules'):
                caches.append(self._transfer_rules_cache)
            
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
            "transfer_rules_cache_size": len(self._transfer_rules_cache),
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


def build_transfer_context(rules: List[TransferRule], language: str = "pt-BR") -> str:
    """
    Constrói contexto de transferência para injetar no system prompt do LLM.
    
    Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (5.1)
    Ref: openspec/changes/add-realtime-handoff-omni/design.md (Decision 2)
    
    Args:
        rules: Lista de TransferRule ordenada por prioridade
        language: Idioma para as instruções
    
    Returns:
        Texto formatado para incluir no system prompt
    """
    if not rules:
        return ""
    
    # Textos por idioma
    texts = {
        "pt-BR": {
            "header": "\n\n## Transferência de Chamadas\n\nQuando o cliente quiser falar com alguém específico ou um departamento, use a função `transfer_call` com o destino apropriado.\n\n### Departamentos Disponíveis:\n",
            "keywords": "Keywords",
            "extension": "Ramal",
            "instruction": "\n### Instruções:\n- Identifique a intenção do cliente baseado nas keywords ou contexto\n- Use `transfer_call(destination=\"RAMAL\", department=\"NOME\")` para transferir\n- Sempre confirme a transferência com o cliente antes de executar\n- Exemplo: `transfer_call(destination=\"1000\", department=\"Vendas\")`\n"
        },
        "en-US": {
            "header": "\n\n## Call Transfer\n\nWhen the customer wants to speak with someone specific or a department, use the `transfer_call` function with the appropriate destination.\n\n### Available Departments:\n",
            "keywords": "Keywords",
            "extension": "Extension",
            "instruction": "\n### Instructions:\n- Identify the customer's intent based on keywords or context\n- Use `transfer_call(destination=\"EXT\", department=\"NAME\")` to transfer\n- Always confirm the transfer with the customer before executing\n- Example: `transfer_call(destination=\"1000\", department=\"Sales\")`\n"
        }
    }
    
    # Usar pt-BR como fallback
    t = texts.get(language, texts["pt-BR"])
    
    lines = [t["header"]]
    
    for rule in rules:
        keywords_str = ", ".join(rule.intent_keywords[:5])  # Limitar para não sobrecarregar
        if len(rule.intent_keywords) > 5:
            keywords_str += "..."
        
        lines.append(f"- **{rule.department_name}** ({t['extension']} {rule.transfer_extension})")
        if keywords_str:
            lines.append(f"  - {t['keywords']}: {keywords_str}")
        if rule.transfer_message:
            lines.append(f"  - Mensagem: \"{rule.transfer_message}\"")
    
    lines.append(t["instruction"])
    
    return "\n".join(lines)


def build_transfer_tools_schema() -> List[Dict[str, Any]]:
    """
    Retorna schema de tools para function calling do LLM.
    
    Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (5.1)
    
    Returns:
        Lista de tool definitions no formato OpenAI/Anthropic
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "transfer_call",
                "description": "Transfere a chamada para um departamento ou ramal específico. Use quando o cliente quiser falar com uma pessoa ou departamento específico.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "destination": {
                            "type": "string",
                            "description": "Número do ramal de destino (ex: '1000', '2000')"
                        },
                        "department": {
                            "type": "string",
                            "description": "Nome do departamento para informar ao cliente (ex: 'Vendas', 'Suporte')"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Motivo da transferência (ex: 'cliente quer comprar', 'problema técnico')"
                        }
                    },
                    "required": ["destination", "department"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "end_call",
                "description": "Encerra a chamada após resolver o assunto do cliente.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Motivo do encerramento (ex: 'assunto resolvido', 'cliente desligou')"
                        }
                    },
                    "required": []
                }
            }
        }
    ]


# =============================================================================
# Helper function for ESL mode
# =============================================================================

async def load_secretary_config(
    domain_uuid: str,
    secretary_uuid: str,
) -> Optional[Dict[str, Any]]:
    """
    Carrega configuração da secretária pelo UUID.
    
    Helper function para uso no ESL mode.
    
    Args:
        domain_uuid: UUID do domínio (tenant)
        secretary_uuid: UUID da secretária
    
    Returns:
        Dict com configuração ou None se não encontrada
    """
    # Usar get_config_loader() para obter a instância singleton
    loader = get_config_loader()
    
    if loader is None:
        # Se o loader não foi inicializado, criar conexão direta
        import os
        import asyncpg
        
        db_host = os.getenv("DB_HOST", "host.docker.internal")
        db_port = int(os.getenv("DB_PORT", "5432"))
        db_name = os.getenv("DB_NAME", "fusionpbx")
        db_user = os.getenv("DB_USER", "fusionpbx")
        db_pass = os.getenv("DB_PASS", "")
        
        try:
            conn = await asyncpg.connect(
                host=db_host,
                port=db_port,
                database=db_name,
                user=db_user,
                password=db_pass,
            )
            
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
            
            await conn.close()
            
            if not row:
                logger.warning(
                    f"Secretary not found: domain={domain_uuid}, uuid={secretary_uuid}"
                )
                return None
            
            # Config pode vir como string JSON
            import json
            provider_config = row['realtime_provider_config']
            if isinstance(provider_config, str):
                provider_config = json.loads(provider_config)
            
            return {
                "secretary_uuid": str(row['voice_secretary_uuid']),
                "domain_uuid": str(row['domain_uuid']),
                "secretary_name": row['secretary_name'] or 'Secretária',
                "extension": row['extension'] or '',
                "system_prompt": row['personality_prompt'] or '',
                "first_message": row['greeting_message'] or 'Olá!',
                "farewell": row['farewell_message'] or 'Até logo!',
                "provider_name": row['realtime_provider_name'] or "elevenlabs",
                "voice": row['tts_voice_id'] or 'alloy',
                "language": row['language'] or 'pt-BR',
                "provider_config": provider_config or {},
            }
            
        except Exception as e:
            logger.error(f"Error loading secretary config from DB: {e}")
            return None
    
    # Se loader já foi inicializado, usar normalmente
    config = await loader.get_secretary_by_uuid(domain_uuid, secretary_uuid)
    
    if not config:
        logger.warning(
            f"Secretary not found: domain={domain_uuid}, uuid={secretary_uuid}"
        )
        return None
    
    # Converter para dict para compatibilidade
    return {
        "secretary_uuid": config.secretary_uuid,
        "domain_uuid": config.domain_uuid,
        "secretary_name": config.name,
        "extension": config.extension,
        "system_prompt": config.system_prompt,
        "first_message": config.greeting_message,
        "farewell": config.farewell_message,
        "provider_name": config.realtime_provider or "elevenlabs",
        "voice": config.voice,
        "language": config.language,
        "provider_config": config.realtime_provider_config,
    }


def validate_transfer_config(
    handoff_keywords: List[str],
    transfer_extension: str,
    transfer_rules: List[TransferRule],
    domain_uuid: str,
    secretary_uuid: str,
) -> List[str]:
    """
    Valida se há conflitos entre Transfer Settings e Transfer Rules.
    
    Ref: voice-ai-ivr/docs/TRANSFER_SETTINGS_VS_RULES.md
    
    Args:
        handoff_keywords: Keywords de handoff da Secretary
        transfer_extension: Ramal padrão de transferência da Secretary  
        transfer_rules: Lista de Transfer Rules configuradas
        domain_uuid: UUID do tenant
        secretary_uuid: UUID da secretária
    
    Returns:
        Lista de warnings (vazio se nenhum conflito)
    """
    warnings: List[str] = []
    
    # Normalizar keywords para lowercase
    handoff_keywords_lower = {kw.lower().strip() for kw in handoff_keywords if kw.strip()}
    
    # Coletar todas as keywords das transfer rules
    rule_keywords: Dict[str, str] = {}  # keyword -> department_name
    rule_extensions: Dict[str, str] = {}  # extension -> department_name
    
    for rule in transfer_rules:
        rule_extensions[rule.transfer_extension] = rule.department_name
        
        for kw in rule.intent_keywords:
            kw_lower = kw.lower().strip()
            if kw_lower:
                rule_keywords[kw_lower] = rule.department_name
    
    # Check 1: Keywords sobrepostas
    overlapping = handoff_keywords_lower & set(rule_keywords.keys())
    if overlapping:
        for kw in overlapping:
            dept = rule_keywords[kw]
            warnings.append(
                f"⚠️ CONFLITO: Keyword '{kw}' está em Handoff Keywords E em Transfer Rule '{dept}'. "
                f"Isso fará o handoff ir para o ramal padrão ({transfer_extension}) em vez do departamento."
            )
    
    # Check 2: Transfer Extension igual a algum departamento
    if transfer_extension in rule_extensions:
        dept = rule_extensions[transfer_extension]
        warnings.append(
            f"⚠️ AVISO: Transfer Extension ({transfer_extension}) é igual ao ramal de '{dept}'. "
            f"Considere usar um ramal de recepção/geral para handoff genérico."
        )
    
    # Check 3: Handoff keywords contêm nomes de departamentos
    dept_names_lower = {rule.department_name.lower() for rule in transfer_rules}
    dept_in_keywords = handoff_keywords_lower & dept_names_lower
    if dept_in_keywords:
        for dept in dept_in_keywords:
            warnings.append(
                f"⚠️ CONFLITO: Nome de departamento '{dept}' está em Handoff Keywords. "
                f"Isso ignorará a Transfer Rule e usará o ramal padrão."
            )
    
    # Log warnings
    if warnings:
        logger.warning(
            f"Transfer config validation found {len(warnings)} issues",
            extra={
                "domain_uuid": domain_uuid,
                "secretary_uuid": secretary_uuid,
                "warnings": warnings,
            }
        )
    else:
        logger.debug(
            "Transfer config validation passed",
            extra={
                "domain_uuid": domain_uuid,
                "secretary_uuid": secretary_uuid,
            }
        )
    
    return warnings
