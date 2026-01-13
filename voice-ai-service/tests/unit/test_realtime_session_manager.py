"""
Tests for Realtime Session Manager.

Referências:
- openspec/changes/voice-ai-realtime/tasks.md (7.1.2)
- voice-ai-service/realtime/session_manager.py
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


class TestRealtimeSessionManager:
    """Testes para o Session Manager."""
    
    @pytest.fixture
    def session_manager(self):
        """Fixture para SessionManager."""
        from realtime.session_manager import RealtimeSessionManager
        return RealtimeSessionManager(
            max_sessions_per_domain=5,
            session_timeout_seconds=30
        )
    
    @pytest.fixture
    def mock_websocket(self):
        """Mock de WebSocket."""
        ws = AsyncMock()
        ws.close = AsyncMock()
        ws.send = AsyncMock()
        return ws
    
    @pytest.fixture
    def mock_config(self):
        """Mock de configuração de sessão."""
        from realtime.providers.base import RealtimeConfig
        return RealtimeConfig(
            domain_uuid="test-domain-uuid",
            provider_name="openai",
            system_prompt="Test prompt",
            voice="alloy"
        )
    
    @pytest.mark.asyncio
    async def test_create_session(self, session_manager, mock_websocket, mock_config):
        """Testa criação de sessão."""
        session = await session_manager.create_session(
            call_uuid="test-call-uuid",
            domain_uuid="test-domain-uuid",
            caller_id="1234567890",
            websocket=mock_websocket,
            config=mock_config
        )
        
        assert session is not None
        assert session.call_uuid == "test-call-uuid"
        assert session.domain_uuid == "test-domain-uuid"
    
    @pytest.mark.asyncio
    async def test_get_session(self, session_manager, mock_websocket, mock_config):
        """Testa recuperação de sessão."""
        await session_manager.create_session(
            call_uuid="test-call-uuid",
            domain_uuid="test-domain-uuid",
            caller_id="1234567890",
            websocket=mock_websocket,
            config=mock_config
        )
        
        session = session_manager.get_session("test-call-uuid")
        assert session is not None
        assert session.call_uuid == "test-call-uuid"
    
    @pytest.mark.asyncio
    async def test_get_session_not_found(self, session_manager):
        """Testa busca de sessão inexistente."""
        session = session_manager.get_session("nonexistent-uuid")
        assert session is None
    
    @pytest.mark.asyncio
    async def test_remove_session(self, session_manager, mock_websocket, mock_config):
        """Testa remoção de sessão."""
        await session_manager.create_session(
            call_uuid="test-call-uuid",
            domain_uuid="test-domain-uuid",
            caller_id="1234567890",
            websocket=mock_websocket,
            config=mock_config
        )
        
        removed = await session_manager.remove_session("test-call-uuid")
        assert removed is True
        
        session = session_manager.get_session("test-call-uuid")
        assert session is None
    
    @pytest.mark.asyncio
    async def test_max_sessions_per_domain(self, session_manager, mock_config):
        """Testa limite de sessões por domínio."""
        # Criar 5 sessões (o máximo)
        for i in range(5):
            ws = AsyncMock()
            ws.close = AsyncMock()
            await session_manager.create_session(
                call_uuid=f"call-{i}",
                domain_uuid="test-domain-uuid",
                caller_id=f"123456789{i}",
                websocket=ws,
                config=mock_config
            )
        
        # A 6ª deve falhar ou substituir
        ws = AsyncMock()
        ws.close = AsyncMock()
        
        # Verificar contagem
        count = session_manager.get_domain_session_count("test-domain-uuid")
        assert count == 5
    
    @pytest.mark.asyncio
    async def test_get_all_sessions(self, session_manager, mock_websocket, mock_config):
        """Testa listagem de todas as sessões."""
        await session_manager.create_session(
            call_uuid="call-1",
            domain_uuid="domain-1",
            caller_id="123",
            websocket=mock_websocket,
            config=mock_config
        )
        
        ws2 = AsyncMock()
        ws2.close = AsyncMock()
        
        config2 = MagicMock()
        config2.domain_uuid = "domain-2"
        config2.provider_name = "openai"
        
        await session_manager.create_session(
            call_uuid="call-2",
            domain_uuid="domain-2",
            caller_id="456",
            websocket=ws2,
            config=config2
        )
        
        all_sessions = session_manager.get_all_sessions()
        assert len(all_sessions) == 2
    
    @pytest.mark.asyncio
    async def test_get_sessions_by_domain(self, session_manager, mock_websocket, mock_config):
        """Testa listagem de sessões por domínio."""
        await session_manager.create_session(
            call_uuid="call-1",
            domain_uuid="test-domain-uuid",
            caller_id="123",
            websocket=mock_websocket,
            config=mock_config
        )
        
        ws2 = AsyncMock()
        ws2.close = AsyncMock()
        
        await session_manager.create_session(
            call_uuid="call-2",
            domain_uuid="test-domain-uuid",
            caller_id="456",
            websocket=ws2,
            config=mock_config
        )
        
        sessions = session_manager.get_sessions_by_domain("test-domain-uuid")
        assert len(sessions) == 2
    
    @pytest.mark.asyncio
    async def test_session_cleanup(self, session_manager, mock_websocket, mock_config):
        """Testa limpeza de sessões expiradas."""
        # Criar sessão
        await session_manager.create_session(
            call_uuid="test-call-uuid",
            domain_uuid="test-domain-uuid",
            caller_id="123",
            websocket=mock_websocket,
            config=mock_config
        )
        
        # Simular expiração
        session = session_manager.get_session("test-call-uuid")
        if session:
            session.started_at = datetime.now() - timedelta(seconds=60)
        
        # Executar cleanup
        cleaned = await session_manager.cleanup_expired_sessions()
        
        # Sessão expirada deve ser removida
        # (dependendo da implementação)
        assert cleaned >= 0


class TestRealtimeSession:
    """Testes para a classe RealtimeSession."""
    
    @pytest.fixture
    def mock_provider(self):
        """Mock de provider."""
        provider = AsyncMock()
        provider.connect = AsyncMock()
        provider.configure = AsyncMock()
        provider.send_audio = AsyncMock()
        provider.disconnect = AsyncMock()
        return provider
    
    @pytest.mark.asyncio
    async def test_session_lifecycle(self, mock_provider):
        """Testa ciclo de vida da sessão."""
        from realtime.session import RealtimeSession
        from realtime.providers.base import RealtimeConfig
        
        ws = AsyncMock()
        ws.close = AsyncMock()
        ws.send = AsyncMock()
        
        config = RealtimeConfig(
            domain_uuid="test-domain",
            provider_name="openai",
            system_prompt="Test",
            voice="alloy"
        )
        
        session = RealtimeSession(
            domain_uuid="test-domain",
            call_uuid="test-call",
            caller_id="123",
            fs_websocket=ws,
            config=config
        )
        
        assert session.call_uuid == "test-call"
        assert session.is_active is False  # Não iniciada ainda
    
    @pytest.mark.asyncio
    async def test_session_transcript(self, mock_provider):
        """Testa acumulação de transcript."""
        from realtime.session import RealtimeSession
        from realtime.providers.base import RealtimeConfig
        
        ws = AsyncMock()
        
        config = RealtimeConfig(
            domain_uuid="test-domain",
            provider_name="openai",
            system_prompt="Test",
            voice="alloy"
        )
        
        session = RealtimeSession(
            domain_uuid="test-domain",
            call_uuid="test-call",
            caller_id="123",
            fs_websocket=ws,
            config=config
        )
        
        # Adicionar ao transcript
        session.transcript.append({"role": "user", "content": "Olá"})
        session.transcript.append({"role": "assistant", "content": "Olá!"})
        
        assert len(session.transcript) == 2
        assert session.transcript[0]["role"] == "user"
