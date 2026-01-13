# Design: Voice AI Realtime Architecture

## Goals

1. **Latência < 500ms** - Resposta em menos de meio segundo
2. **Full-duplex** - Fala simultânea usuário/IA
3. **Barge-in** - Interrupção natural do usuário
4. **Multi-provider** - OpenAI, ElevenLabs, Gemini, Custom
5. **Multi-tenant** - Isolamento total por domain_uuid
6. **Escalável** - Suportar centenas de chamadas simultâneas
7. **Resiliente** - Fallback automático entre providers

---

## Decision 1: Arquitetura de Streaming

### Opção A: Streaming Direto (FreeSWITCH → AI Provider) ❌
```
FreeSWITCH → mod_audio_stream → OpenAI Realtime API
```
**Problemas**:
- Cada provider tem protocolo diferente
- Difícil implementar multi-tenant
- Sem RAG/knowledge base
- Sem fallback

### Opção B: Bridge Intermediário ✅ (Escolhida)
```
FreeSWITCH → mod_audio_stream → Python Bridge → AI Provider
```
**Vantagens**:
- Abstração de providers
- Multi-tenant nativo
- RAG/function calling
- Fallback automático
- Logging/monitoring

---

## Decision 2: Protocolo de Comunicação

### FreeSWITCH ↔ Bridge

**Protocolo**: WebSocket (RFC 6455)
**URL Pattern**: `ws://bridge:8085/stream/{domain_uuid}/{call_uuid}`
**Codec**: PCM16 Linear @ 16kHz mono

```
┌─────────────────────────────────────────────────────────────┐
│              WEBSOCKET PROTOCOL (FS → Bridge)                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  BINARY FRAMES (Audio):                                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ PCM16 bytes (640 bytes = 20ms @ 16kHz)               │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  TEXT FRAMES (Control):                                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ {"type": "metadata", "caller_id": "...", ...}        │   │
│  │ {"type": "dtmf", "digit": "5"}                       │   │
│  │ {"type": "hangup"}                                   │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  BINARY FRAMES (Playback - Bridge → FS):                     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ {"type":"streamAudio","data":{"audioData":"base64"}} │   │
│  │ or raw PCM16 binary                                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Bridge ↔ AI Provider

Cada provider tem seu próprio protocolo WebSocket:

#### OpenAI Realtime API
```python
# Events enviados
{
    "type": "input_audio_buffer.append",
    "audio": "<base64 PCM16 @ 24kHz>"
}

# Events recebidos
{
    "type": "response.audio.delta",
    "delta": "<base64 PCM16 @ 24kHz>"
}
{
    "type": "response.audio_transcript.delta",
    "delta": "Hello, how can I..."
}
{
    "type": "input_audio_buffer.speech_started"  # VAD detected speech
}
{
    "type": "input_audio_buffer.speech_stopped"  # VAD detected silence
}
```

#### ElevenLabs Conversational AI
```python
# Audio enviado
{
    "user_audio_chunk": "<base64 PCM16 @ 16kHz>"
}

# Events recebidos
{
    "type": "audio",
    "audio_event": {
        "audio_base_64": "<base64 PCM16 @ 16kHz>",
        "is_final": false
    }
}
{
    "type": "agent_response",
    "agent_response_event": {
        "agent_response": "Hello! How can I help?"
    }
}
```

---

## Decision 3: Arquitetura do Bridge

### Estrutura de Diretórios

```
voice-ai-service/
├── realtime/
│   ├── __init__.py
│   ├── server.py              # WebSocket server principal
│   ├── session_manager.py     # Gerencia sessões ativas
│   ├── audio_processor.py     # Resample, buffer, format conversion
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py            # Interface abstrata
│   │   ├── openai_realtime.py # OpenAI Realtime API
│   │   ├── elevenlabs_conv.py # ElevenLabs Conversational
│   │   ├── gemini_live.py     # Google Gemini 2.0 Flash
│   │   └── custom_pipeline.py # STT + LLM + TTS separados
│   │
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── freeswitch.py      # Handler para conexões do FS
│   │   ├── function_call.py   # Execução de function calls
│   │   └── transfer.py        # Lógica de transferência
│   │
│   └── utils/
│       ├── __init__.py
│       ├── resampler.py       # Conversão de sample rate
│       ├── vad.py             # Voice Activity Detection local
│       └── metrics.py         # Prometheus metrics
│
├── config/
│   └── realtime_settings.py
│
└── tests/
    └── realtime/
        ├── test_providers.py
        └── test_audio.py
