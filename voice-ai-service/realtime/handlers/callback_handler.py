"""
CallbackHandler - Gerencia captura de dados e criação de callbacks.

Referências:
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/proposal.md (Sistema de Callback)
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/tasks.md (FASE 2)

Funcionalidades:
- Captura inteligente de número (valida caller_id, pede confirmação)
- Captura opcional de horário preferido
- Captura do motivo do callback
- Criação do ticket callback via API OmniPlay
- Notificação opcional via WhatsApp

Multi-tenant: domain_uuid obrigatório em todas as operações.
"""

import os
import re
import logging
import aiohttp
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

from .transfer_destination_loader import TransferDestination

logger = logging.getLogger(__name__)

# Configurações
OMNIPLAY_API_URL = os.getenv("OMNIPLAY_API_URL", "http://host.docker.internal:8080")
VOICE_AI_SERVICE_TOKEN = os.getenv("VOICE_AI_SERVICE_TOKEN", "")


class CallbackStatus(Enum):
    """Status do callback."""
    PENDING = "pending"            # Aguardando notificação
    NOTIFIED = "notified"          # Atendente notificado
    READY_TO_CALL = "ready_to_call"  # Atendente pronto para ligar
    IN_PROGRESS = "in_progress"    # Ligação em andamento
    COMPLETED = "completed"        # Callback realizado
    EXPIRED = "expired"            # Expirou sem atendimento
    CANCELED = "canceled"          # Cliente cancelou
    FAILED = "failed"              # Falha técnica
    NEEDS_REVIEW = "needs_review"  # Precisa de revisão (muitas notificações)


@dataclass
class CallbackData:
    """Dados do callback a ser criado."""
    # Número de retorno
    callback_number: str
    callback_extension: Optional[str] = None  # Ramal de retorno se diferente
    
    # Destino pretendido
    intended_for_name: Optional[str] = None
    department: Optional[str] = None
    
    # Motivo e contexto
    reason: Optional[str] = None
    summary: Optional[str] = None
    
    # Agendamento
    scheduled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    
    # Notificação
    notify_via_whatsapp: bool = False
    
    # Dados da chamada original
    voice_call_uuid: Optional[str] = None
    voice_call_duration: Optional[int] = None
    recording_url: Optional[str] = None
    transcript: Optional[List[Dict[str, Any]]] = None


@dataclass
class CallbackResult:
    """Resultado da criação do callback."""
    success: bool
    ticket_id: Optional[int] = None
    ticket_uuid: Optional[str] = None
    callback_status: CallbackStatus = CallbackStatus.PENDING
    error: Optional[str] = None
    whatsapp_sent: bool = False


