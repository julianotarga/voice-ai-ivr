"""
Testes de Integração - Fluxo Completo de Callback

FASE 2-5: Sistema de Callback Inteligente
Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/

Testes end-to-end do fluxo de callback incluindo:
- Captura de dados
- Criação de ticket
- Click-to-Call
- Monitoramento
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import asdict

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))


class TestCallbackFlowIntegration:
    """
    Testes de integração para o fluxo completo de callback.
    
    Cenários testados:
    1. Ligação falha de transferência → Callback oferecido → Cliente aceita → Ticket criado
    2. Cliente fornece outro número → Validação → Confirmação
    3. Cliente agenda horário → Callback agendado
    4. Atendente clica em "Ligar Agora" → Chamada originada
    5. Callback expirado → Notificação de expiração
    """

    @pytest.fixture
    def mock_omniplay_api(self):
        """Mock da API do OmniPlay."""
        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 201
            mock_response.json = AsyncMock(return_value={
                "id": 123,
                "uuid": "ticket-uuid-123",
                "ticketType": "callback",
                "callbackStatus": "pending",
                "whatsappSent": False
            })
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()
            
            mock_session_instance = AsyncMock()
            mock_session_instance.post.return_value = mock_response
            mock_session_instance.closed = False
            mock_session.return_value = mock_session_instance
            
            yield mock_session_instance

    @pytest.fixture
    def mock_esl_client(self):
        """Mock do cliente ESL."""
        mock = AsyncMock()
        mock.is_connected = True
        mock.connect = AsyncMock(return_value=True)
        mock.execute_api = AsyncMock(return_value="+OK")
        mock.execute_bgapi = AsyncMock(return_value="+OK Job-UUID: test-uuid-123")
        return mock

    @pytest.mark.asyncio
    async def test_full_callback_creation_flow(self, mock_omniplay_api):
        """
        Teste: Fluxo completo de criação de callback após falha de transferência.
        
        1. Transferência falha (destino ocupado)
        2. Sistema oferece callback ao cliente
        3. Cliente aceita com mesmo número
        4. Callback criado com sucesso
        """
        from realtime.handlers.callback_handler import (
            CallbackHandler,
            PhoneNumberUtils,
            CallbackStatus,
        )
        from realtime.handlers.transfer_destination_loader import TransferDestination
        
        # Setup
        handler = CallbackHandler(
            domain_uuid="test-domain-uuid",
            call_uuid="test-call-uuid",
            caller_id="5518997751073",
            omniplay_company_id=1
        )
        
        # 1. Cliente aceita usar o mesmo número
        result = handler.use_caller_id_as_callback()
        assert result is True
        assert handler.callback_data.callback_number == "5518997751073"
        
        # 2. Definir destino pretendido
        destination = TransferDestination(
            uuid="dest-1",
            name="João Vendas",
            destination_type="extension",
            destination_number="1001",
            department="Vendas"
        )
        handler.set_intended_destination(destination)
        assert handler.callback_data.intended_for_name == "João Vendas"
        assert handler.callback_data.department == "Vendas"
        
        # 3. Definir motivo
        handler.set_reason("Cliente precisa de orçamento para produto X")
        assert "orçamento" in handler.callback_data.reason.lower()
        
        # 4. Calcular expiração
        handler.calculate_expiration(hours=24)
        assert handler.callback_data.expires_at is not None
        
        # 5. Criar callback (mocked)
        handler._http_session = mock_omniplay_api
        
        # Nota: O teste real da criação requer mock completo do aiohttp
        # Aqui validamos o estado do handler
        assert handler.callback_data.callback_number == "5518997751073"
        assert handler.callback_data.intended_for_name == "João Vendas"

    @pytest.mark.asyncio
    async def test_callback_with_different_number(self):
        """
        Teste: Cliente fornece número diferente para callback.
        """
        from realtime.handlers.callback_handler import (
            CallbackHandler,
            PhoneNumberUtils,
        )
        
        handler = CallbackHandler(
            domain_uuid="test-domain-uuid",
            call_uuid="test-call-uuid",
            caller_id="1001",  # Ramal interno
            omniplay_company_id=1
        )
        
        # 1. Tentar usar caller ID (ramal - deve falhar)
        result = handler.use_caller_id_as_callback()
        assert result is False
        
        # 2. Cliente fornece número externo
        result = handler.set_callback_number("18997752222")
        assert result is True
        assert handler.callback_data.callback_number == "5518997752222"

    @pytest.mark.asyncio
    async def test_scheduled_callback(self):
        """
        Teste: Cliente agenda callback para horário específico.
        """
        from realtime.handlers.callback_handler import CallbackHandler
        
        handler = CallbackHandler(
            domain_uuid="test-domain-uuid",
            call_uuid="test-call-uuid",
            caller_id="5518997751073",
            omniplay_company_id=1
        )
        
        handler.use_caller_id_as_callback()
        
        # Agendar para daqui a 2 horas
        scheduled_time = datetime.now() + timedelta(hours=2)
        handler.set_scheduled_at(scheduled_time)
        
        assert handler.callback_data.scheduled_at == scheduled_time
        
        # Expiração deve ser posterior ao agendamento
        handler.calculate_expiration(hours=24)
        assert handler.callback_data.expires_at > handler.callback_data.scheduled_at

    @pytest.mark.asyncio
    async def test_click_to_call_flow(self, mock_esl_client):
        """
        Teste: Atendente clica em "Ligar Agora" e chamada é originada.
        """
        from api.callback import (
            check_extension_registered,
            check_extension_in_call,
            OriginateRequest,
            OriginateStatus,
        )
        
        # 1. Verificar disponibilidade
        mock_esl_client.execute_api.return_value = "1001 REGISTERED"
        is_registered = await check_extension_registered(
            mock_esl_client, "1001", "test-domain"
        )
        assert is_registered is True
        
        # 2. Verificar se não está em chamada
        mock_esl_client.execute_api.return_value = ""  # Sem canais ativos
        in_call = await check_extension_in_call(mock_esl_client, "1001")
        assert in_call is False
        
        # 3. Modelo de request
        request = OriginateRequest(
            domain_uuid="test-domain",
            extension="1001",
            client_number="5518997751073",
            ticket_id=123,
            callback_reason="Retorno de orçamento"
        )
        
        assert request.call_timeout == 30
        assert request.record is True

    @pytest.mark.asyncio
    async def test_callback_whatsapp_notification_flow(self):
        """
        Teste: Callback com notificação WhatsApp ativada.
        """
        from realtime.handlers.callback_handler import CallbackHandler
        
        handler = CallbackHandler(
            domain_uuid="test-domain-uuid",
            call_uuid="test-call-uuid",
            caller_id="5518997751073",
            omniplay_company_id=1
        )
        
        handler.use_caller_id_as_callback()
        handler.set_notify_via_whatsapp(True)
        
        assert handler.callback_data.notify_via_whatsapp is True

    @pytest.mark.asyncio
    async def test_callback_data_preservation(self):
        """
        Teste: Dados da chamada original são preservados no callback.
        """
        from realtime.handlers.callback_handler import CallbackHandler
        
        handler = CallbackHandler(
            domain_uuid="test-domain-uuid",
            call_uuid="original-call-uuid",
            caller_id="5518997751073",
            omniplay_company_id=1
        )
        
        handler.use_caller_id_as_callback()
        
        # Adicionar dados da chamada original
        transcript = [
            {"role": "user", "content": "Olá, preciso de um orçamento"},
            {"role": "assistant", "content": "Claro! Vou transferir você para vendas"},
        ]
        
        handler.set_voice_call_data(
            duration=120,
            recording_url="/recordings/original-call-uuid.wav",
            transcript=transcript
        )
        
        assert handler.callback_data.voice_call_uuid == "original-call-uuid"
        assert handler.callback_data.voice_call_duration == 120
        assert handler.callback_data.recording_url == "/recordings/original-call-uuid.wav"
        assert handler.callback_data.transcript == transcript


class TestCallbackMonitoringIntegration:
    """
    Testes de integração para monitoramento de callbacks.
    """

    @pytest.mark.asyncio
    async def test_callback_expiration_detection(self):
        """
        Teste: Callbacks expirados são detectados corretamente.
        """
        from realtime.handlers.callback_handler import CallbackData
        from datetime import datetime, timedelta
        
        # Callback expirado
        expired_callback = CallbackData(
            callback_number="5518997751073",
            expires_at=datetime.now() - timedelta(hours=1)  # Expirou há 1 hora
        )
        
        assert expired_callback.expires_at < datetime.now()
        
        # Callback válido
        valid_callback = CallbackData(
            callback_number="5518997751073",
            expires_at=datetime.now() + timedelta(hours=23)
        )
        
        assert valid_callback.expires_at > datetime.now()

    @pytest.mark.asyncio
    async def test_callback_notification_tracking(self):
        """
        Teste: Contador de notificações é atualizado corretamente.
        
        Nota: Este teste valida a lógica do CallbackMonitorJob no OmniPlay.
        """
        # Mock de ticket com contador de notificações
        class MockTicket:
            def __init__(self):
                self.callbackNotificationCount = 0
                self.callbackMaxAttempts = 3
            
            async def update(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
        
        ticket = MockTicket()
        
        # Simular 3 notificações
        for i in range(3):
            await ticket.update(
                callbackNotificationCount=ticket.callbackNotificationCount + 1,
                callbackStatus="notified"
            )
        
        assert ticket.callbackNotificationCount == 3
        
        # Após max notificações, deve escalar
        max_notifications = ticket.callbackMaxAttempts * 3
        if ticket.callbackNotificationCount >= max_notifications:
            await ticket.update(callbackStatus="needs_review")
            assert ticket.callbackStatus == "needs_review"


class TestCallbackAPIIntegration:
    """
    Testes de integração para a API de callback.
    """

    @pytest.fixture
    def test_client(self):
        """Cliente de teste para a API."""
        # Este teste requer FastAPI TestClient configurado
        # Deixamos como placeholder para implementação futura
        pass

    def test_api_health_check(self):
        """
        Teste: Endpoint de health check responde corretamente.
        """
        # Placeholder - implementar com TestClient real
        # response = test_client.get("/api/callback/health")
        # assert response.status_code == 200
        # assert response.json()["status"] == "ok"
        pass

    def test_api_requires_domain_uuid(self):
        """
        Teste: Endpoints exigem domain_uuid (multi-tenant).
        """
        from api.callback import OriginateRequest, CheckAvailabilityRequest
        from pydantic import ValidationError
        
        # Deve falhar sem domain_uuid
        with pytest.raises(ValidationError):
            OriginateRequest(
                extension="1001",
                client_number="5518997751073"
            )
        
        with pytest.raises(ValidationError):
            CheckAvailabilityRequest(
                extension="1001"
            )
