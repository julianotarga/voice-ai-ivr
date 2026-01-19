"""
Base interface para providers realtime.

Documentação de referência:
- .context/docs/architecture.md: Key Pattern #1 (Factory Pattern)
- .context/agents/backend-specialist.md: Padrão de criação de providers
- .context/docs/security.md: domain_uuid obrigatório
- openspec/changes/voice-ai-realtime/design.md: Decision 4 (Multi-Provider Factory)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional


class ProviderEventType(Enum):
    """Tipos de eventos que um provider pode emitir."""
    
    # Áudio
    AUDIO_DELTA = "audio_delta"
    AUDIO_DONE = "audio_done"
    
    # Transcrição
    TRANSCRIPT_DELTA = "transcript_delta"
    TRANSCRIPT_DONE = "transcript_done"
    USER_TRANSCRIPT = "user_transcript"
    
    # Estado (VAD)
    SPEECH_STARTED = "speech_started"
    SPEECH_STOPPED = "speech_stopped"
    RESPONSE_STARTED = "response_started"
    RESPONSE_DONE = "response_done"
    
    # Ações
    FUNCTION_CALL = "function_call"
    INTERRUPT = "interrupt"
    
    # Erros
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    
    # Sessão
    SESSION_CREATED = "session_created"
    SESSION_ENDED = "session_ended"


@dataclass
class ProviderEvent:
    """
    Evento emitido por um provider realtime.
    Estrutura unificada para todos os providers.
    """
    
    type: ProviderEventType
    data: Dict[str, Any]
    item_id: Optional[str] = None
    response_id: Optional[str] = None
    
    @property
    def audio_bytes(self) -> Optional[bytes]:
        return self.data.get("audio")
    
    @property
    def transcript(self) -> Optional[str]:
        return self.data.get("transcript")
    
    @property
    def function_name(self) -> Optional[str]:
        return self.data.get("function_name")
    
    @property
    def function_args(self) -> Optional[Dict[str, Any]]:
        return self.data.get("arguments")


@dataclass
class RealtimeConfig:
    """
    Configuração para sessão realtime.
    
    Multi-tenant: domain_uuid é OBRIGATÓRIO conforme
    .context/docs/security.md
    """
    
    # Multi-tenant (OBRIGATÓRIO)
    domain_uuid: str
    
    # Provider
    provider_name: str = "openai"
    
    # Model (para OpenAI Realtime)
    model: str = "gpt-realtime"  # gpt-realtime, gpt-realtime-mini
    
    # Secretary (opcional - pode ser passado via session config)
    secretary_uuid: Optional[str] = None
    
    # Instruções
    system_prompt: str = ""
    first_message: Optional[str] = None
    
    # Voz
    voice: str = "alloy"
    
    # VAD (Voice Activity Detection)
    # Tipo: "server_vad" (silêncio) ou "semantic_vad" (semântica/inteligente)
    vad_type: str = "semantic_vad"  # RECOMENDADO
    vad_threshold: float = 0.5  # 0.0-1.0 sensibilidade
    vad_eagerness: str = "high"  # high responde rápido (só semantic_vad)
    silence_duration_ms: int = 500
    silence_timeout_ms: int = 1000  # Alias para compatibilidade
    prefix_padding_ms: int = 300
    
    # Guardrails
    guardrails_enabled: bool = True
    guardrails_topics: Optional[List[str]] = None  # Tópicos proibidos customizados
    
    # Tools
    tools: Optional[List[Dict[str, Any]]] = None
    
    # Limites
    max_response_output_tokens: Optional[int] = 4096  # None = infinito
    
    # Audio
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    
    # Audio format
    # - "pcm16" or "l16": Linear PCM 16-bit (legacy)
    # - "g711_ulaw" or "pcmu": G.711 μ-law (recommended for telephony)
    # - "g711_alaw" or "pcma": G.711 A-law
    audio_format: str = "g711_ulaw"  # G.711 μ-law por padrão


class BaseRealtimeProvider(ABC):
    """
    Interface abstrata para providers realtime.
    
    Conforme openspec/changes/voice-ai-realtime/design.md (Decision 4).
    
    Implementações:
    - OpenAIRealtimeProvider
    - ElevenLabsConversationalProvider
    - GeminiLiveProvider
    - CustomPipelineProvider
    """
    
    def __init__(self, credentials: Dict[str, Any], config: RealtimeConfig):
        self.credentials = credentials
        self.config = config
        self._connected = False
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Nome identificador do provider."""
        pass
    
    @property
    @abstractmethod
    def input_sample_rate(self) -> int:
        """Sample rate esperado para entrada."""
        pass
    
    @property
    @abstractmethod
    def output_sample_rate(self) -> int:
        """Sample rate do áudio de saída."""
        pass
    
    @abstractmethod
    async def connect(self) -> None:
        """Estabelece conexão WebSocket."""
        pass
    
    @abstractmethod
    async def configure(self) -> None:
        """Configura sessão (prompt, voz, VAD)."""
        pass
    
    @abstractmethod
    async def send_audio(self, audio_bytes: bytes) -> None:
        """Envia chunk de áudio."""
        pass
    
    @abstractmethod
    async def send_text(self, text: str) -> None:
        """Envia mensagem de texto."""
        pass
    
    @abstractmethod
    async def interrupt(self) -> None:
        """Interrompe resposta atual (barge-in)."""
        pass
    
    @abstractmethod
    async def send_function_result(
        self, 
        function_name: str, 
        result: Dict[str, Any],
        call_id: Optional[str] = None
    ) -> None:
        """Envia resultado de function call."""
        pass
    
    @abstractmethod
    async def receive_events(self) -> AsyncIterator[ProviderEvent]:
        """Generator de eventos do provider."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Encerra conexão."""
        pass
    
    async def __aenter__(self) -> "BaseRealtimeProvider":
        await self.connect()
        await self.configure()
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.disconnect()
