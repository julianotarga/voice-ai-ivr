"""
Tests for Transfer Handler.

Referências:
- openspec/changes/voice-ai-realtime/tasks.md (7.1.4)
- voice-ai-service/realtime/handlers/transfer.py
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTransferHandler:
    """Testes para o Transfer Handler."""
    
    @pytest.fixture
    def transfer_handler(self):
        """Fixture para TransferHandler."""
        from realtime.handlers.transfer import TransferHandler
        return TransferHandler()
    
    @pytest.mark.asyncio
    async def test_resolve_extension(self, transfer_handler):
        """Testa resolução de extensão."""
        destination = await transfer_handler.resolve_destination(
            domain_uuid="test-domain",
            destination="200",
            destination_type="extension"
        )
        
        assert destination is not None
        assert destination.value == "200"
        assert destination.dialplan_extension == "200"
    
    @pytest.mark.asyncio
    async def test_resolve_department(self, transfer_handler):
        """Testa resolução de departamento."""
        destination = await transfer_handler.resolve_destination(
            domain_uuid="test-domain",
            destination="vendas",
            destination_type="department"
        )
        
        assert destination is not None
        assert destination.value == "vendas"
        assert destination.dialplan_extension == "200"  # Mapeamento padrão
    
    @pytest.mark.asyncio
    async def test_resolve_department_not_found(self, transfer_handler):
        """Testa departamento não encontrado."""
        destination = await transfer_handler.resolve_destination(
            domain_uuid="test-domain",
            destination="departamento_inexistente",
            destination_type="department"
        )
        
        assert destination is None
    
    @pytest.mark.asyncio
    async def test_resolve_queue(self, transfer_handler):
        """Testa resolução de fila."""
        destination = await transfer_handler.resolve_destination(
            domain_uuid="test-domain",
            destination="support",
            destination_type="queue"
        )
        
        assert destination is not None
        assert destination.dialplan_extension == "queue_support"
    
    @pytest.mark.asyncio
    async def test_resolve_voicemail(self, transfer_handler):
        """Testa resolução de caixa postal."""
        destination = await transfer_handler.resolve_destination(
            domain_uuid="test-domain",
            destination="200",
            destination_type="voicemail"
        )
        
        assert destination is not None
        assert destination.dialplan_extension == "*99200"
    
    @pytest.mark.asyncio
    async def test_resolve_external(self, transfer_handler):
        """Testa resolução de número externo."""
        destination = await transfer_handler.resolve_destination(
            domain_uuid="test-domain",
            destination="5511999999999",
            destination_type="external"
        )
        
        assert destination is not None
        assert destination.dialplan_extension == "5511999999999"
        assert destination.context == "external"
    
    def test_infer_destination_type_extension(self, transfer_handler):
        """Testa inferência de tipo - extensão."""
        dtype = transfer_handler._infer_destination_type("200")
        assert dtype == "extension"
    
    def test_infer_destination_type_external(self, transfer_handler):
        """Testa inferência de tipo - externo."""
        dtype = transfer_handler._infer_destination_type("5511999999999")
        assert dtype == "external"
    
    def test_infer_destination_type_queue(self, transfer_handler):
        """Testa inferência de tipo - fila."""
        dtype = transfer_handler._infer_destination_type("queue_support")
        assert dtype == "queue"
    
    def test_infer_destination_type_voicemail(self, transfer_handler):
        """Testa inferência de tipo - voicemail."""
        dtype = transfer_handler._infer_destination_type("*99200")
        assert dtype == "voicemail"
    
    def test_infer_destination_type_department(self, transfer_handler):
        """Testa inferência de tipo - departamento (default)."""
        dtype = transfer_handler._infer_destination_type("vendas")
        assert dtype == "department"
    
    @pytest.mark.asyncio
    async def test_transfer_call_success(self, transfer_handler):
        """Testa transferência bem-sucedida."""
        with patch.object(
            transfer_handler.esl,
            'uuid_transfer',
            new_callable=AsyncMock,
            return_value=True
        ):
            result = await transfer_handler.transfer_call(
                call_uuid="test-call-uuid",
                domain_uuid="test-domain",
                destination="200"
            )
            
            assert result.success is True
            assert result.destination is not None
    
    @pytest.mark.asyncio
    async def test_transfer_call_failure(self, transfer_handler):
        """Testa transferência falha."""
        with patch.object(
            transfer_handler.esl,
            'uuid_transfer',
            new_callable=AsyncMock,
            return_value=False
        ):
            result = await transfer_handler.transfer_call(
                call_uuid="test-call-uuid",
                domain_uuid="test-domain",
                destination="200"
            )
            
            assert result.success is False
    
    @pytest.mark.asyncio
    async def test_transfer_call_destination_not_found(self, transfer_handler):
        """Testa transferência com destino não encontrado."""
        result = await transfer_handler.transfer_call(
            call_uuid="test-call-uuid",
            domain_uuid="test-domain",
            destination="departamento_inexistente"
        )
        
        assert result.success is False
        assert "Could not resolve" in result.error
    
    def test_transfer_log(self, transfer_handler):
        """Testa log de transferências."""
        from realtime.handlers.transfer import TransferResult, TransferDestination, DestinationType
        
        destination = TransferDestination(
            type=DestinationType.EXTENSION,
            value="200",
            display_name="Ramal 200",
            dialplan_extension="200"
        )
        
        result = TransferResult(
            success=True,
            destination=destination
        )
        
        transfer_handler._log_transfer(
            call_uuid="test-call",
            domain_uuid="test-domain",
            result=result
        )
        
        logs = transfer_handler.get_transfer_log("test-domain")
        assert len(logs) == 1
        assert logs[0]["success"] is True


class TestESLClient:
    """Testes para o cliente ESL."""
    
    @pytest.fixture
    def esl_client(self):
        """Fixture para ESLClient."""
        from realtime.handlers.transfer import ESLClient
        return ESLClient(host="127.0.0.1", port=8021)
    
    @pytest.mark.asyncio
    async def test_esl_uuid_transfer_command(self, esl_client):
        """Testa comando de transferência."""
        with patch.object(
            esl_client,
            'execute',
            new_callable=AsyncMock,
            return_value="+OK Success"
        ):
            success = await esl_client.uuid_transfer(
                uuid="test-uuid",
                destination="200",
                context="default"
            )
            
            assert success is True
    
    @pytest.mark.asyncio
    async def test_esl_uuid_broadcast_command(self, esl_client):
        """Testa comando de broadcast."""
        with patch.object(
            esl_client,
            'execute',
            new_callable=AsyncMock,
            return_value="+OK Success"
        ):
            success = await esl_client.uuid_broadcast(
                uuid="test-uuid",
                audio_file="/tmp/test.wav"
            )
            
            assert success is True