```

### Classe Principal: RealtimeSession

```python
class RealtimeSession:
    """
    Gerencia uma sessão de conversa realtime.
    Uma instância por chamada ativa.
    """
    
    def __init__(
        self,
        domain_uuid: str,
        call_uuid: str,
        caller_id: str,
        fs_websocket: WebSocket,
        config: SecretaryRealtimeConfig,
    ):
        self.domain_uuid = domain_uuid
        self.call_uuid = call_uuid
        self.caller_id = caller_id
        self.fs_ws = fs_websocket
        self.config = config
        
        # Provider connection
        self.provider: BaseRealtimeProvider = None
        
        # Audio processing
        self.input_resampler = Resampler(16000, 24000)  # FS → Provider
        self.output_resampler = Resampler(24000, 16000)  # Provider → FS
        
        # State
        self.transcript: List[dict] = []
        self.is_speaking = False
        self.turn_count = 0
        
        # Metrics
        self.started_at = datetime.now()
        self.latencies: List[float] = []
    
    async def start(self):
        """Inicia a sessão e conecta ao provider."""
        self.provider = await self._create_provider()
        await self.provider.connect()
        await self.provider.configure(self.config)
        
        # Enviar saudação inicial se configurada
        if self.config.first_message:
            await self.provider.send_text(self.config.first_message)
    
    async def handle_audio_from_fs(self, audio_bytes: bytes):
        """Processa áudio vindo do FreeSWITCH."""
        # Resample se necessário
        resampled = self.input_resampler.process(audio_bytes)
        
        # Enviar para o provider
        await self.provider.send_audio(resampled)
    
    async def handle_audio_from_provider(self, audio_bytes: bytes):
        """Processa áudio vindo do provider de IA."""
        # Resample de volta para 16kHz
        resampled = self.output_resampler.process(audio_bytes)
        
        # Enviar para FreeSWITCH
        await self.fs_ws.send_bytes(resampled)
    
    async def handle_function_call(self, function_name: str, args: dict):
        """Executa function calls do provider."""
        if function_name == "transfer_call":
            await self._transfer_to(args["destination"])
        elif function_name == "create_ticket":
            await self._create_omniplay_ticket(args)
        elif function_name == "lookup_customer":
            result = await self._lookup_customer(args["phone"])
            await self.provider.send_function_result(function_name, result)
    
    async def stop(self):
        """Encerra a sessão."""
        await self.provider.disconnect()
        await self._save_conversation()
        await self._calculate_costs()
