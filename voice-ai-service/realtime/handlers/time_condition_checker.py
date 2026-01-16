"""
TimeConditionChecker - Verifica condições de horário comercial do FusionPBX.

Referências:
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/tasks.md
- FusionPBX Time Conditions: https://docs.fusionpbx.com/en/latest/applications/time_conditions.html

Multi-tenant: domain_uuid obrigatório em todas as operações.

Funcionalidades:
- Carrega time_condition do banco de dados FusionPBX
- Verifica se horário atual está dentro das regras configuradas
- Suporte a múltiplos slots de horário por dia
- Cache em memória com TTL configurável
- Suporte a timezone do domínio

Tabelas FusionPBX utilizadas:
- v_time_conditions: Condições de horário principais
- v_time_condition_details: Detalhes (horários específicos) - NÃO usado, FusionPBX usa dialplan
- v_dialplans: Onde as regras reais ficam armazenadas como XML

NOTA: No FusionPBX, time_conditions são implementadas via dialplan XML.
Para simplificar, vamos usar uma abordagem baseada em:
1. Buscar timezone do domínio
2. Usar regras simplificadas baseadas no nome da time_condition
3. OU parsear o dialplan XML se necessário

Para MVP, vamos implementar verificação baseada em horário comercial padrão
quando time_condition_uuid está configurado.
"""

import os
import logging
import asyncio
import time as time_module
import json
from dataclasses import dataclass, field
from datetime import datetime, time, date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

# Timezone support - importar com fallback para evitar quebra se não instalado
try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    pytz = None  # type: ignore
    PYTZ_AVAILABLE = False
    logging.warning("pytz not installed. Timezone support will be limited.")

import asyncpg

logger = logging.getLogger(__name__)

# Configurações do banco
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "fusionpbx")
DB_USER = os.getenv("DB_USER", "fusionpbx")
DB_PASS = os.getenv("DB_PASS", "")

# Cache TTL
CACHE_TTL_SECONDS = int(os.getenv("TIME_CONDITION_CACHE_TTL", "300"))  # 5 minutos

# Timezone padrão do Brasil
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/Sao_Paulo")


class TimeConditionStatus(Enum):
    """Status da verificação de horário."""
    OPEN = "open"              # Dentro do horário comercial
    CLOSED = "closed"          # Fora do horário comercial
    HOLIDAY = "holiday"        # Feriado
    NO_CONDITION = "none"      # Sem restrição configurada
    ERROR = "error"            # Erro na verificação


@dataclass
class TimeConditionResult:
    """Resultado da verificação de time condition."""
    status: TimeConditionStatus
    is_open: bool
    message: str
    time_condition_name: Optional[str] = None
    next_open: Optional[datetime] = None  # Próximo horário de abertura
    details: Optional[Dict[str, Any]] = None
    
    @property
    def should_create_ticket(self) -> bool:
        """Retorna True se deve criar ticket/callback ao invés de transferir."""
        return self.status in [
            TimeConditionStatus.CLOSED,
            TimeConditionStatus.HOLIDAY
        ]


@dataclass
class TimeSlot:
    """Um slot de horário (ex: 08:00-12:00)."""
    start: time
    end: time
    
    def contains(self, t: time) -> bool:
        """Verifica se horário está dentro do slot."""
        # Tratar caso de horário que cruza meia-noite
        if self.start <= self.end:
            return self.start <= t <= self.end
        else:
            # Ex: 22:00-06:00 (turno noturno)
            return t >= self.start or t <= self.end


@dataclass
class DaySchedule:
    """Horários de um dia da semana."""
    day: int  # 0=Monday, 6=Sunday (padrão Python)
    slots: List[TimeSlot] = field(default_factory=list)
    
    def is_open(self, t: time) -> bool:
        """Verifica se está aberto neste horário."""
        return any(slot.contains(t) for slot in self.slots)


@dataclass
class TimeConditionConfig:
    """Configuração completa de uma time condition."""
    uuid: str
    name: str
    domain_uuid: str
    timezone: str
    schedule: Dict[int, DaySchedule]  # day -> DaySchedule
    holidays: List[datetime] = field(default_factory=list)
    is_enabled: bool = True
    
    def get_schedule_for_day(self, day: int) -> Optional[DaySchedule]:
        """Retorna schedule para um dia específico."""
        return self.schedule.get(day)


