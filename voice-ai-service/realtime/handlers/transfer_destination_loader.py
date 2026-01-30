"""
TransferDestinationLoader - Carrega destinos de transferência do banco de dados.

Referências:
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/tasks.md (1.1)
- voice-ai-ivr/database/migrations/012_create_voice_transfer_destinations.sql

Multi-tenant: domain_uuid obrigatório em todas as operações.

Funcionalidades:
- Carrega destinos da tabela v_voice_transfer_destinations
- Cache em memória com TTL de 5 minutos
- Fuzzy matching para aliases
- Verificação de horário comercial
"""

import os
import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional, Any
from difflib import SequenceMatcher
import json

import asyncpg

# Import pytz com fallback
try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    pytz = None  # type: ignore
    PYTZ_AVAILABLE = False
    logging.warning("pytz not installed. Timezone support will be limited.")

logger = logging.getLogger(__name__)

# Configurações do banco
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "fusionpbx")
DB_USER = os.getenv("DB_USER", "fusionpbx")
DB_PASS = os.getenv("DB_PASS", "")

# Cache TTL
CACHE_TTL_SECONDS = int(os.getenv("TRANSFER_CACHE_TTL_SECONDS", "300"))  # 5 minutos


@dataclass
class TransferDestination:
    """Destino de transferência carregado do banco."""
    uuid: str
    name: str
    aliases: List[str]
    destination_type: str  # extension, ring_group, queue, external, voicemail
    destination_number: str
    destination_context: str
    ring_timeout_seconds: int
    max_retries: int
    retry_delay_seconds: int
    fallback_action: str  # offer_ticket, create_ticket, voicemail, return_agent, hangup
    department: Optional[str]
    role: Optional[str]
    description: Optional[str]
    working_hours: Optional[Dict[str, Any]]
    priority: int
    is_default: bool = False
    
    def matches_text(self, text: str) -> float:
        """
        Calcula score de match entre texto e este destino.
        
        Retorna score entre 0.0 e 1.0.
        """
        text_lower = text.lower().strip()
        
        # 1. Match exato em aliases - score máximo
        for alias in self.aliases:
            if alias.lower() == text_lower:
                return 1.0
        
        # 2. Match exato no nome
        if self.name.lower() == text_lower:
            return 0.95
        
        # 3. Alias contido no texto ou texto contido em alias
        for alias in self.aliases:
            alias_lower = alias.lower()
            if alias_lower in text_lower or text_lower in alias_lower:
                return 0.85
        
        # 4. Nome contido no texto
        if self.name.lower() in text_lower:
            return 0.80
        
        # 5. Match no departamento
        if self.department and self.department.lower() in text_lower:
            return 0.70
        
        # 6. Match no role
        if self.role and self.role.lower() in text_lower:
            return 0.65
        
        # 7. Fuzzy matching usando SequenceMatcher
        best_ratio = 0.0
        
        # Comparar com nome
        ratio = SequenceMatcher(None, text_lower, self.name.lower()).ratio()
        best_ratio = max(best_ratio, ratio)
        
        # Comparar com aliases
        for alias in self.aliases:
            ratio = SequenceMatcher(None, text_lower, alias.lower()).ratio()
            best_ratio = max(best_ratio, ratio)
        
        # Só retornar se ratio > 0.6 (match razoável)
        if best_ratio > 0.6:
            return best_ratio * 0.6  # Diminuir peso do fuzzy
        
        return 0.0


@dataclass
class CacheEntry:
    """Entrada de cache com TTL."""
    destinations: List[TransferDestination]
    timestamp: float
    
    def is_expired(self, ttl_seconds: int) -> bool:
        import time
        return time.time() - self.timestamp > ttl_seconds


