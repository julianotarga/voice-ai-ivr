"""
Testes unitários para TimeConditionChecker.

Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/tasks.md
"""

import pytest
from datetime import datetime, time, date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Import pytz com fallback
try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    pytz = None  # type: ignore
    PYTZ_AVAILABLE = False

from realtime.handlers.time_condition_checker import (
    TimeConditionChecker,
    TimeConditionStatus,
    TimeConditionResult,
    TimeConditionConfig,
    DaySchedule,
    TimeSlot,
    get_time_condition_checker,
    is_within_business_hours,
    PYTZ_AVAILABLE as CHECKER_PYTZ_AVAILABLE,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_schedule():
    """Schedule comercial padrão: Seg-Sex 08:00-12:00, 13:00-18:00."""
    schedule = {}
    for day in range(5):  # 0=Monday to 4=Friday
        schedule[day] = DaySchedule(
            day=day,
            slots=[
                TimeSlot(time(8, 0), time(12, 0)),
                TimeSlot(time(13, 0), time(18, 0)),
            ]
        )
    return schedule


@pytest.fixture
def sample_config(sample_schedule):
    """Configuração de exemplo."""
    return TimeConditionConfig(
        uuid="test-uuid-1234",
        name="Horário Comercial",
        domain_uuid="domain-uuid-5678",
        timezone="America/Sao_Paulo",
        schedule=sample_schedule,
        holidays=[],
        is_enabled=True,
    )


# =============================================================================
# TimeSlot Tests
# =============================================================================

class TestTimeSlot:
    """Testes para TimeSlot."""
    
    def test_contains_within_slot(self):
        """Hora dentro do slot deve retornar True."""
        slot = TimeSlot(time(8, 0), time(12, 0))
        
        assert slot.contains(time(8, 0))  # início
        assert slot.contains(time(10, 30))  # meio
        assert slot.contains(time(12, 0))  # fim
    
    def test_contains_outside_slot(self):
        """Hora fora do slot deve retornar False."""
        slot = TimeSlot(time(8, 0), time(12, 0))
        
        assert not slot.contains(time(7, 59))  # antes
        assert not slot.contains(time(12, 1))  # depois
        assert not slot.contains(time(14, 0))  # muito depois
    
    def test_contains_overnight_slot(self):
        """Slot que cruza meia-noite (turno noturno)."""
        slot = TimeSlot(time(22, 0), time(6, 0))
        
        assert slot.contains(time(22, 0))  # início
        assert slot.contains(time(23, 30))  # noite
        assert slot.contains(time(0, 0))  # meia-noite
        assert slot.contains(time(3, 0))  # madrugada
        assert slot.contains(time(6, 0))  # fim
        
        assert not slot.contains(time(10, 0))  # manhã
        assert not slot.contains(time(15, 0))  # tarde


# =============================================================================
# DaySchedule Tests
# =============================================================================

class TestDaySchedule:
    """Testes para DaySchedule."""
    
    def test_is_open_with_multiple_slots(self):
        """Dia com múltiplos slots (manhã + tarde)."""
        schedule = DaySchedule(
            day=0,  # Segunda
            slots=[
                TimeSlot(time(8, 0), time(12, 0)),
                TimeSlot(time(13, 0), time(18, 0)),
            ]
        )
        
        # Dentro dos slots
        assert schedule.is_open(time(9, 0))  # manhã
        assert schedule.is_open(time(15, 0))  # tarde
        
        # Fora dos slots
        assert not schedule.is_open(time(7, 0))  # antes
        assert not schedule.is_open(time(12, 30))  # almoço
        assert not schedule.is_open(time(19, 0))  # depois
    
    def test_is_open_empty_slots(self):
        """Dia sem slots (fechado)."""
        schedule = DaySchedule(day=6, slots=[])  # Domingo
        
        assert not schedule.is_open(time(10, 0))
        assert not schedule.is_open(time(15, 0))


# =============================================================================
# TimeConditionChecker Tests
# =============================================================================

class TestTimeConditionChecker:
    """Testes para TimeConditionChecker."""
    
    @pytest.mark.asyncio
    async def test_check_no_condition(self):
        """Sem time_condition_uuid deve retornar sempre aberto."""
        checker = TimeConditionChecker()
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid=None,
        )
        
        assert result.status == TimeConditionStatus.NO_CONDITION
        assert result.is_open is True
        assert not result.should_create_ticket
    
    @pytest.mark.asyncio
    async def test_check_empty_uuid(self):
        """UUID vazio deve retornar sempre aberto."""
        checker = TimeConditionChecker()
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="",
        )
        
        assert result.status == TimeConditionStatus.NO_CONDITION
        assert result.is_open is True
    
    @pytest.mark.asyncio
    async def test_check_within_hours(self, sample_config):
        """Chamada dentro do horário comercial."""
        checker = TimeConditionChecker()
        
        # Mock _load_config para retornar nossa config de teste
        checker._load_config = AsyncMock(return_value=sample_config)
        
        # Segunda-feira 10:00 (dentro do horário)
        if PYTZ_AVAILABLE and pytz is not None:
            tz = pytz.timezone("America/Sao_Paulo")
            test_time = tz.localize(datetime(2026, 1, 19, 10, 0, 0))  # Segunda
        else:
            test_time = datetime(2026, 1, 19, 10, 0, 0)  # Segunda (sem timezone)
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="test-uuid",
            now=test_time,
        )
        
        assert result.status == TimeConditionStatus.OPEN
        assert result.is_open is True
        assert not result.should_create_ticket
        assert result.time_condition_name == "Horário Comercial"
    
    @pytest.mark.asyncio
    async def test_check_outside_hours_lunch(self, sample_config):
        """Chamada durante horário de almoço."""
        checker = TimeConditionChecker()
        checker._load_config = AsyncMock(return_value=sample_config)
        
        # Segunda-feira 12:30 (horário de almoço)
        if PYTZ_AVAILABLE and pytz is not None:
            tz = pytz.timezone("America/Sao_Paulo")
            test_time = tz.localize(datetime(2026, 1, 19, 12, 30, 0))
        else:
            test_time = datetime(2026, 1, 19, 12, 30, 0)
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="test-uuid",
            now=test_time,
        )
        
        assert result.status == TimeConditionStatus.CLOSED
        assert result.is_open is False
        assert result.should_create_ticket
    
    @pytest.mark.asyncio
    async def test_check_outside_hours_weekend(self, sample_config):
        """Chamada no fim de semana (Sábado)."""
        checker = TimeConditionChecker()
        checker._load_config = AsyncMock(return_value=sample_config)
        
        # Sábado 10:00 (não trabalha)
        if PYTZ_AVAILABLE and pytz is not None:
            tz = pytz.timezone("America/Sao_Paulo")
            test_time = tz.localize(datetime(2026, 1, 17, 10, 0, 0))  # Sábado
        else:
            test_time = datetime(2026, 1, 17, 10, 0, 0)  # Sábado
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="test-uuid",
            now=test_time,
        )
        
        assert result.status == TimeConditionStatus.CLOSED
        assert result.is_open is False
        assert result.should_create_ticket
    
    @pytest.mark.asyncio
    async def test_check_outside_hours_evening(self, sample_config):
        """Chamada à noite após expediente."""
        checker = TimeConditionChecker()
        checker._load_config = AsyncMock(return_value=sample_config)
        
        # Terça-feira 20:00 (após expediente)
        if PYTZ_AVAILABLE and pytz is not None:
            tz = pytz.timezone("America/Sao_Paulo")
            test_time = tz.localize(datetime(2026, 1, 20, 20, 0, 0))
        else:
            test_time = datetime(2026, 1, 20, 20, 0, 0)
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="test-uuid",
            now=test_time,
        )
        
        assert result.status == TimeConditionStatus.CLOSED
        assert result.is_open is False
        assert "fora do horário" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_check_holiday(self, sample_config):
        """Chamada em feriado."""
        # Adicionar feriado
        if PYTZ_AVAILABLE and pytz is not None:
            tz = pytz.timezone("America/Sao_Paulo")
            holiday = tz.localize(datetime(2026, 1, 19, 0, 0, 0))  # Segunda
            test_time = tz.localize(datetime(2026, 1, 19, 10, 0, 0))
        else:
            holiday = datetime(2026, 1, 19, 0, 0, 0)  # Segunda
            test_time = datetime(2026, 1, 19, 10, 0, 0)
        
        sample_config.holidays.append(holiday)
        
        checker = TimeConditionChecker()
        checker._load_config = AsyncMock(return_value=sample_config)
        
        # Segunda-feira 10:00 (mas é feriado)
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="test-uuid",
            now=test_time,
        )
        
        assert result.status == TimeConditionStatus.HOLIDAY
        assert result.is_open is False
        assert result.should_create_ticket
    
    @pytest.mark.asyncio
    async def test_check_disabled_condition(self, sample_config):
        """Time condition desabilitada deve permitir."""
        sample_config.is_enabled = False
        
        checker = TimeConditionChecker()
        checker._load_config = AsyncMock(return_value=sample_config)
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="test-uuid",
        )
        
        assert result.status == TimeConditionStatus.NO_CONDITION
        assert result.is_open is True
    
    @pytest.mark.asyncio
    async def test_check_config_not_found(self):
        """Config não encontrada deve permitir (fail-open)."""
        checker = TimeConditionChecker()
        checker._load_config = AsyncMock(return_value=None)
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="non-existent-uuid",
        )
        
        assert result.status == TimeConditionStatus.NO_CONDITION
        assert result.is_open is True
    
    @pytest.mark.asyncio
    async def test_check_error_fallback(self):
        """Erro na verificação deve permitir (fail-open)."""
        checker = TimeConditionChecker()
        checker._load_config = AsyncMock(side_effect=Exception("DB Error"))
        
        result = await checker.check(
            domain_uuid="test-domain",
            time_condition_uuid="test-uuid",
        )
        
        assert result.status == TimeConditionStatus.ERROR
        assert result.is_open is True