@dataclass
class CacheEntry:
    """Entrada de cache com TTL."""
    config: TimeConditionConfig
    timestamp: float
    
    def is_expired(self, ttl_seconds: int) -> bool:
        return time_module.time() - self.timestamp > ttl_seconds


class TimeConditionChecker:
    """
    Verifica condições de horário comercial.
    
    Uso:
        checker = TimeConditionChecker()
        result = await checker.check(domain_uuid, time_condition_uuid)
        if result.is_open:
            # Pode transferir
        else:
            # Criar ticket/callback
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
                    max_size=3,
                    command_timeout=10
                )
                logger.info("Database pool created for TimeConditionChecker")
            except Exception as e:
                logger.error(f"Failed to create database pool: {e}")
                raise
        return self._pool
    
    async def close(self):
        """Fecha pool de conexões."""
        if self._pool:
            await self._pool.close()
            self._pool = None
    
    async def check(
        self,
        domain_uuid: str,
        time_condition_uuid: Optional[str],
        now: Optional[datetime] = None
    ) -> TimeConditionResult:
        """
        Verifica se está dentro do horário comercial.
        
        Args:
            domain_uuid: UUID do tenant
            time_condition_uuid: UUID da time condition (None = sem restrição)
            now: Data/hora atual (opcional, usa agora se não fornecido)
        
        Returns:
            TimeConditionResult com status da verificação
        """
        # Se não há time_condition configurada, sempre aberto
        if not time_condition_uuid:
            return TimeConditionResult(
                status=TimeConditionStatus.NO_CONDITION,
                is_open=True,
                message="Sem restrição de horário configurada."
            )
        
        try:
            # Carregar configuração (com cache)
            config = await self._load_config(domain_uuid, time_condition_uuid)
            
            if not config:
                logger.warning(
                    f"Time condition not found: {time_condition_uuid}",
                    extra={"domain_uuid": domain_uuid}
                )
                # Se não encontrou, considerar aberto (fail-open)
                return TimeConditionResult(
                    status=TimeConditionStatus.NO_CONDITION,
                    is_open=True,
                    message="Condição de horário não encontrada, considerando aberto."
                )
            
            if not config.is_enabled:
                return TimeConditionResult(
                    status=TimeConditionStatus.NO_CONDITION,
                    is_open=True,
                    message=f"Condição '{config.name}' desabilitada.",
                    time_condition_name=config.name
                )
            
            # Obter horário atual no timezone correto
            if now is None:
                now = datetime.now()
            
            # Converter para timezone do domínio
            local_now = now
            if PYTZ_AVAILABLE and pytz is not None:
                try:
                    tz = pytz.timezone(config.timezone)
                    if now.tzinfo is None:
                        # Assumir UTC se não tem timezone
                        now = pytz.UTC.localize(now)
                    local_now = now.astimezone(tz)
                except Exception as e:
                    logger.warning(f"Timezone error: {e}, using local time")
                    local_now = now
            
            # Verificar feriados primeiro
            date_only = local_now.date()
            for holiday in config.holidays:
                if holiday.date() == date_only:
                    return TimeConditionResult(
                        status=TimeConditionStatus.HOLIDAY,
                        is_open=False,
                        message=f"Estamos em feriado. Retornaremos em breve.",
                        time_condition_name=config.name,
                        details={"holiday_date": str(date_only)}
                    )
            
            # Verificar schedule do dia
            day_of_week = local_now.weekday()  # 0=Monday, 6=Sunday
            current_time = local_now.time()
            
            day_schedule = config.get_schedule_for_day(day_of_week)
            
            if not day_schedule or not day_schedule.slots:
                # Não trabalha neste dia
                next_open = self._find_next_open(config, local_now)
                return TimeConditionResult(
                    status=TimeConditionStatus.CLOSED,
                    is_open=False,
                    message=self._build_closed_message(config.name, next_open),
                    time_condition_name=config.name,
                    next_open=next_open,
                    details={"day_of_week": day_of_week}
                )
            
            if day_schedule.is_open(current_time):
                return TimeConditionResult(
                    status=TimeConditionStatus.OPEN,
                    is_open=True,
                    message=f"Dentro do horário de atendimento.",
                    time_condition_name=config.name,
                    details={
                        "day_of_week": day_of_week,
                        "current_time": str(current_time)
                    }
                )
            else:
                # Fora do horário
                next_open = self._find_next_open(config, local_now)
                return TimeConditionResult(
                    status=TimeConditionStatus.CLOSED,
                    is_open=False,
                    message=self._build_closed_message(config.name, next_open),
                    time_condition_name=config.name,
                    next_open=next_open,
                    details={
                        "day_of_week": day_of_week,
                        "current_time": str(current_time)
                    }
                )
                
        except Exception as e:
            logger.error(
                f"Error checking time condition: {e}",
                extra={
                    "domain_uuid": domain_uuid,
                    "time_condition_uuid": time_condition_uuid
                },
                exc_info=True
            )
            # Fail-open: em caso de erro, permitir (não bloquear chamadas)
            return TimeConditionResult(
                status=TimeConditionStatus.ERROR,
                is_open=True,
                message="Erro ao verificar horário, prosseguindo normalmente.",
                details={"error": str(e)}
            )
    
    async def _load_config(
        self,
        domain_uuid: str,
        time_condition_uuid: str,
        force_refresh: bool = False
    ) -> Optional[TimeConditionConfig]:
        """Carrega configuração de time condition do banco."""
        cache_key = f"{domain_uuid}:{time_condition_uuid}"
        
        # Verificar cache
        if not force_refresh:
            async with self._cache_lock:
                if cache_key in self._cache:
                    entry = self._cache[cache_key]
                    if not entry.is_expired(CACHE_TTL_SECONDS):
                        logger.debug(f"Time condition cache hit: {cache_key}")
                        return entry.config
        
        # Carregar do banco
        try:
            pool = await self._get_pool()
            
            async with pool.acquire() as conn:
                # Buscar time condition principal
                row = await conn.fetchrow("""
                    SELECT 
                        time_condition_uuid::text as uuid,
                        time_condition_name as name,
                        domain_uuid::text,
                        time_condition_enabled as is_enabled,
                        COALESCE(time_condition_param, '') as param,
                        COALESCE(time_condition_preset, '') as preset
                    FROM v_time_conditions
                    WHERE time_condition_uuid = $1::uuid
                      AND domain_uuid = $2::uuid
                """, time_condition_uuid, domain_uuid)
                
                if not row:
                    return None
                
                # Buscar timezone do domínio
                tz_row = await conn.fetchrow("""
                    SELECT 
                        COALESCE(
                            (SELECT default_setting_value FROM v_default_settings 
                             WHERE default_setting_category = 'domain' 
                             AND default_setting_subcategory = 'time_zone'
                             AND default_setting_enabled = true
                             LIMIT 1),
                            $1
                        ) as timezone
                """, DEFAULT_TIMEZONE)
                
                timezone = tz_row["timezone"] if tz_row else DEFAULT_TIMEZONE
                
                # Parsear schedule do preset ou param
                schedule = self._parse_schedule(
                    row["param"],
                    row["preset"],
                    row["name"]
                )
                
                config = TimeConditionConfig(
                    uuid=row["uuid"],
                    name=row["name"],
                    domain_uuid=row["domain_uuid"],
                    timezone=timezone,
                    schedule=schedule,
                    holidays=[],  # TODO: Carregar de tabela de feriados se existir
                    is_enabled=row["is_enabled"]
                )
                
                # Salvar em cache
                async with self._cache_lock:
                    self._cache[cache_key] = CacheEntry(
                        config=config,
                        timestamp=time_module.time()
                    )
                
                logger.info(
                    f"Time condition loaded: {config.name}",
                    extra={
                        "domain_uuid": domain_uuid,
                        "timezone": timezone,
                        "schedule_days": list(schedule.keys())
                    }
                )
                
                return config
                
        except Exception as e:
            logger.error(f"Failed to load time condition: {e}")
            return None
    
    def _parse_schedule(
        self,
        param: str,
        preset: str,
        name: str
    ) -> Dict[int, DaySchedule]:
        """
        Parseia schedule baseado nos parâmetros.
        
        O FusionPBX armazena as regras de forma complexa no dialplan.
        Para simplificar, vamos usar presets comuns ou inferir do nome.
        """
        schedule: Dict[int, DaySchedule] = {}
        
        # Tentar detectar pelo preset
        preset_lower = preset.lower() if preset else ""
        name_lower = name.lower() if name else ""
        
        # Horário comercial padrão Brasil: Seg-Sex 08:00-18:00
        default_slots = [TimeSlot(time(8, 0), time(18, 0))]
        
        # Detectar padrões comuns
        if "24" in name_lower or "always" in preset_lower:
            # 24 horas
            all_day = [TimeSlot(time(0, 0), time(23, 59, 59))]
            for day in range(7):
                schedule[day] = DaySchedule(day=day, slots=all_day)
        
        elif "weekend" in preset_lower or "fim de semana" in name_lower:
            # Apenas fins de semana
            for day in [5, 6]:  # Sab, Dom
                schedule[day] = DaySchedule(day=day, slots=default_slots)
        
        elif "weekday" in preset_lower or "semana" in name_lower:
            # Apenas dias de semana
            for day in range(5):  # Seg-Sex
                schedule[day] = DaySchedule(day=day, slots=default_slots)
        
        elif "comercial" in name_lower or "business" in preset_lower:
            # Horário comercial padrão
            commercial_slots = [
                TimeSlot(time(8, 0), time(12, 0)),
                TimeSlot(time(13, 0), time(18, 0))
            ]
            for day in range(5):  # Seg-Sex
                schedule[day] = DaySchedule(day=day, slots=commercial_slots)
        
        else:
            # Default: Seg-Sex 08:00-18:00
            for day in range(5):
                schedule[day] = DaySchedule(day=day, slots=default_slots)
        
        # Tentar parsear parâmetros específicos se existirem
        if param:
            try:
                # Formato esperado: dia:HH:MM-HH:MM,dia:HH:MM-HH:MM
                # ou JSON: {"0": ["08:00-18:00"], "1": ["08:00-18:00"]}
                if param.startswith("{"):
                    parsed = json.loads(param)
                    schedule = self._parse_json_schedule(parsed)
                else:
                    # Formato texto simples
                    schedule = self._parse_text_schedule(param)
            except Exception as e:
                logger.warning(f"Failed to parse schedule param: {e}")
        
        return schedule
    
    def _parse_json_schedule(self, data: Dict) -> Dict[int, DaySchedule]:
        """Parseia schedule de formato JSON."""
        schedule: Dict[int, DaySchedule] = {}
        
        for day_str, slots_list in data.items():
            try:
                day = int(day_str)
                slots = []
                
                for slot_str in slots_list:
                    if "-" in slot_str:
                        start_str, end_str = slot_str.split("-")
                        start = self._parse_time(start_str)
                        end = self._parse_time(end_str)
                        if start and end:
                            slots.append(TimeSlot(start, end))
                
                if slots:
                    schedule[day] = DaySchedule(day=day, slots=slots)
                    
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse day schedule: {e}")
        
        return schedule
    
    def _parse_text_schedule(self, text: str) -> Dict[int, DaySchedule]:
        """Parseia schedule de formato texto."""
        schedule: Dict[int, DaySchedule] = {}
        
        # Padrão: "0-4:08:00-18:00" ou "1:08:00-12:00,13:00-18:00"
        parts = text.split(";")
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            try:
                # Separar dia(s) dos horários
                if ":" not in part:
                    continue
                
                day_part, time_part = part.split(":", 1)
                
                # Parsear dias (pode ser range "0-4" ou único "5")
                if "-" in day_part and day_part.count("-") == 1:
                    start_day, end_day = map(int, day_part.split("-"))
                    days = range(start_day, end_day + 1)
                else:
                    days = [int(day_part)]
                
                # Parsear slots de horário
                slots = []
                time_ranges = time_part.split(",")
                for tr in time_ranges:
                    if "-" in tr:
                        start_str, end_str = tr.split("-")
                        start = self._parse_time(start_str)
                        end = self._parse_time(end_str)
                        if start and end:
                            slots.append(TimeSlot(start, end))
                
                # Adicionar para cada dia
                for day in days:
                    schedule[day] = DaySchedule(day=day, slots=slots)
                    
            except Exception as e:
                logger.warning(f"Failed to parse schedule part '{part}': {e}")
        
        return schedule
    
    def _parse_time(self, time_str: str) -> Optional[time]:
        """Parseia string de horário para objeto time."""
        time_str = time_str.strip()
        
        formats = ["%H:%M:%S", "%H:%M", "%H%M"]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(time_str, fmt)
                return dt.time()
            except ValueError:
                continue
        
        return None
    
    def _find_next_open(
        self,
        config: TimeConditionConfig,
        now: datetime
    ) -> Optional[datetime]:
        """Encontra próximo horário de abertura."""
        try:
            tz = None
            if PYTZ_AVAILABLE and pytz is not None:
                try:
                    tz = pytz.timezone(config.timezone)
                except Exception:
                    pass
            
            # Procurar nos próximos 7 dias
            for days_ahead in range(8):
                check_date = now + timedelta(days=days_ahead)
                day_of_week = check_date.weekday()
                
                day_schedule = config.get_schedule_for_day(day_of_week)
                if not day_schedule or not day_schedule.slots:
                    continue
                
                for slot in day_schedule.slots:
                    # Criar datetime com horário de início do slot
                    open_time = datetime.combine(
                        check_date.date(),
                        slot.start
                    )
                    
                    # Localizar no timezone se disponível
                    if tz is not None and open_time.tzinfo is None:
                        open_time = tz.localize(open_time)
                    
                    # Se é no futuro, retornar
                    # Comparar sem timezone se now não tem timezone
                    compare_now = now.replace(tzinfo=None) if now.tzinfo else now
                    compare_open = open_time.replace(tzinfo=None) if open_time.tzinfo else open_time
                    
                    if compare_open > compare_now:
                        return open_time
            
            return None
            
        except Exception as e:
            logger.warning(f"Error finding next open time: {e}")
            return None
    
    def _build_closed_message(
        self,
        condition_name: str,
        next_open: Optional[datetime]
    ) -> str:
        """Constrói mensagem de fechado."""
        base_msg = "Estamos fora do horário de atendimento."
        
        if next_open:
            # Formatar próxima abertura
            day_names = [
                "segunda-feira", "terça-feira", "quarta-feira",
                "quinta-feira", "sexta-feira", "sábado", "domingo"
            ]
            
            day_name = day_names[next_open.weekday()]
            time_str = next_open.strftime("%H:%M")
            
            # Verificar se é hoje
            today = date.today()
            if next_open.date() == today:
                return f"{base_msg} Retornaremos às {time_str}."
            elif (next_open.date() - today).days == 1:
                return f"{base_msg} Retornaremos amanhã às {time_str}."
            else:
                return f"{base_msg} Retornaremos na {day_name} às {time_str}."
        
        return base_msg
    
    def invalidate_cache(self, domain_uuid: Optional[str] = None):
        """Invalida cache."""
        if domain_uuid:
            keys_to_remove = [
                key for key in self._cache.keys()
                if key.startswith(f"{domain_uuid}:")
            ]
            for key in keys_to_remove:
                del self._cache[key]
        else:
            self._cache.clear()
        
        logger.info(f"Time condition cache invalidated: domain_uuid={domain_uuid or 'all'}")


# Singleton
_checker_instance: Optional[TimeConditionChecker] = None


def get_time_condition_checker() -> TimeConditionChecker:
    """Retorna instância singleton do checker."""
    global _checker_instance
    if _checker_instance is None:
        _checker_instance = TimeConditionChecker()
    return _checker_instance


# =============================================================================
# Função helper para uso simples
# =============================================================================

async def is_within_business_hours(
    domain_uuid: str,
    time_condition_uuid: Optional[str]
) -> Tuple[bool, str]:
    """
    Verifica se está dentro do horário comercial.
    
    Helper function para uso simples.
    
    Args:
        domain_uuid: UUID do domínio
        time_condition_uuid: UUID da time condition (None = sempre aberto)
    
    Returns:
        Tuple (is_open: bool, message: str)
    """
    checker = get_time_condition_checker()
    result = await checker.check(domain_uuid, time_condition_uuid)
    return (result.is_open, result.message)