```

---

## Decision 4: Multi-Provider Factory

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator

class BaseRealtimeProvider(ABC):
    """Interface base para providers realtime."""
    
    @abstractmethod
    async def connect(self) -> None:
        """Estabelece conexão WebSocket com o provider."""
        pass
    
    @abstractmethod
    async def configure(self, config: dict) -> None:
        """Configura a sessão (prompt, voz, VAD, etc)."""
        pass
    
    @abstractmethod
    async def send_audio(self, audio_bytes: bytes) -> None:
        """Envia chunk de áudio para o provider."""
        pass
    
    @abstractmethod
    async def send_text(self, text: str) -> None:
        """Envia mensagem de texto para o provider."""
        pass
    
    @abstractmethod
    async def interrupt(self) -> None:
        """Interrompe a resposta atual (barge-in)."""
        pass
    
    @abstractmethod
    async def receive_events(self) -> AsyncIterator[ProviderEvent]:
        """Recebe eventos do provider (áudio, transcript, etc)."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Encerra a conexão."""
        pass


class RealtimeProviderFactory:
    """Factory para criar providers realtime."""
    
    _providers = {
        "openai": OpenAIRealtimeProvider,
        "elevenlabs": ElevenLabsConversationalProvider,
        "gemini": GeminiLiveProvider,
        "custom": CustomPipelineProvider,
    }
    
    @classmethod
    async def create(
        cls,
        provider_name: str,
        domain_uuid: str,
        config: dict,
    ) -> BaseRealtimeProvider:
        """Cria instância do provider configurado."""
        if provider_name not in cls._providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        
        # Buscar credenciais do banco
        credentials = await get_provider_credentials(domain_uuid, provider_name)
        
        provider_class = cls._providers[provider_name]
        return provider_class(credentials=credentials, config=config)
```

---

## Decision 5: Resampling de Áudio

### Problema
- FreeSWITCH: 8kHz ou 16kHz
- OpenAI Realtime: 24kHz
- ElevenLabs: 16kHz
- Gemini: 16kHz

### Solução: Resampler Otimizado

```python
import numpy as np
from scipy import signal

class Resampler:
    """
    Resampler eficiente para streaming de áudio.
    Usa scipy.signal.resample_poly para qualidade e performance.
    """
    
    def __init__(self, input_rate: int, output_rate: int):
        self.input_rate = input_rate
        self.output_rate = output_rate
        
        # Calcular fatores de up/down sampling
        from math import gcd
        g = gcd(input_rate, output_rate)
        self.up = output_rate // g
        self.down = input_rate // g
        
        # Buffer para acumular samples entre chunks
        self.buffer = np.array([], dtype=np.int16)
    
    def process(self, audio_bytes: bytes) -> bytes:
        """Processa um chunk de áudio."""
        # Converter bytes para numpy array
        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        
        # Acumular no buffer
        self.buffer = np.concatenate([self.buffer, samples])
        
        # Resample
        if self.up != 1 or self.down != 1:
            resampled = signal.resample_poly(
                self.buffer.astype(np.float32),
                self.up,
                self.down
            ).astype(np.int16)
        else:
            resampled = self.buffer
        
        # Limpar buffer
        self.buffer = np.array([], dtype=np.int16)
        
        return resampled.tobytes()
```

---

## Decision 6: Voice Activity Detection (VAD)

### Para providers com VAD nativo (OpenAI, ElevenLabs, Gemini)
- Usar o VAD do provider
- Configurar threshold e silence duration

### Para Custom Pipeline
- Usar Silero VAD (leve, preciso)

```python
import torch

class SileroVAD:
    """Voice Activity Detection usando Silero."""
    
    def __init__(self, threshold: float = 0.5):
        self.model, self.utils = torch.hub.load(
            'snakers4/silero-vad', 'silero_vad'
        )
        self.threshold = threshold
        self.sample_rate = 16000
    
    def is_speech(self, audio_bytes: bytes) -> bool:
        """Detecta se o chunk contém fala."""
        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        tensor = torch.from_numpy(samples).float() / 32768.0
        
        confidence = self.model(tensor, self.sample_rate).item()
        return confidence > self.threshold
    
    def reset(self):
        """Reseta o estado do VAD."""
        self.model.reset_states()
```

---

## Decision 7: Function Calling / Tool Use

### Ferramentas Disponíveis

```python
REALTIME_TOOLS = [
    {
        "type": "function",
        "name": "transfer_call",
        "description": "Transfere a chamada para outro ramal ou departamento",
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Número do ramal ou nome do departamento"
                },
                "reason": {
                    "type": "string",
                    "description": "Motivo da transferência"
                }
            },
            "required": ["destination"]
        }
    },
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Cria um ticket no sistema OmniPlay",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]}
            },
            "required": ["subject"]
        }
    },
    {
        "type": "function",
        "name": "lookup_customer",
        "description": "Busca informações do cliente pelo telefone",
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {"type": "string"}
            },
            "required": ["phone"]
        }
    },
    {
        "type": "function",
        "name": "check_appointment",
        "description": "Verifica agenda de compromissos",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "format": "date"},
                "customer_name": {"type": "string"}
            }
        }
    }
]
```

