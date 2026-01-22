"""
VoiceEvent - Sistema de eventos internos do Voice AI.

Este módulo define todos os tipos de eventos que podem ocorrer durante
uma chamada, abstraindo eventos do FreeSWITCH e do provider (OpenAI).

Referência: voice-ai-ivr/docs/PLANO-ARQUITETURA-INTERNA.md
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict
import time


class VoiceEventType(Enum):
    """
    Tipos de eventos internos do Voice AI.
    
    Estes eventos abstraem:
    - Eventos ESL do FreeSWITCH (CHANNEL_*, DTMF, etc)
    - Eventos do provider (OpenAI Realtime API)
    - Eventos internos do sistema
    
    Vantagem: Lógica de negócio reage a eventos internos,
    não a eventos específicos de cada tecnologia.
    """
    
    # ========================================
    # CHAMADA - Ciclo de vida
    # ========================================
    CALL_STARTED = "call_started"           # Chamada iniciada
    CALL_CONNECTED = "call_connected"       # WebSocket conectado
    CALL_ENDING = "call_ending"             # Iniciando encerramento
    CALL_ENDED = "call_ended"               # Chamada finalizada
    
    # ========================================
    # ÁUDIO - AI (saída para o caller)
    # ========================================
    AI_SPEAKING_STARTED = "ai_speaking_started"     # IA começou a falar
    AI_SPEAKING_DONE = "ai_speaking_done"           # IA terminou de falar
    AI_AUDIO_CHUNK = "ai_audio_chunk"               # Chunk de áudio gerado
    AI_AUDIO_BUFFER_LOW = "ai_audio_buffer_low"     # Buffer abaixo do threshold
    AI_AUDIO_COMPLETE = "ai_audio_complete"         # Todo áudio reproduzido
    AI_RESPONSE_STARTED = "ai_response_started"     # OpenAI iniciou resposta
    AI_RESPONSE_DONE = "ai_response_done"           # OpenAI finalizou resposta
    
    # ========================================
    # ÁUDIO - Usuário (entrada do caller)
    # ========================================
    USER_SPEAKING_STARTED = "user_speaking_started" # Caller começou a falar
    USER_SPEAKING_DONE = "user_speaking_done"       # Caller parou de falar
    USER_AUDIO_RECEIVED = "user_audio_received"     # Áudio recebido do caller
    USER_TRANSCRIPT = "user_transcript"             # Transcrição do que o caller disse
    USER_DTMF = "user_dtmf"                         # Dígito DTMF pressionado
    
    # ========================================
    # TRANSFERÊNCIA - Fluxo completo
    # ========================================
    TRANSFER_REQUESTED = "transfer_requested"       # IA chamou request_handoff
    TRANSFER_VALIDATED = "transfer_validated"       # Destino encontrado e válido
    TRANSFER_VALIDATION_FAILED = "transfer_validation_failed"  # Destino não encontrado
    TRANSFER_DIALING = "transfer_dialing"           # Discando para atendente
    TRANSFER_RINGING = "transfer_ringing"           # Atendente tocando
    TRANSFER_ANSWERED = "transfer_answered"         # Atendente atendeu
    TRANSFER_ANNOUNCING = "transfer_announcing"     # Falando com atendente
    TRANSFER_ANNOUNCEMENT_DONE = "transfer_announcement_done"  # Anúncio finalizado
    TRANSFER_WAITING_RESPONSE = "transfer_waiting_response"    # Aguardando aceitar/recusar
    TRANSFER_ACCEPTED = "transfer_accepted"         # Atendente aceitou
    TRANSFER_REJECTED = "transfer_rejected"         # Atendente recusou
    TRANSFER_TIMEOUT = "transfer_timeout"           # Timeout interno
    TRANSFER_BRIDGING = "transfer_bridging"         # Fazendo bridge
    TRANSFER_COMPLETED = "transfer_completed"       # Bridge feito com sucesso
    TRANSFER_FAILED = "transfer_failed"             # Falha geral
    TRANSFER_CANCELLED = "transfer_cancelled"       # Cliente desligou durante
    
    # ========================================
    # HOLD - Espera
    # ========================================
    HOLD_STARTED = "hold_started"           # Cliente colocado em espera
    HOLD_ENDED = "hold_ended"               # Cliente retirado da espera
    
    # ========================================
    # ESTADO - Máquina de estados
    # ========================================
    STATE_CHANGED = "state_changed"                     # Mudança de estado
    STATE_TRANSITION_BLOCKED = "state_transition_blocked"  # Transição bloqueada por guard
    
    # ========================================
    # CONEXÃO - Saúde
    # ========================================
    CONNECTION_HEALTHY = "connection_healthy"       # Conexão OK
    CONNECTION_DEGRADED = "connection_degraded"     # Problemas detectados
    CONNECTION_LOST = "connection_lost"             # Conexão perdida
    WEBSOCKET_DISCONNECTED = "websocket_disconnected"  # WebSocket desconectou
    PROVIDER_TIMEOUT = "provider_timeout"           # OpenAI não respondeu
    
    # ========================================
    # FUNÇÃO - Function calls
    # ========================================
    FUNCTION_CALL_STARTED = "function_call_started"     # Função chamada pela IA
    FUNCTION_CALL_COMPLETED = "function_call_completed" # Função executada
    FUNCTION_CALL_FAILED = "function_call_failed"       # Erro na função


@dataclass
class VoiceEvent:
    """
    Evento interno do Voice AI.
    
    Representa qualquer evento que ocorre durante uma chamada.
    Usado pelo EventBus para comunicação desacoplada entre componentes.
    
    Attributes:
        type: Tipo do evento (VoiceEventType)
        call_uuid: UUID da chamada
        data: Dados adicionais do evento (varia por tipo)
        timestamp: Momento do evento
        source: Origem do evento (para debug)
    """
    
    type: VoiceEventType
    call_uuid: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = "internal"  # "internal", "esl", "provider", "websocket"
    
    def __repr__(self) -> str:
        call_short = self.call_uuid[:8] if self.call_uuid else "none"
        data_preview = str(self.data)[:50] if self.data else "{}"
        return f"VoiceEvent({self.type.value}, call={call_short}..., data={data_preview})"
    
    def __str__(self) -> str:
        return f"[{self.type.value}] {self.data}"
    
    def with_data(self, **kwargs) -> 'VoiceEvent':
        """Retorna cópia do evento com dados adicionais"""
        new_data = {**self.data, **kwargs}
        return VoiceEvent(
            type=self.type,
            call_uuid=self.call_uuid,
            data=new_data,
            timestamp=self.timestamp,
            source=self.source
        )
