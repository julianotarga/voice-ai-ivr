"""
Testes unitários para CallbackHandler e utilitários relacionados.

FASE 2: Sistema de Callback
Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
"""

import pytest
from datetime import datetime, timedelta

# Importar classes do callback_handler
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from realtime.handlers.callback_handler import (
    PhoneNumberUtils,
    ResponseAnalyzer,
    CallbackHandler,
    CallbackData,
    CallbackResult,
    CallbackStatus,
)


class TestPhoneNumberUtils:
    """Testes para PhoneNumberUtils."""
    
    def test_normalize_brazilian_number_with_55(self):
        """Número já com código do país."""
        result = PhoneNumberUtils.normalize_brazilian_number("5518997751073")
        assert result == "5518997751073"
    
    def test_normalize_brazilian_number_without_55(self):
        """Número sem código do país."""
        result = PhoneNumberUtils.normalize_brazilian_number("18997751073")
        assert result == "5518997751073"
    
    def test_normalize_brazilian_number_landline(self):
        """Número fixo (10 dígitos)."""
        result = PhoneNumberUtils.normalize_brazilian_number("1832223344")
        assert result == "551832223344"
    
    def test_normalize_brazilian_number_invalid_short(self):
        """Número muito curto (inválido)."""
        result = PhoneNumberUtils.normalize_brazilian_number("99775")
        assert result == ""
    
    def test_normalize_brazilian_number_with_formatting(self):
        """Número com formatação."""
        result = PhoneNumberUtils.normalize_brazilian_number("(18) 99775-1073")
        assert result == "5518997751073"
    
    def test_validate_brazilian_number_valid_mobile(self):
        """Celular válido."""
        normalized, is_valid = PhoneNumberUtils.validate_brazilian_number("18997751073")
        assert is_valid is True
        assert normalized == "5518997751073"
    
    def test_validate_brazilian_number_valid_landline(self):
        """Fixo válido."""
        normalized, is_valid = PhoneNumberUtils.validate_brazilian_number("1832223344")
        assert is_valid is True
        assert normalized == "551832223344"
    
    def test_validate_brazilian_number_invalid_ddd(self):
        """DDD inválido (menor que 11)."""
        normalized, is_valid = PhoneNumberUtils.validate_brazilian_number("0997751073")
        assert is_valid is False
        assert normalized == ""
    
    def test_validate_brazilian_number_mobile_without_9(self):
        """Celular sem 9 inicial (inválido)."""
        normalized, is_valid = PhoneNumberUtils.validate_brazilian_number("18897751073")
        assert is_valid is False
        assert normalized == ""
    
    def test_is_internal_extension_short(self):
        """Ramal interno (4 dígitos)."""
        assert PhoneNumberUtils.is_internal_extension("1001") is True
    
    def test_is_internal_extension_very_short(self):
        """Ramal interno (2 dígitos)."""
        assert PhoneNumberUtils.is_internal_extension("10") is True
    
    def test_is_internal_extension_external(self):
        """Número externo."""
        assert PhoneNumberUtils.is_internal_extension("18997751073") is False
    
    def test_is_internal_extension_empty(self):
        """Número vazio."""
        assert PhoneNumberUtils.is_internal_extension("") is True
    
    def test_extract_phone_from_text_direct_digits(self):
        """Extrair número com dígitos diretos."""
        result = PhoneNumberUtils.extract_phone_from_text("meu número é 18997751073")
        assert result == "18997751073"
    
    def test_extract_phone_from_text_formatted(self):
        """Extrair número formatado."""
        result = PhoneNumberUtils.extract_phone_from_text("o telefone é 18 99775 1073")
        assert result == "18997751073"
    
    def test_extract_phone_from_text_words(self):
        """Extrair número por extenso."""
        result = PhoneNumberUtils.extract_phone_from_text("um oito nove nove sete sete cinco um zero sete três")
        assert result is not None
        assert "18997751073" in result or len(result) >= 10
    
    def test_extract_phone_from_text_no_number(self):
        """Texto sem número."""
        result = PhoneNumberUtils.extract_phone_from_text("não tenho número agora")
        assert result is None
    
    def test_format_for_speech_mobile(self):
        """Formatar celular para fala."""
        result = PhoneNumberUtils.format_for_speech("5518997751073")
        assert "18" in result
        assert "," in result  # Deve ter pausas
    
    def test_format_for_speech_landline(self):
        """Formatar fixo para fala."""
        result = PhoneNumberUtils.format_for_speech("551832223344")
        assert "18" in result
    
    def test_wants_same_number_true(self):
        """Cliente quer usar mesmo número."""
        assert PhoneNumberUtils.wants_same_number("pode ser esse mesmo número") is True
        assert PhoneNumberUtils.wants_same_number("esse atual tá bom") is True
    
    def test_wants_same_number_false(self):
        """Cliente quer outro número."""
        assert PhoneNumberUtils.wants_same_number("quero usar outro") is False