### Execução de Function Calls

```python
class FunctionCallHandler:
    """Executa function calls do provider de IA."""
    
    def __init__(self, session: RealtimeSession):
        self.session = session
        self.db = get_database()
    
    async def execute(self, function_name: str, args: dict) -> dict:
        """Executa a função e retorna o resultado."""
        
        if function_name == "transfer_call":
            return await self._transfer_call(args)
        
        elif function_name == "create_ticket":
            return await self._create_ticket(args)
        
        elif function_name == "lookup_customer":
            return await self._lookup_customer(args)
        
        elif function_name == "check_appointment":
            return await self._check_appointment(args)
        
        else:
            return {"error": f"Unknown function: {function_name}"}
    
    async def _transfer_call(self, args: dict) -> dict:
        """Transfere a chamada via ESL."""
        destination = args["destination"]
        
        # Resolver destino (pode ser nome de departamento)
        resolved = await self._resolve_destination(destination)
        
        # Enviar comando para FreeSWITCH via ESL
        esl_client = get_esl_client()
        await esl_client.execute(
            self.session.call_uuid,
            "transfer",
            resolved
        )
        
        return {"status": "transferred", "destination": resolved}
    
    async def _create_ticket(self, args: dict) -> dict:
        """Cria ticket no OmniPlay."""
        webhook_url = await self._get_omniplay_webhook()
        
        payload = {
            "caller_id": self.session.caller_id,
            "subject": args["subject"],
            "description": args.get("description", ""),
            "priority": args.get("priority", "medium"),
            "transcript": self.session.transcript,
            "domain_uuid": self.session.domain_uuid,
        }
        
        async with aiohttp.ClientSession() as client:
            async with client.post(webhook_url, json=payload) as resp:
                result = await resp.json()
        
        return {"status": "created", "ticket_id": result.get("id")}
```

---

## Decision 8: FreeSWITCH Dialplan

### Extensão para Secretária Realtime

```xml
<!-- /etc/freeswitch/dialplan/default/900_voice_ai_realtime.xml -->

<extension name="voice_ai_realtime">
  <condition field="destination_number" expression="^(8\d{3})$">
    <!-- Obter domain_uuid -->
    <action application="set" data="domain_uuid=${domain_uuid}"/>
    <action application="set" data="secretary_extension=$1"/>
    
    <!-- Configurar streaming -->
    <action application="set" data="STREAM_PLAYBACK=true"/>
    <action application="set" data="STREAM_SAMPLE_RATE=16000"/>
    <action application="set" data="STREAM_BUFFER_SIZE=20"/>
    
    <!-- Headers extras (caller_id, etc) -->
    <action application="set" data="STREAM_EXTRA_HEADERS={"caller_id":"${caller_id_number}","extension":"$1"}"/>
    
    <!-- Iniciar streaming ao atender -->
    <action application="set" data="api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/stream/${domain_uuid}/${uuid} mono 16k ${caller_id_number}"/>
    
    <!-- Atender e aguardar -->
    <action application="answer"/>
    <action application="park"/>
  </condition>
</extension>
```

---

## Decision 9: Métricas e Observabilidade

### Prometheus Metrics