class PhoneNumberUtils:
    """Utilitários para manipulação de números de telefone."""
    
    # Palavras que indicam "mesmo número"
    SAME_NUMBER_KEYWORDS = [
        "mesmo", "esse", "este", "atual", "que tô ligando",
        "que estou ligando", "de onde tô", "de onde estou"
    ]
    
    # Padrões para extrair números falados
    NUMBER_PATTERNS = [
        # Formato com DDD: "18 99775 1073" ou "18997751073"
        r'(\d{2})\s*(\d{4,5})\s*(\d{4})',
        # Formato internacional: "55 18 99775 1073"
        r'55\s*(\d{2})\s*(\d{4,5})\s*(\d{4})',
        # Números separados por qualquer coisa
        r'(\d{2})\D*(\d{4,5})\D*(\d{4})',
    ]
    
    # Palavras para dígitos (transcrição de fala)
    WORD_TO_DIGIT = {
        "zero": "0", "um": "1", "uma": "1", "dois": "2", "duas": "2",
        "três": "3", "tres": "3", "quatro": "4", "cinco": "5",
        "seis": "6", "meia": "6", "sete": "7", "oito": "8", "nove": "9"
    }
    
    @classmethod
    def normalize_brazilian_number(cls, number: str) -> str:
        """
        Normaliza número brasileiro para formato E.164.
        
        Exemplos:
        - "18997751073" → "5518997751073"
        - "5518997751073" → "5518997751073"
        - "997751073" → "" (inválido, sem DDD)
        """
        if not number:
            return ""
        
        # Remover não-dígitos
        clean = re.sub(r'\D', '', number)
        
        # Já tem +55
        if clean.startswith("55") and len(clean) in (12, 13):
            return clean
        
        # Número brasileiro (10-11 dígitos = DDD + número)
        if len(clean) in (10, 11):
            return f"55{clean}"
        
        return ""
    
    @classmethod
    def validate_brazilian_number(cls, number: str) -> tuple[str, bool]:
        """
        Valida e normaliza número brasileiro.
        
        Returns:
            Tuple (normalized_number, is_valid)
        """
        normalized = cls.normalize_brazilian_number(number)
        
        if not normalized:
            return ("", False)
        
        # Validar formato
        # 55 + DDD (2) + número (8-9)
        if len(normalized) == 12:
            # Fixo: 55 + DDD + 8 dígitos
            ddd = normalized[2:4]
            numero = normalized[4:]
        elif len(normalized) == 13:
            # Celular: 55 + DDD + 9 + 8 dígitos
            ddd = normalized[2:4]
            numero = normalized[4:]
            if not numero.startswith("9"):
                return ("", False)
        else:
            return ("", False)
        
        # Validar DDD (11-99)
        if not (11 <= int(ddd) <= 99):
            return ("", False)
        
        return (normalized, True)
    
    @classmethod
    def is_internal_extension(cls, number: str) -> bool:
        """Verifica se é ramal interno (2-4 dígitos)."""
        if not number:
            return True
        clean = re.sub(r'\D', '', number)
        return len(clean) <= 4
    
    @classmethod
    def extract_phone_from_text(cls, text: str) -> Optional[str]:
        """
        Extrai número de telefone de texto falado.
        
        Exemplos:
        - "18 99775 1073" → "18997751073"
        - "dezoito nove nove sete sete cinco um zero sete três" → "18997751073"
        """
        if not text:
            return None
        
        # 1. Tentar extrair dígitos direto
        for pattern in cls.NUMBER_PATTERNS:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                number = "".join(groups)
                if len(number) >= 10:
                    return number
        
        # 2. Tentar converter palavras para dígitos
        text_lower = text.lower()
        for word, digit in cls.WORD_TO_DIGIT.items():
            text_lower = text_lower.replace(word, digit)
        
        # Extrair todos os dígitos
        digits = re.sub(r'\D', '', text_lower)
        if len(digits) >= 10:
            return digits
        
        return None
    
    @classmethod
    def format_for_speech(cls, number: str) -> str:
        """
        Formata número para TTS (fala natural).
        
        Exemplo: "5518997751073" → "18, 9, 9, 7, 7, 5, 1, 0, 7, 3"
        """
        if not number:
            return ""
        
        # Remover código do país
        clean = number
        if clean.startswith("55"):
            clean = clean[2:]
        
        # Formatar com pausas para TTS
        if len(clean) == 11:
            # Celular: DDD - 9XXXX - XXXX
            return f"{clean[:2]}, {clean[2]}, {', '.join(clean[3:7])}, {', '.join(clean[7:])}"
        elif len(clean) == 10:
            # Fixo: DDD - XXXX - XXXX
            return f"{clean[:2]}, {', '.join(clean[2:6])}, {', '.join(clean[6:])}"
        else:
            return ", ".join(clean)
    
    @classmethod
    def wants_same_number(cls, text: str) -> bool:
        """Verifica se cliente quer usar o mesmo número."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in cls.SAME_NUMBER_KEYWORDS)


class ResponseAnalyzer:
    """Analisa respostas do cliente."""
    
    AFFIRMATIVE_WORDS = [
        "sim", "isso", "correto", "certo", "pode", "positivo",
        "exato", "isso mesmo", "tá certo", "está certo", "ok", "tá",
        "uhum", "aham", "isso aí", "confirmo", "pode ser"
    ]
    
    NEGATIVE_WORDS = [
        "não", "nao", "errado", "incorreto", "outro", "diferente",
        "negativo", "tá errado", "está errado", "outro número"
    ]
    
    @classmethod
    def is_affirmative(cls, text: str) -> bool:
        """Verifica se resposta é afirmativa."""
        text_lower = text.lower().strip()
        
        # Verificar se contém palavra negativa primeiro
        for word in cls.NEGATIVE_WORDS:
            if word in text_lower:
                return False
        
        # Verificar se contém palavra afirmativa
        for word in cls.AFFIRMATIVE_WORDS:
            if word in text_lower:
                return True
        
        # Default: assumir afirmativo para respostas curtas
        return len(text_lower) < 5
    
    @classmethod
    def is_negative(cls, text: str) -> bool:
        """Verifica se resposta é negativa."""
        text_lower = text.lower().strip()
        
        for word in cls.NEGATIVE_WORDS:
            if word in text_lower:
                return True
        
        return False
    
    @classmethod
    def wants_callback(cls, text: str) -> bool:
        """Verifica se cliente quer callback (retorno de ligação)."""
        text_lower = text.lower()
        
        callback_keywords = [
            "retornar", "ligar de volta", "me ligar", "retorno",
            "liga pra mim", "ligação de volta", "callback"
        ]
        
        return any(kw in text_lower for kw in callback_keywords)
    
    @classmethod
    def wants_message(cls, text: str) -> bool:
        """Verifica se cliente quer deixar recado."""
        text_lower = text.lower()
        
        message_keywords = [
            "recado", "mensagem", "anotar", "avisar",
            "deixar um recado", "deixar uma mensagem"
        ]
        
        return any(kw in text_lower for kw in message_keywords)


class CallbackHandler:
    """
    Gerencia o fluxo de captura e criação de callbacks.
    
    Uso:
        handler = CallbackHandler(
            domain_uuid=domain_uuid,
            call_uuid=call_uuid,
            caller_id=caller_id,
        )
        
        # Capturar dados
        await handler.capture_callback_number(original_caller_id)
        
        # Criar callback
        result = await handler.create_callback(
            destination=destination,
            transcript=transcript,
        )
    """
    
    def __init__(
        self,
        domain_uuid: str,
        call_uuid: str,
        caller_id: str,
        secretary_uuid: Optional[str] = None,
        omniplay_company_id: Optional[int] = None,
        on_say: Optional[Callable[[str], Any]] = None,
    ):
        """
        Args:
            domain_uuid: UUID do tenant (FusionPBX)
            call_uuid: UUID da chamada
            caller_id: Número do chamador original
            secretary_uuid: UUID da secretária
            omniplay_company_id: ID da empresa no OmniPlay
            on_say: Callback para enviar mensagem TTS
        """
        self.domain_uuid = domain_uuid
        self.call_uuid = call_uuid
        self.caller_id = caller_id
        self.secretary_uuid = secretary_uuid
        self.omniplay_company_id = omniplay_company_id
        self._on_say = on_say
        
        # Dados do callback
        self._callback_data = CallbackData(callback_number="")
        
        # Estado
        self._number_confirmed = False
        self._http_session: Optional[aiohttp.ClientSession] = None
    
    @property
    def callback_data(self) -> CallbackData:
        """Retorna dados do callback."""
        return self._callback_data
    
    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Obtém sessão HTTP."""
        if self._http_session is None or self._http_session.closed:
            headers = {
                "Content-Type": "application/json",
                "X-Service-Name": "voice-ai-realtime",
            }
            if VOICE_AI_SERVICE_TOKEN:
                headers["Authorization"] = f"Bearer {VOICE_AI_SERVICE_TOKEN}"
            if self.omniplay_company_id:
                headers["X-Company-Id"] = str(self.omniplay_company_id)
            
            self._http_session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._http_session
    
    async def close(self) -> None:
        """Fecha recursos."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
    
    async def _say(self, text: str) -> None:
        """Envia mensagem TTS."""
        if self._on_say:
            result = self._on_say(text)
            if hasattr(result, '__await__'):
                await result
    
    def set_callback_number(self, number: str) -> bool:
        """
        Define número de callback (já validado).
        
        Args:
            number: Número normalizado
        
        Returns:
            True se válido
        """
        normalized, is_valid = PhoneNumberUtils.validate_brazilian_number(number)
        if is_valid:
            self._callback_data.callback_number = normalized
            self._number_confirmed = True
            return True
        return False
    
    def use_caller_id_as_callback(self) -> bool:
        """
        Usa caller ID como número de callback.
        
        Returns:
            True se caller ID é válido
        """
        if PhoneNumberUtils.is_internal_extension(self.caller_id):
            return False
        
        normalized, is_valid = PhoneNumberUtils.validate_brazilian_number(self.caller_id)
        if is_valid:
            self._callback_data.callback_number = normalized
            self._number_confirmed = True
            return True
        return False
    
    def set_intended_destination(self, destination: TransferDestination) -> None:
        """Define destino pretendido."""
        self._callback_data.intended_for_name = destination.name
        self._callback_data.department = destination.department
    
    def set_reason(self, reason: str) -> None:
        """Define motivo do callback."""
        # Limitar tamanho
        if len(reason) > 500:
            reason = reason[:497] + "..."
        self._callback_data.reason = reason
    
    def set_scheduled_at(self, scheduled_at: Optional[datetime]) -> None:
        """Define horário agendado."""
        self._callback_data.scheduled_at = scheduled_at
    
    def set_voice_call_data(
        self,
        duration: int,
        recording_url: Optional[str] = None,
        transcript: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Define dados da chamada original."""
        self._callback_data.voice_call_uuid = self.call_uuid
        self._callback_data.voice_call_duration = duration
        self._callback_data.recording_url = recording_url
        self._callback_data.transcript = transcript
    
    def set_notify_via_whatsapp(self, notify: bool) -> None:
        """Define se deve notificar via WhatsApp."""
        self._callback_data.notify_via_whatsapp = notify
    
    def calculate_expiration(self, hours: int = 24) -> None:
        """Calcula data de expiração."""
        self._callback_data.expires_at = datetime.now() + timedelta(hours=hours)
    
    async def create_callback(
        self,
        summary: Optional[str] = None
    ) -> CallbackResult:
        """
        Cria ticket de callback via API OmniPlay.
        
        Args:
            summary: Resumo opcional da conversa
        
        Returns:
            CallbackResult com status
        """
        if not self._callback_data.callback_number:
            return CallbackResult(
                success=False,
                error="Número de callback não definido"
            )
        
        if not self.omniplay_company_id:
            return CallbackResult(
                success=False,
                error="Company ID não configurado"
            )
        
        # Garantir expiração
        if not self._callback_data.expires_at:
            self.calculate_expiration()
        
        # Preparar payload
        payload = {
            "ticketType": "callback",
            "callbackNumber": self._callback_data.callback_number,
            "callbackExtension": self._callback_data.callback_extension,
            "callbackIntendedForName": self._callback_data.intended_for_name,
            "callbackDepartment": self._callback_data.department,
            "callbackReason": self._callback_data.reason,
            "callbackScheduledAt": (
                self._callback_data.scheduled_at.isoformat()
                if self._callback_data.scheduled_at else None
            ),
            "callbackExpiresAt": (
                self._callback_data.expires_at.isoformat()
                if self._callback_data.expires_at else None
            ),
            "callbackNotifyViaWhatsApp": self._callback_data.notify_via_whatsapp,
            "voiceCallUuid": self._callback_data.voice_call_uuid,
            "voiceCallDuration": self._callback_data.voice_call_duration,
            "voiceRecordingPath": self._callback_data.recording_url,
            "voiceTranscript": (
                str(self._callback_data.transcript)
                if self._callback_data.transcript else None
            ),
            "voiceSummary": summary,
            "voiceDomainUuid": self.domain_uuid,
            # Contexto adicional
            "contact": self._callback_data.callback_number,
            "channel": "voice",
            "status": "pending",
        }
        
        # Remover valores None
        payload = {k: v for k, v in payload.items() if v is not None}
        
        try:
            session = await self._get_http_session()
            url = f"{OMNIPLAY_API_URL}/api/callbacks"
            
            logger.info(
                "Creating callback ticket",
                extra={
                    "call_uuid": self.call_uuid,
                    "callback_number": self._callback_data.callback_number,
                    "intended_for": self._callback_data.intended_for_name,
                }
            )
            
            async with session.post(url, json=payload) as response:
                if response.status in (200, 201):
                    data = await response.json()
                    
                    logger.info(
                        "Callback ticket created",
                        extra={
                            "call_uuid": self.call_uuid,
                            "ticket_id": data.get("id"),
                            "whatsapp_sent": data.get("whatsappSent", False),
                        }
                    )
                    
                    return CallbackResult(
                        success=True,
                        ticket_id=data.get("id"),
                        ticket_uuid=data.get("uuid"),
                        callback_status=CallbackStatus.PENDING,
                        whatsapp_sent=data.get("whatsappSent", False)
                    )
                else:
                    error_text = await response.text()
                    logger.error(
                        f"Failed to create callback: {response.status} - {error_text}",
                        extra={"call_uuid": self.call_uuid}
                    )
                    return CallbackResult(
                        success=False,
                        error=f"API error: {response.status}"
                    )
                    
        except Exception as e:
            logger.exception(f"Error creating callback: {e}")
            return CallbackResult(
                success=False,
                error=str(e)
            )


