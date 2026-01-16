"""
Testes unitários para API de Callback.

FASE 4: Click-to-Call via Proxy
Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))


class TestCallbackAPIModels:
    """Testes para modelos da API de callback."""
    
    def test_originate_request_model(self):
        """Modelo de request de originate."""
        from api.callback import OriginateRequest
        
        request = OriginateRequest(
            domain_uuid="test-domain",
            extension="1001",
            client_number="5518997751073",
            ticket_id=123,
            callback_reason="Retorno de orçamento"
        )
        
        assert request.domain_uuid == "test-domain"
        assert request.extension == "1001"
        assert request.client_number == "5518997751073"
        assert request.ticket_id == 123
        assert request.call_timeout == 30  # default
        assert request.record is True  # default
    
    def test_originate_request_minimal(self):
        """Request mínimo (campos obrigatórios)."""
        from api.callback import OriginateRequest
        
        request = OriginateRequest(
            domain_uuid="test-domain",
            extension="1001",
            client_number="5518997751073"
        )
        
        assert request.ticket_id is None
        assert request.callback_reason is None
        assert request.caller_id_name == "Callback"
    
    def test_check_availability_request(self):
        """Modelo de request de disponibilidade."""
        from api.callback import CheckAvailabilityRequest
        
        request = CheckAvailabilityRequest(
            domain_uuid="test-domain",
            extension="1001"
        )
        
        assert request.domain_uuid == "test-domain"
        assert request.extension == "1001"
    
    def test_originate_response_success(self):
        """Response de sucesso."""
        from api.callback import OriginateResponse, OriginateStatus
        
        response = OriginateResponse(
            success=True,
            call_uuid="uuid-123",
            status=OriginateStatus.INITIATED,
            message="Ligação iniciada"
        )
        
        assert response.success is True
        assert response.call_uuid == "uuid-123"
        assert response.status == OriginateStatus.INITIATED
        assert response.error is None
    
    def test_originate_response_failure(self):
        """Response de falha."""
        from api.callback import OriginateResponse, OriginateStatus
        
        response = OriginateResponse(
            success=False,
            status=OriginateStatus.AGENT_BUSY,
            error="Ramal em chamada",
            message="Tente novamente em alguns segundos"
        )
        
        assert response.success is False
        assert response.status == OriginateStatus.AGENT_BUSY
        assert response.error == "Ramal em chamada"
    
    def test_check_availability_response_available(self):
        """Ramal disponível."""
        from api.callback import CheckAvailabilityResponse, ExtensionStatus
        
        response = CheckAvailabilityResponse(
            extension="1001",
            status=ExtensionStatus.AVAILABLE,
            available=True,
            reason=None
        )
        
        assert response.available is True
        assert response.status == ExtensionStatus.AVAILABLE
    
    def test_check_availability_response_busy(self):
        """Ramal ocupado."""
        from api.callback import CheckAvailabilityResponse, ExtensionStatus
        
        response = CheckAvailabilityResponse(
            extension="1001",
            status=ExtensionStatus.IN_CALL,
            available=False,
            reason="Em chamada ativa"
        )
        
        assert response.available is False
        assert response.status == ExtensionStatus.IN_CALL
        assert response.reason == "Em chamada ativa"


class TestOriginateStatus:
    """Testes para enum OriginateStatus."""
    
    def test_status_values(self):
        """Verificar valores do enum."""
        from api.callback import OriginateStatus
        
        assert OriginateStatus.INITIATED.value == "initiated"
        assert OriginateStatus.RINGING_AGENT.value == "ringing_agent"
        assert OriginateStatus.AGENT_ANSWERED.value == "agent_answered"
        assert OriginateStatus.RINGING_CLIENT.value == "ringing_client"
        assert OriginateStatus.CONNECTED.value == "connected"
        assert OriginateStatus.COMPLETED.value == "completed"
        assert OriginateStatus.FAILED.value == "failed"
        assert OriginateStatus.AGENT_BUSY.value == "agent_busy"
        assert OriginateStatus.AGENT_NO_ANSWER.value == "agent_no_answer"
        assert OriginateStatus.CLIENT_NO_ANSWER.value == "client_no_answer"
        assert OriginateStatus.CANCELLED.value == "cancelled"


class TestExtensionStatus:
    """Testes para enum ExtensionStatus."""
    
    def test_status_values(self):
        """Verificar valores do enum."""
        from api.callback import ExtensionStatus
        
        assert ExtensionStatus.AVAILABLE.value == "available"
        assert ExtensionStatus.IN_CALL.value == "in_call"
        assert ExtensionStatus.RINGING.value == "ringing"
        assert ExtensionStatus.DND.value == "dnd"
        assert ExtensionStatus.OFFLINE.value == "offline"
        assert ExtensionStatus.UNKNOWN.value == "unknown"


class TestCallbackAPIHelpers:
    """Testes para funções auxiliares da API."""
    
    @pytest.mark.asyncio
    async def test_check_extension_registered_success(self):
        """Ramal registrado com sucesso."""
        from api.callback import check_extension_registered
        
        mock_esl = AsyncMock()
        mock_esl.execute_api.return_value = "1001@domain REGISTERED\n"
        
        result = await check_extension_registered(mock_esl, "1001", "test-domain")
        
        assert result is True
        mock_esl.execute_api.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_check_extension_registered_not_found(self):
        """Ramal não registrado."""
        from api.callback import check_extension_registered
        
        mock_esl = AsyncMock()
        mock_esl.execute_api.return_value = "NOT FOUND"
        
        result = await check_extension_registered(mock_esl, "1001", "test-domain")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_check_extension_registered_error(self):
        """Erro ao verificar registro."""
        from api.callback import check_extension_registered
        
        mock_esl = AsyncMock()
        mock_esl.execute_api.side_effect = Exception("Connection failed")
        
        result = await check_extension_registered(mock_esl, "1001", "test-domain")
        
        # Em caso de erro, assume não registrado
        assert result is False
    
    @pytest.mark.asyncio
    async def test_check_extension_in_call_true(self):
        """Ramal em chamada."""
        from api.callback import check_extension_in_call
        
        mock_esl = AsyncMock()
        mock_esl.execute_api.return_value = "1001,uuid-123,ACTIVE\n"
        
        result = await check_extension_in_call(mock_esl, "1001")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_check_extension_in_call_false(self):
        """Ramal livre."""
        from api.callback import check_extension_in_call
        
        mock_esl = AsyncMock()
        mock_esl.execute_api.return_value = "9999,uuid-456,ACTIVE\n"  # Outro ramal
        
        result = await check_extension_in_call(mock_esl, "1001")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_check_extension_dnd_default(self):
        """DND não implementado deve retornar False."""
        from api.callback import check_extension_dnd
        
        result = await check_extension_dnd("1001", "test-domain")
        
        # Atualmente retorna False (TODO no código)
        assert result is False
