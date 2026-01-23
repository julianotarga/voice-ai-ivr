"""
Call Logger - Coleta logs estruturados de chamadas para RCA.

Este módulo fornece:
- CallLogger: Classe principal para coletar eventos/métricas
- CallEvent: Evento na timeline da chamada
- CallMetric: Métrica numérica
- ToolExecution: Registro de execução de tool

Os logs são enviados ao backend via webhook ao final da chamada.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum
import asyncio
import aiohttp
import logging
import time

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Tipos de eventos na timeline."""
    # Sessão
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_ERROR = "session_error"
    
    # OpenAI
    OPENAI_CONNECTED = "openai_connected"
    OPENAI_DISCONNECTED = "openai_disconnected"
    OPENAI_ERROR = "openai_error"
    
    # Áudio
    AUDIO_FIRST_INPUT = "audio_first_input"
    AUDIO_FIRST_OUTPUT = "audio_first_output"
    AUDIO_SILENCE_DETECTED = "audio_silence_detected"
    
    # Transcrição
    TRANSCRIPT_USER = "transcript_user"
    TRANSCRIPT_ASSISTANT = "transcript_assistant"
    
    # Tools
    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    
    # Transferência
    TRANSFER_INITIATED = "transfer_initiated"
    TRANSFER_RINGING = "transfer_ringing"
    TRANSFER_ANSWERED = "transfer_answered"
    TRANSFER_REJECTED = "transfer_rejected"
    TRANSFER_NO_ANSWER = "transfer_no_answer"
    TRANSFER_COMPLETED = "transfer_completed"
    TRANSFER_FAILED = "transfer_failed"
    
    # Recado
    MESSAGE_TAKEN = "message_taken"
    
    # Chamada
    CALL_HANGUP = "call_hangup"


@dataclass
class CallEvent:
    """Evento na timeline da chamada."""
    type: EventType
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    data: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value if isinstance(self.type, EventType) else self.type,
            "timestamp": self.timestamp,
            "data": self.data or {}
        }