```python
from prometheus_client import Counter, Histogram, Gauge

# Contadores
CALLS_TOTAL = Counter(
    'voice_ai_realtime_calls_total',
    'Total de chamadas realtime',
    ['domain_uuid', 'provider', 'outcome']
)

AUDIO_CHUNKS_PROCESSED = Counter(
    'voice_ai_realtime_audio_chunks_total',
    'Total de chunks de áudio processados',
    ['domain_uuid', 'direction']  # direction: inbound/outbound
)

# Histogramas
RESPONSE_LATENCY = Histogram(
    'voice_ai_realtime_response_latency_seconds',
    'Latência de resposta do provider',
    ['domain_uuid', 'provider'],
    buckets=[0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0]
)

CALL_DURATION = Histogram(
    'voice_ai_realtime_call_duration_seconds',
    'Duração das chamadas',
    ['domain_uuid', 'provider'],
    buckets=[30, 60, 120, 180, 300, 600, 900]
)

# Gauges
ACTIVE_SESSIONS = Gauge(
    'voice_ai_realtime_active_sessions',
    'Sessões ativas no momento',
    ['domain_uuid', 'provider']
)
```

### Logging Estruturado

```python
import structlog

logger = structlog.get_logger()

# Log de início de sessão
logger.info(
    "realtime_session_started",
    domain_uuid=domain_uuid,
    call_uuid=call_uuid,
    caller_id=caller_id,
    provider=provider_name,
)

# Log de latência
logger.info(
    "realtime_response_latency",
    domain_uuid=domain_uuid,
    call_uuid=call_uuid,
    latency_ms=latency_ms,
    turn_number=turn_count,
)

# Log de erro
logger.error(
    "realtime_provider_error",
    domain_uuid=domain_uuid,
    call_uuid=call_uuid,
    provider=provider_name,
    error=str(error),
    error_type=type(error).__name__,
)
```

---

## Decision 10: Graceful Degradation

### Fallback Automático

```python
class FallbackHandler:
    """Gerencia fallback entre providers e modos."""
    
    FALLBACK_CHAIN = [
        ("openai", "elevenlabs"),
        ("elevenlabs", "gemini"),
        ("gemini", "custom"),
        ("custom", "turn_based"),  # Fallback final para v1
    ]
    
    async def handle_provider_failure(
        self,
        session: RealtimeSession,
        current_provider: str,
        error: Exception,
    ):
        """Tenta fallback para próximo provider."""
        
        next_provider = self._get_next_provider(current_provider)
        
        if next_provider == "turn_based":
            # Fallback para modo turn-based (v1)
            logger.warning(
                "realtime_fallback_to_turn_based",
                domain_uuid=session.domain_uuid,
                call_uuid=session.call_uuid,
            )
            await self._switch_to_turn_based(session)
            return
        
        logger.warning(
            "realtime_provider_fallback",
            domain_uuid=session.domain_uuid,
            call_uuid=session.call_uuid,
            from_provider=current_provider,
            to_provider=next_provider,
            error=str(error),
        )
        
        # Reconectar com novo provider
        await session.switch_provider(next_provider)
```

---

## Timeline de Implementação

```
Semana 1: Infraestrutura
├── Compilar/instalar mod_audio_stream
├── Estrutura base do realtime bridge
└── Testes de conectividade WebSocket

Semana 2: OpenAI Provider
├── Implementar OpenAIRealtimeProvider
├── Resampling 16k↔24k
├── Testes de latência
└── Function calling básico

Semana 3: Multi-provider
├── ElevenLabsConversationalProvider
├── GeminiLiveProvider
├── Provider factory e fallback
└── Testes comparativos

Semana 4: Integração FusionPBX
├── Páginas PHP de configuração
├── Dialplan XML
├── Database migrations
└── Documentação

Semana 5: Polish
├── Métricas Prometheus
├── Logging estruturado
├── Testes de carga
└── Documentação de API
```

---

## Riscos Técnicos

| Risco | Mitigação |
|-------|-----------|
| mod_audio_stream instável | Fork próprio, testes extensivos |
| Latência de rede variável | Buffer adaptativo, jitter buffer |
| Provider rate limits | Queue com backpressure |
| Memory leaks em sessões longas | Timeouts, garbage collection |
| Resampling introduz artefatos | Usar scipy de alta qualidade |