class TestResponseAnalyzer:
    """Testes para ResponseAnalyzer."""
    
    def test_is_affirmative_yes(self):
        """Respostas afirmativas básicas."""
        assert ResponseAnalyzer.is_affirmative("sim") is True
        assert ResponseAnalyzer.is_affirmative("Sim") is True
        assert ResponseAnalyzer.is_affirmative("SIM") is True
    
    def test_is_affirmative_variations(self):
        """Variações de afirmação."""
        assert ResponseAnalyzer.is_affirmative("isso") is True
        assert ResponseAnalyzer.is_affirmative("certo") is True
        assert ResponseAnalyzer.is_affirmative("pode") is True
        assert ResponseAnalyzer.is_affirmative("ok") is True
    
    def test_is_affirmative_short(self):
        """Respostas curtas devem ser consideradas afirmativas."""
        assert ResponseAnalyzer.is_affirmative("uh") is True  # < 5 chars
    
    def test_is_affirmative_with_negation(self):
        """Negação deve prevalecer."""
        assert ResponseAnalyzer.is_affirmative("não, obrigado") is False
        assert ResponseAnalyzer.is_affirmative("errado sim") is False  # "errado" é negativo
    
    def test_is_negative_basic(self):
        """Respostas negativas básicas."""
        assert ResponseAnalyzer.is_negative("não") is True
        assert ResponseAnalyzer.is_negative("nao") is True
        assert ResponseAnalyzer.is_negative("errado") is True
    
    def test_is_negative_false(self):
        """Respostas não negativas."""
        assert ResponseAnalyzer.is_negative("sim") is False
        assert ResponseAnalyzer.is_negative("ok") is False
    
    def test_wants_callback(self):
        """Cliente quer callback."""
        assert ResponseAnalyzer.wants_callback("pode me ligar de volta?") is True
        assert ResponseAnalyzer.wants_callback("retornar depois") is True
        assert ResponseAnalyzer.wants_callback("gostaria de um callback") is True
    
    def test_wants_callback_false(self):
        """Cliente não mencionou callback."""
        assert ResponseAnalyzer.wants_callback("quero falar com vendas") is False
    
    def test_wants_message(self):
        """Cliente quer deixar recado."""
        assert ResponseAnalyzer.wants_message("quero deixar um recado") is True
        assert ResponseAnalyzer.wants_message("pode anotar uma mensagem?") is True
    
    def test_wants_message_false(self):
        """Cliente não mencionou recado."""
        assert ResponseAnalyzer.wants_message("quero falar agora") is False


class TestCallbackData:
    """Testes para dataclass CallbackData."""
    
    def test_callback_data_defaults(self):
        """Valores default do CallbackData."""
        data = CallbackData(callback_number="5518997751073")
        assert data.callback_number == "5518997751073"
        assert data.callback_extension is None
        assert data.intended_for_name is None
        assert data.scheduled_at is None
        assert data.notify_via_whatsapp is False
    
    def test_callback_data_full(self):
        """CallbackData com todos os campos."""
        data = CallbackData(
            callback_number="5518997751073",
            intended_for_name="João Silva",
            department="Vendas",
            reason="Orçamento",
            scheduled_at=datetime.now() + timedelta(hours=2),
            notify_via_whatsapp=True
        )
        assert data.callback_number == "5518997751073"
        assert data.intended_for_name == "João Silva"
        assert data.department == "Vendas"
        assert data.reason == "Orçamento"
        assert data.notify_via_whatsapp is True