@dataclass
class CallMetric:
    """Métrica numérica da chamada."""
    name: str
    value: float
    unit: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ToolExecution:
    """Registro de execução de um tool."""
    name: str
    input: Dict[str, Any]
    output: Dict[str, Any]
    duration_ms: float
    timestamp: str
    success: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CallLogger:
    """
    Logger estruturado para uma chamada de voz.
    
    Coleta eventos, métricas e execuções de tools durante a chamada.
    Ao final, envia tudo para o backend via webhook.
    
    Uso:
        logger = CallLogger(
            call_uuid="abc-123",
            webhook_url="https://api.omniplay.com/api/voice-ai/webhook/logs",
            company_id=42
        )
        
        logger.log_event(EventType.SESSION_START)
        logger.log_metric("ai_response_time_ms", 150)
        logger.log_tool("take_message", {...}, {...}, 50.5, True)
        
        await logger.flush()  # Envia ao backend
    """
    
    def __init__(
        self,
        call_uuid: str,
        webhook_url: Optional[str] = None,
        company_id: Optional[int] = None,
        secretary_id: Optional[str] = None,
        caller_id: Optional[str] = None
    ):
        self.call_uuid = call_uuid
        self.webhook_url = webhook_url
        self.company_id = company_id
        self.secretary_id = secretary_id
        self.caller_id = caller_id
        
        # Dados coletados
        self._events: List[CallEvent] = []
        self._metrics: Dict[str, float] = {}
        self._tools: List[ToolExecution] = []
        
        # Metadados
        self._started_at: Optional[str] = None
        self._ended_at: Optional[str] = None
        self._final_state: Optional[str] = None
        self._error_message: Optional[str] = None
        self._caller_name: Optional[str] = None
        self._outcome: Optional[str] = None
        self._transfer_destination: Optional[str] = None
        self._transfer_result: Optional[str] = None
        
        # Lock para thread safety
        self._lock = asyncio.Lock()
        
        # Marcar início
        self._started_at = datetime.utcnow().isoformat()
    
    def log_event(
        self, 
        event_type: EventType, 
        data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Registra um evento na timeline.
        
        Thread-safe: usa lista que é thread-safe para appends em Python.
        
        Args:
            event_type: Tipo do evento
            data: Dados adicionais do evento
        """
        try:
            event = CallEvent(type=event_type, data=data)
            self._events.append(event)
            
            logger.debug(f"📝 [RCA] Event: {event_type.value}", extra={
                "call_uuid": self.call_uuid,
                "event_type": event_type.value,
                "data": data
            })
        except Exception as e:
            # Nunca falhar por causa de logging
            logger.warning(f"📝 [RCA] Erro ao registrar evento: {e}")
    
    def log_metric(self, name: str, value: float, aggregate: str = "last") -> None:
        """
        Registra uma métrica numérica.
        
        Args:
            name: Nome da métrica
            value: Valor numérico
            aggregate: Como agregar valores repetidos ("last", "sum", "max", "min", "avg")
        """
        try:
            if aggregate == "last":
                self._metrics[name] = value
            elif aggregate == "sum":
                self._metrics[name] = self._metrics.get(name, 0) + value
            elif aggregate == "max":
                self._metrics[name] = max(self._metrics.get(name, float('-inf')), value)
            elif aggregate == "min":
                self._metrics[name] = min(self._metrics.get(name, float('inf')), value)
            elif aggregate == "avg":
                # Para avg, armazenamos soma e contagem
                sum_key = f"__{name}_sum"
                count_key = f"__{name}_count"
                self._metrics[sum_key] = self._metrics.get(sum_key, 0) + value
                self._metrics[count_key] = self._metrics.get(count_key, 0) + 1
                self._metrics[name] = self._metrics[sum_key] / self._metrics[count_key]
        except Exception as e:
            logger.warning(f"📝 [RCA] Erro ao registrar métrica {name}: {e}")
    
    def log_tool(
        self,
        name: str,
        input_args: Dict[str, Any],
        output: Dict[str, Any],
        duration_ms: float,
        success: bool
    ) -> None:
        """
        Registra execução de um tool.
        
        Args:
            name: Nome do tool
            input_args: Argumentos de entrada
            output: Resultado do tool
            duration_ms: Duração da execução em ms
            success: Se foi bem-sucedido
        """
        try:
            # Sanitizar input/output para evitar dados sensíveis
            safe_input = self._sanitize_data(input_args)
            safe_output = self._sanitize_data(output)
            
            execution = ToolExecution(
                name=name,
                input=safe_input,
                output=safe_output,
                duration_ms=duration_ms,
                timestamp=datetime.utcnow().isoformat(),
                success=success
            )
            self._tools.append(execution)
            
            # Também registrar como evento
            self.log_event(
                EventType.TOOL_CALLED if success else EventType.TOOL_ERROR,
                {"tool": name, "success": success, "duration_ms": duration_ms}
            )
        except Exception as e:
            logger.warning(f"📝 [RCA] Erro ao registrar tool {name}: {e}")
    
    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove dados sensíveis de dicts para logging."""
        if not data:
            return {}
        
        sensitive_keys = {"password", "token", "secret", "api_key", "authorization"}
        result = {}
        
        for key, value in data.items():
            if key.lower() in sensitive_keys:
                result[key] = "[REDACTED]"
            elif isinstance(value, dict):
                result[key] = self._sanitize_data(value)
            elif isinstance(value, str) and len(value) > 1000:
                # Truncar strings muito longas
                result[key] = value[:1000] + "...[truncated]"
            else:
                result[key] = value
        
        return result
    
    def set_caller_name(self, name: str) -> None:
        """Define o nome do cliente."""
        self._caller_name = name
    
    def set_outcome(self, outcome: str) -> None:
        """Define o resultado da chamada (message_taken, transferred, hangup, error)."""
        self._outcome = outcome
    
    def set_transfer_info(self, destination: str, result: str) -> None:
        """Define informações de transferência."""
        self._transfer_destination = destination
        self._transfer_result = result
    
    def set_error(self, message: str) -> None:
        """Define mensagem de erro."""
        self._error_message = message
        self._final_state = "error"
    
    def set_final_state(self, state: str) -> None:
        """Define estado final (ended, transferred, error, timeout)."""
        self._final_state = state
    
    def to_dict(self) -> Dict[str, Any]:
        """Converte todos os logs para dict."""
        # Calcular duração
        duration_ms = None
        if self._started_at and self._ended_at:
            try:
                start = datetime.fromisoformat(self._started_at.replace('Z', '+00:00'))
                end = datetime.fromisoformat(self._ended_at.replace('Z', '+00:00'))
                duration_ms = int((end - start).total_seconds() * 1000)
            except:
                pass
        
        # Limpar métricas internas de agregação
        clean_metrics = {
            k: v for k, v in self._metrics.items() 
            if not k.startswith("__")
        }
        
        return {
            "call_uuid": self.call_uuid,
            "company_id": self.company_id,
            "secretary_id": self.secretary_id,
            "caller_id": self.caller_id,
            "caller_name": self._caller_name,
            "started_at": self._started_at,
            "ended_at": self._ended_at,
            "duration_ms": duration_ms,
            "final_state": self._final_state,
            "error_message": self._error_message,
            "events": [e.to_dict() for e in self._events],
            "metrics": clean_metrics,
            "tools_executed": [t.to_dict() for t in self._tools],
            "outcome": self._outcome,
            "transfer_destination": self._transfer_destination,
            "transfer_result": self._transfer_result
        }
    
    async def flush(self) -> bool:
        """
        Envia todos os logs para o backend via webhook.
        
        Returns:
            True se enviou com sucesso, False caso contrário
        """
        async with self._lock:
            # Marcar fim
            self._ended_at = datetime.utcnow().isoformat()
            if not self._final_state:
                self._final_state = "ended"
            
            # Log final
            self.log_event(EventType.SESSION_END, {
                "final_state": self._final_state,
                "events_count": len(self._events),
                "tools_count": len(self._tools)
            })
            
            if not self.webhook_url:
                logger.warning("📝 [RCA] Nenhum webhook_url configurado - logs não enviados", extra={
                    "call_uuid": self.call_uuid
                })
                return False
            
            payload = {
                "event": "voice_ai_call_log",
                **self.to_dict()
            }
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.webhook_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status in (200, 201):
                            logger.info("📝 [RCA] Logs enviados ao backend", extra={
                                "call_uuid": self.call_uuid,
                                "events": len(self._events),
                                "tools": len(self._tools)
                            })
                            return True
                        else:
                            resp_text = await resp.text()
                            logger.warning(f"📝 [RCA] Webhook retornou {resp.status}: {resp_text}", extra={
                                "call_uuid": self.call_uuid
                            })
                            return False
            except Exception as e:
                logger.error(f"📝 [RCA] Erro ao enviar logs: {e}", extra={
                    "call_uuid": self.call_uuid
                })
                return False
    
    def __repr__(self) -> str:
        return f"<CallLogger call_uuid={self.call_uuid} events={len(self._events)} tools={len(self._tools)}>"