class TransferDestinationLoader:
    """
    Carrega e gerencia destinos de transferência.
    
    Uso:
        loader = TransferDestinationLoader()
        destinations = await loader.load_destinations(domain_uuid, secretary_uuid)
        dest = loader.find_by_alias("Jeni", destinations)
    """
    
    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._cache: Dict[str, CacheEntry] = {}
        self._cache_lock = asyncio.Lock()
    
    async def _get_pool(self) -> asyncpg.Pool:
        """Obtém pool de conexões do banco."""
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    host=DB_HOST,
                    port=DB_PORT,
                    database=DB_NAME,
                    user=DB_USER,
                    password=DB_PASS,
                    min_size=1,
                    max_size=5,
                    command_timeout=10
                )
                logger.info("Database pool created for TransferDestinationLoader")
            except Exception as e:
                logger.error(f"Failed to create database pool: {e}")
                raise
        return self._pool
    
    async def close(self):
        """Fecha pool de conexões."""
        if self._pool:
            await self._pool.close()
            self._pool = None
    
    def _cache_key(self, domain_uuid: str, secretary_uuid: Optional[str]) -> str:
        """Gera chave de cache."""
        return f"{domain_uuid}:{secretary_uuid or 'all'}"
    
    async def load_destinations(
        self,
        domain_uuid: str,
        secretary_uuid: Optional[str] = None,
        force_refresh: bool = False
    ) -> List[TransferDestination]:
        """
        Carrega destinos de transferência do banco.
        
        Args:
            domain_uuid: UUID do tenant
            secretary_uuid: UUID da secretária (opcional, filtra por secretária)
            force_refresh: Ignora cache
        
        Returns:
            Lista de TransferDestination
        """
        cache_key = self._cache_key(domain_uuid, secretary_uuid)
        
        # Verificar cache
        if not force_refresh:
            async with self._cache_lock:
                if cache_key in self._cache:
                    entry = self._cache[cache_key]
                    if not entry.is_expired(CACHE_TTL_SECONDS):
                        logger.debug(f"Cache hit for {cache_key}")
                        return entry.destinations
        
        # Carregar do banco
        try:
            pool = await self._get_pool()
            
            query = """
                SELECT 
                    transfer_destination_uuid::text as uuid,
                    name,
                    aliases,
                    destination_type,
                    destination_number,
                    destination_context,
                    ring_timeout_seconds,
                    max_retries,
                    retry_delay_seconds,
                    fallback_action,
                    department,
                    role,
                    description,
                    working_hours,
                    priority,
                    is_default
                FROM v_voice_transfer_destinations
                WHERE domain_uuid = $1
                  AND is_enabled = true
            """
            params = [domain_uuid]
            
            if secretary_uuid:
                query += " AND (secretary_uuid = $2 OR secretary_uuid IS NULL)"
                params.append(secretary_uuid)
            
            query += " ORDER BY priority ASC, name ASC"
            
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            
            destinations = []
            for row in rows:
                # Parse aliases JSON
                aliases_data = row["aliases"]
                if isinstance(aliases_data, str):
                    aliases = json.loads(aliases_data)
                elif isinstance(aliases_data, list):
                    aliases = aliases_data
                else:
                    aliases = []
                
                # Parse working_hours JSON
                working_hours = row["working_hours"]
                if isinstance(working_hours, str):
                    working_hours = json.loads(working_hours)
                
                destinations.append(TransferDestination(
                    uuid=row["uuid"],
                    name=row["name"],
                    aliases=aliases,
                    destination_type=row["destination_type"],
                    destination_number=row["destination_number"],
                    destination_context=row["destination_context"] or "default",
                    ring_timeout_seconds=row["ring_timeout_seconds"] or 30,
                    max_retries=row["max_retries"] or 1,
                    retry_delay_seconds=row["retry_delay_seconds"] or 5,
                    fallback_action=row["fallback_action"] or "offer_ticket",
                    department=row["department"],
                    role=row["role"],
                    description=row["description"],
                    working_hours=working_hours,
                    priority=row["priority"] or 100,
                    is_default=row["is_default"] or False
                ))
            
            # Atualizar cache
            async with self._cache_lock:
                import time
                self._cache[cache_key] = CacheEntry(
                    destinations=destinations,
                    timestamp=time.time()
                )
            
            # Log detalhado dos destinos carregados (MULTI-TENANT)
            destination_names = [d.name for d in destinations]
            logger.info(
                f"📋 [DESTINATIONS] Carregados {len(destinations)} destinos do banco: {destination_names}",
                extra={
                    "domain_uuid": domain_uuid,
                    "secretary_uuid": secretary_uuid,
                    "destinations": destination_names,
                }
            )
            
            # Log cada destino para debug
            for dest in destinations:
                logger.debug(
                    f"📋 [DESTINATIONS] {dest.name}: "
                    f"tipo={dest.destination_type}, "
                    f"número={dest.destination_number}, "
                    f"aliases={dest.aliases}, "
                    f"horário={dest.working_hours}"
                )
            
            return destinations
            
        except Exception as e:
            logger.error(f"Failed to load transfer destinations: {e}")
            return []
    
    def find_by_alias(
        self,
        text: str,
        destinations: List[TransferDestination],
        min_score: float = 0.5
    ) -> Optional[TransferDestination]:
        """
        Encontra destino por alias, nome ou departamento usando fuzzy matching.
        
        Args:
            text: Texto falado pelo cliente (ex: "Jeni", "financeiro", "suporte")
            destinations: Lista de destinos
            min_score: Score mínimo para considerar match (0.0 a 1.0)
        
        Returns:
            Melhor destino encontrado ou None
        """
        if not text or not destinations:
            return None
        
        best_match: Optional[TransferDestination] = None
        best_score = 0.0
        
        for dest in destinations:
            score = dest.matches_text(text)
            
            # Em caso de empate, preferir maior prioridade (menor número)
            if score > best_score or (score == best_score and dest.priority < (best_match.priority if best_match else 999)):
                best_score = score
                best_match = dest
        
        if best_score >= min_score:
            logger.info(
                f"Found destination match",
                extra={
                    "text": text,
                    "destination": best_match.name if best_match else None,
                    "score": best_score
                }
            )
            return best_match
        
        logger.debug(
            f"No destination match found",
            extra={
                "text": text,
                "best_score": best_score,
                "min_score": min_score
            }
        )
        return None
    
    def get_default(
        self,
        destinations: List[TransferDestination]
    ) -> Optional[TransferDestination]:
        """
        Retorna destino padrão (para "qualquer atendente disponível").
        
        Args:
            destinations: Lista de destinos
        
        Returns:
            Destino marcado como is_default ou primeiro com destination_type='queue'
        """
        # 1. Buscar destino marcado como default
        for dest in destinations:
            if dest.is_default:
                return dest
        
        # 2. Buscar primeira fila
        for dest in destinations:
            if dest.destination_type == "queue":
                return dest
        
        # 3. Buscar primeiro ring_group
        for dest in destinations:
            if dest.destination_type == "ring_group":
                return dest
        
        # 4. Retornar primeiro disponível
        return destinations[0] if destinations else None
    
    def is_within_working_hours(
        self,
        dest: TransferDestination,
        now: Optional[datetime] = None
    ) -> tuple[bool, str]:
        """
        Verifica se destino está em horário comercial.
        
        Args:
            dest: Destino a verificar
            now: Data/hora atual (opcional, usa agora se não fornecido)
        
        Returns:
            Tuple (is_available, message_if_unavailable)
        
        Suporta dois formatos de working_hours:
        
        Formato 1 (FusionPBX PHP - ATUAL):
        {
            "start": "08:00",
            "end": "15:00",
            "days": [1, 2, 3, 4, 5],  // 0=Dom, 1=Seg...6=Sab
            "timezone": "America/Sao_Paulo"
        }
        
        Formato 2 (schedule detalhado - LEGADO):
        {
            "timezone": "America/Sao_Paulo",
            "schedule": {
                "monday": [{"start": "08:00", "end": "18:00"}],
                ...
            }
        }
        """
        if not dest.working_hours:
            # Sem restrição de horário = sempre disponível
            return (True, "")
        
        try:
            wh = dest.working_hours
            
            # Obter timezone e aplicar
            tz_name = wh.get("timezone", "America/Sao_Paulo")
            if PYTZ_AVAILABLE and pytz is not None:
                try:
                    tz = pytz.timezone(tz_name)
                    if now is None:
                        now = datetime.now(tz)
                    elif now.tzinfo is None:
                        now = tz.localize(now)
                    else:
                        now = now.astimezone(tz)
                except Exception as tz_err:
                    logger.debug(f"Timezone error (using local): {tz_err}")
                    if now is None:
                        now = datetime.now()
            else:
                if now is None:
                    now = datetime.now()
            
            # Detectar formato do working_hours
            if "schedule" in wh:
                # Formato 2: schedule detalhado (legado)
                return self._check_schedule_format(dest, wh, now)
            elif "days" in wh:
                # Formato 1: FusionPBX PHP (atual)
                return self._check_days_format(dest, wh, now)
            else:
                # Formato desconhecido, considerar disponível
                logger.warning(f"Unknown working_hours format for {dest.name}: {wh}")
                return (True, "")
            
        except Exception as e:
            logger.warning(f"Error checking working hours for {dest.name}: {e}")
            # Em caso de erro, considerar disponível
            return (True, "")
    
    def _check_days_format(
        self,
        dest: TransferDestination,
        wh: Dict[str, Any],
        now: datetime
    ) -> tuple[bool, str]:
        """
        Verifica disponibilidade no formato FusionPBX PHP.
        
        Formato:
        {
            "start": "08:00",
            "end": "15:00",
            "days": [1, 2, 3, 4, 5],  // 0=Dom, 1=Seg...6=Sab
            "timezone": "America/Sao_Paulo"
        }
        
        IMPORTANTE: PHP usa 0=Domingo, Python usa 0=Segunda!
        """
        days = wh.get("days", [])
        start_str = wh.get("start", "00:00")
        end_str = wh.get("end", "23:59")
        
        # Converter dia Python (0=Seg) para formato PHP (0=Dom)
        # Python weekday(): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
        # PHP/JS: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
        python_weekday = now.weekday()  # 0=Mon...6=Sun
        php_day = (python_weekday + 1) % 7  # Converte para 0=Sun...6=Sat
        
        logger.debug(
            f"[WORKING_HOURS] {dest.name}: python_weekday={python_weekday}, "
            f"php_day={php_day}, configured_days={days}, now={now}"
        )
        
        if php_day not in days:
            day_names = {0: "Dom", 1: "Seg", 2: "Ter", 3: "Qua", 4: "Qui", 5: "Sex", 6: "Sáb"}
            available_days = ", ".join([day_names.get(d, str(d)) for d in sorted(days)])
            callback_offer = " Prefere que liguemos de volta quando estiver disponível, ou quer deixar um recado?"
            return (False, f"{dest.name} não está disponível hoje. Atende: {available_days}.{callback_offer}")
        
        # Verificar horário
        current_time = now.time()
        
        start_parts = start_str.split(":")
        end_parts = end_str.split(":")
        
        start_time = time(int(start_parts[0]), int(start_parts[1]))
        end_time = time(int(end_parts[0]), int(end_parts[1]))
        
        if start_time <= current_time <= end_time:
            return (True, "")
        
        # Fora do horário mas no dia certo
        # IMPORTANTE: Oferecer CALLBACK como primeira opção (mais útil para o cliente)
        callback_offer = " Prefere que liguemos de volta quando estiver disponível, ou quer deixar um recado?"
        if current_time < start_time:
            return (False, f"{dest.name} abre às {start_str}.{callback_offer}")
        else:
            return (False, f"{dest.name} fechou às {end_str}.{callback_offer}")
    
    def _check_schedule_format(
        self,
        dest: TransferDestination,
        wh: Dict[str, Any],
        now: datetime
    ) -> tuple[bool, str]:
        """
        Verifica disponibilidade no formato schedule detalhado (legado).
        
        Formato:
        {
            "schedule": {
                "monday": [{"start": "08:00", "end": "18:00"}],
                ...
            }
        }
        """
        schedule = wh.get("schedule", {})
        
        # Mapear dia da semana
        day_map = {
            0: "monday",
            1: "tuesday",
            2: "wednesday",
            3: "thursday",
            4: "friday",
            5: "saturday",
            6: "sunday"
        }
        
        day_name = day_map.get(now.weekday())
        day_schedule = schedule.get(day_name, [])
        
        if not day_schedule:
            # Não trabalha neste dia
            callback_offer = " Prefere que liguemos de volta quando estiver disponível, ou quer deixar um recado?"
            return (False, f"{dest.name} não está disponível hoje.{callback_offer}")
        
        current_time = now.time()
        
        for slot in day_schedule:
            start_str = slot.get("start", "00:00")
            end_str = slot.get("end", "23:59")
            
            start_parts = start_str.split(":")
            end_parts = end_str.split(":")
            
            start_time = time(int(start_parts[0]), int(start_parts[1]))
            end_time = time(int(end_parts[0]), int(end_parts[1]))
            
            if start_time <= current_time <= end_time:
                return (True, "")
        
        # Fora do horário
        next_slot = day_schedule[0]
        callback_offer = " Prefere que liguemos de volta quando estiver disponível, ou quer deixar um recado?"
        return (
            False,
            f"{dest.name} está disponível a partir das {next_slot.get('start', '08:00')}.{callback_offer}"
        )
    
    def invalidate_cache(self, domain_uuid: Optional[str] = None):
        """
        Invalida cache.
        
        Args:
            domain_uuid: Se fornecido, invalida apenas para este domain.
                        Se None, invalida todo o cache.
        """
        if domain_uuid:
            keys_to_remove = [
                key for key in self._cache.keys()
                if key.startswith(f"{domain_uuid}:")
            ]
            for key in keys_to_remove:
                del self._cache[key]
        else:
            self._cache.clear()
        
        logger.info(f"Cache invalidated for domain_uuid={domain_uuid or 'all'}")


# Singleton para uso global
_loader_instance: Optional[TransferDestinationLoader] = None


def get_destination_loader() -> TransferDestinationLoader:
    """Retorna instância singleton do loader."""
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = TransferDestinationLoader()
    return _loader_instance