# Function call definitions para o LLM
CALLBACK_FUNCTION_DEFINITIONS = [
    {
        "type": "function",
        "name": "accept_callback",
        "description": (
            "Cliente aceitou receber uma ligação de retorno (callback). "
            "Use quando o cliente concordar em receber uma ligação depois."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "use_current_number": {
                    "type": "boolean",
                    "description": (
                        "True se o cliente quer usar o mesmo número que está ligando. "
                        "False se quer fornecer outro número."
                    )
                },
                "reason": {
                    "type": "string",
                    "description": "Motivo do callback (resumo do que o cliente precisa)"
                }
            },
            "required": ["use_current_number"]
        }
    },
    {
        "type": "function",
        "name": "provide_callback_number",
        "description": (
            "Cliente forneceu um número diferente para o callback. "
            "Use quando o cliente disser um número de telefone para retorno."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phone_number": {
                    "type": "string",
                    "description": (
                        "Número de telefone fornecido pelo cliente. "
                        "Pode estar em qualquer formato (ex: '18 99775 1073')"
                    )
                }
            },
            "required": ["phone_number"]
        }
    },
    {
        "type": "function",
        "name": "confirm_callback_number",
        "description": (
            "Cliente confirmou que o número de callback está correto. "
            "Use quando o cliente disser 'sim', 'correto', 'isso', etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confirmed": {
                    "type": "boolean",
                    "description": "True se confirmou, False se quer corrigir"
                }
            },
            "required": ["confirmed"]
        }
    },
    {
        "type": "function",
        "name": "schedule_callback",
        "description": (
            "Agendar o horário preferido para o callback. "
            "Use quando o cliente mencionar um horário específico."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "preferred_time": {
                    "type": "string",
                    "description": (
                        "Horário preferido pelo cliente (ex: 'às 14h', 'amanhã de manhã', 'agora'). "
                        "Use 'asap' se cliente quer o mais rápido possível."
                    )
                }
            },
            "required": ["preferred_time"]
        }
    },
    {
        "type": "function",
        "name": "leave_message",
        "description": (
            "Cliente quer deixar uma mensagem/recado ao invés de callback. "
            "Use quando o cliente preferir deixar um recado."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "A mensagem/recado que o cliente quer deixar"
                },
                "for_whom": {
                    "type": "string",
                    "description": "Para quem é a mensagem (nome ou departamento)"
                }
            },
            "required": ["message"]
        }
    }
]


# Export utils para uso externo
__all__ = [
    "CallbackHandler",
    "CallbackData",
    "CallbackResult",
    "CallbackStatus",
    "PhoneNumberUtils",
    "ResponseAnalyzer",
    "CALLBACK_FUNCTION_DEFINITIONS",
]