class TestCallbackResult:
    """Testes para dataclass CallbackResult."""
    
    def test_callback_result_success(self):
        """Resultado de sucesso."""
        result = CallbackResult(
            success=True,
            ticket_id=123,
            ticket_uuid="uuid-123",
            callback_status=CallbackStatus.PENDING
        )
        assert result.success is True
        assert result.ticket_id == 123
        assert result.error is None
    
    def test_callback_result_failure(self):
        """Resultado de falha."""
        result = CallbackResult(
            success=False,
            error="Company ID não configurado"
        )
        assert result.success is False
        assert result.error == "Company ID não configurado"


class TestCallbackHandler:
    """Testes para CallbackHandler."""
    
    @pytest.fixture
    def handler(self):
        """Fixture para criar handler."""
        return CallbackHandler(
            domain_uuid="test-domain-uuid",
            call_uuid="test-call-uuid",
            caller_id="5518997751073",
            omniplay_company_id=1
        )
    
    def test_handler_init(self, handler):
        """Inicialização correta."""
        assert handler.domain_uuid == "test-domain-uuid"
        assert handler.call_uuid == "test-call-uuid"
        assert handler.caller_id == "5518997751073"
        assert handler.omniplay_company_id == 1
    
    def test_set_callback_number_valid(self, handler):
        """Definir número válido."""
        result = handler.set_callback_number("18997751073")
        assert result is True
        assert handler.callback_data.callback_number == "5518997751073"
    
    def test_set_callback_number_invalid(self, handler):
        """Definir número inválido."""
        result = handler.set_callback_number("12345")
        assert result is False
        assert handler.callback_data.callback_number == ""
    
    def test_use_caller_id_as_callback_valid(self, handler):
        """Usar caller ID válido."""
        result = handler.use_caller_id_as_callback()
        assert result is True
        assert handler.callback_data.callback_number == "5518997751073"
    
    def test_use_caller_id_as_callback_internal(self):
        """Caller ID é ramal interno."""
        handler = CallbackHandler(
            domain_uuid="test",
            call_uuid="test",
            caller_id="1001",  # Ramal interno
            omniplay_company_id=1
        )
        result = handler.use_caller_id_as_callback()
        assert result is False
    
    def test_set_reason(self, handler):
        """Definir motivo."""
        handler.set_reason("Preciso de orçamento de produto X")
        assert handler.callback_data.reason == "Preciso de orçamento de produto X"
    
    def test_set_reason_long(self, handler):
        """Motivo muito longo deve ser truncado."""
        long_reason = "A" * 600
        handler.set_reason(long_reason)
        assert len(handler.callback_data.reason) <= 500
        assert handler.callback_data.reason.endswith("...")
    
    def test_set_scheduled_at(self, handler):
        """Definir horário agendado."""
        scheduled = datetime.now() + timedelta(hours=3)
        handler.set_scheduled_at(scheduled)
        assert handler.callback_data.scheduled_at == scheduled
    
    def test_calculate_expiration_default(self, handler):
        """Expiração padrão de 24h."""
        handler.calculate_expiration()
        assert handler.callback_data.expires_at is not None
        # Deve expirar em aproximadamente 24 horas
        delta = handler.callback_data.expires_at - datetime.now()
        assert 23 * 60 * 60 < delta.total_seconds() < 25 * 60 * 60
    
    def test_calculate_expiration_custom(self, handler):
        """Expiração customizada."""
        handler.calculate_expiration(hours=48)
        delta = handler.callback_data.expires_at - datetime.now()
        assert 47 * 60 * 60 < delta.total_seconds() < 49 * 60 * 60
    
    def test_set_notify_via_whatsapp(self, handler):
        """Habilitar notificação WhatsApp."""
        handler.set_notify_via_whatsapp(True)
        assert handler.callback_data.notify_via_whatsapp is True


class TestCallbackStatus:
    """Testes para enum CallbackStatus."""
    
    def test_status_values(self):
        """Verificar valores do enum."""
        assert CallbackStatus.PENDING.value == "pending"
        assert CallbackStatus.NOTIFIED.value == "notified"
        assert CallbackStatus.READY_TO_CALL.value == "ready_to_call"
        assert CallbackStatus.IN_PROGRESS.value == "in_progress"
        assert CallbackStatus.COMPLETED.value == "completed"
        assert CallbackStatus.EXPIRED.value == "expired"
        assert CallbackStatus.CANCELED.value == "canceled"
        assert CallbackStatus.FAILED.value == "failed"
        assert CallbackStatus.NEEDS_REVIEW.value == "needs_review"