# =============================================================================
# Integration Tests
# =============================================================================

class TestTimeConditionIntegration:
    """Testes de integração leves (sem banco real)."""
    
    def test_parse_schedule_comercial(self):
        """Parse de schedule comercial."""
        checker = TimeConditionChecker()
        
        schedule = checker._parse_schedule(
            param="",
            preset="",
            name="Horário Comercial"
        )
        
        # Deve ter seg-sex
        assert 0 in schedule  # Segunda
        assert 4 in schedule  # Sexta
        assert 5 not in schedule  # Sábado
        assert 6 not in schedule  # Domingo
    
    def test_parse_schedule_24h(self):
        """Parse de schedule 24 horas."""
        checker = TimeConditionChecker()
        
        schedule = checker._parse_schedule(
            param="",
            preset="always",
            name="Atendimento 24h"
        )
        
        # Deve ter todos os dias
        for day in range(7):
            assert day in schedule
            assert len(schedule[day].slots) > 0
    
    def test_build_closed_message_today(self):
        """Mensagem quando retorna hoje."""
        checker = TimeConditionChecker()
        
        # Next open hoje às 13:00
        from datetime import date
        today = datetime.combine(date.today(), time(13, 0))
        
        message = checker._build_closed_message(
            "Comercial",
            today
        )
        
        assert "13:00" in message
        assert "retornaremos" in message.lower()
    
    def test_cache_invalidation(self):
        """Teste de invalidação de cache."""
        import time as time_mod
        from realtime.handlers.time_condition_checker import CacheEntry
        
        checker = TimeConditionChecker()
        
        # Simular cache populado
        mock_config = MagicMock()
        checker._cache = {
            "domain1:uuid1": CacheEntry(mock_config, time_mod.time()),
            "domain1:uuid2": CacheEntry(mock_config, time_mod.time()),
            "domain2:uuid3": CacheEntry(mock_config, time_mod.time()),
        }
        
        # Invalidar apenas domain1
        checker.invalidate_cache("domain1")
        
        assert "domain1:uuid1" not in checker._cache
        assert "domain1:uuid2" not in checker._cache
        assert "domain2:uuid3" in checker._cache
        
        # Invalidar tudo
        checker.invalidate_cache()
        
        assert len(checker._cache) == 0


# =============================================================================
# Helper Function Tests
# =============================================================================

class TestHelperFunctions:
    """Testes para funções helper."""
    
    @pytest.mark.asyncio
    async def test_is_within_business_hours_helper(self):
        """Teste da função helper is_within_business_hours."""
        with patch.object(
            TimeConditionChecker, 'check',
            new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = TimeConditionResult(
                status=TimeConditionStatus.OPEN,
                is_open=True,
                message="Dentro do horário"
            )
            
            is_open, message = await is_within_business_hours(
                domain_uuid="test-domain",
                time_condition_uuid="test-uuid",
            )
            
            assert is_open is True
            assert "horário" in message.lower()
    
    def test_get_singleton_checker(self):
        """Singleton deve retornar mesma instância."""
        checker1 = get_time_condition_checker()
        checker2 = get_time_condition_checker()
        
        assert checker1 is checker2
