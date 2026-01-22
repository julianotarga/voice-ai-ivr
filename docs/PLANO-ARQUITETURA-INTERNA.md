# Plano de Implementação: Arquitetura de Controle Interno

> **Status: ✅ IMPLEMENTADO (Jan/2026)**
> 
> Fases 1-4 concluídas. Sistema em produção.

## Objetivo

Reduzir dependência do FreeSWITCH para controle de estado e eventos, movendo a lógica para código Python que temos 100% de controle.

---

## Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                      RealtimeSession                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐   │
│  │  StateMachine  │  │   EventBus     │  │ HeartbeatMonitor │   │
│  │  (transitions) │  │   (interno)    │  │   (interno)      │   │
│  │                │  │                │  │                  │   │
│  │  - Estados     │  │  - emit()      │  │  - check_health  │   │
│  │  - Transições  │  │  - on()        │  │  - detect_issues │   │
│  │  - Guards      │  │  - wait_for()  │  │  - auto_recover  │   │
│  │  - Callbacks   │  │                │  │                  │   │
│  └───────┬────────┘  └───────┬────────┘  └────────┬─────────┘   │
│          │                   │                     │             │
│          └───────────┬───────┴─────────────────────┘             │
│                      ▼                                           │
│              ┌───────────────────┐                               │
│              │  TimeoutManager   │                               │
│              │  (anyio scopes)   │                               │
│              └───────────────────┘                               │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                    Camada de Abstração                           │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              ESLCommandExecutor                             │  │
│  │                                                             │  │
│  │  - execute_api(cmd) → apenas executa, não decide           │  │
│  │  - Traduz eventos ESL → VoiceEvent                         │  │
│  │  - FreeSWITCH é "burro" - só faz o que mandamos            │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## FASE 1: Infraestrutura Core

### 1.1 `realtime/core/events.py`

Define todos os tipos de eventos internos do sistema.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import time


class VoiceEventType(Enum):
    """Eventos internos do Voice AI - abstraem eventos do FreeSWITCH"""
    
    # ========== CHAMADA ==========
    CALL_STARTED = "call_started"
    CALL_CONNECTED = "call_connected"
    CALL_ENDING = "call_ending"
    CALL_ENDED = "call_ended"
    
    # ========== ÁUDIO - AI ==========
    AI_SPEAKING_STARTED = "ai_speaking_started"
    AI_SPEAKING_DONE = "ai_speaking_done"
    AI_AUDIO_CHUNK = "ai_audio_chunk"           # Chunk de áudio gerado
    AI_AUDIO_BUFFER_LOW = "ai_audio_buffer_low" # Buffer abaixo do threshold
    AI_AUDIO_COMPLETE = "ai_audio_complete"     # Todo áudio reproduzido
    
    # ========== ÁUDIO - USUÁRIO ==========
    USER_SPEAKING_STARTED = "user_speaking_started"
    USER_SPEAKING_DONE = "user_speaking_done"
    USER_AUDIO_RECEIVED = "user_audio_received"
    USER_TRANSCRIPT = "user_transcript"
    USER_DTMF = "user_dtmf"
    
    # ========== TRANSFERÊNCIA ==========
    TRANSFER_REQUESTED = "transfer_requested"     # IA chamou request_handoff
    TRANSFER_VALIDATED = "transfer_validated"     # Destino validado
    TRANSFER_DIALING = "transfer_dialing"         # Discando para atendente
    TRANSFER_RINGING = "transfer_ringing"         # Atendente tocando
    TRANSFER_ANSWERED = "transfer_answered"       # Atendente atendeu
    TRANSFER_ANNOUNCING = "transfer_announcing"   # Falando com atendente
    TRANSFER_ACCEPTED = "transfer_accepted"       # Atendente aceitou
    TRANSFER_REJECTED = "transfer_rejected"       # Atendente recusou
    TRANSFER_TIMEOUT = "transfer_timeout"         # Timeout interno
    TRANSFER_COMPLETED = "transfer_completed"     # Bridge feito com sucesso
    TRANSFER_FAILED = "transfer_failed"           # Falha geral
    TRANSFER_CANCELLED = "transfer_cancelled"     # Cliente desligou durante
    
    # ========== HOLD ==========
    HOLD_STARTED = "hold_started"
    HOLD_ENDED = "hold_ended"
    
    # ========== ESTADO ==========
    STATE_CHANGED = "state_changed"
    STATE_TRANSITION_BLOCKED = "state_transition_blocked"
    
    # ========== CONEXÃO ==========
    CONNECTION_HEALTHY = "connection_healthy"
    CONNECTION_DEGRADED = "connection_degraded"
    CONNECTION_LOST = "connection_lost"
    WEBSOCKET_DISCONNECTED = "websocket_disconnected"
    PROVIDER_TIMEOUT = "provider_timeout"
    
    # ========== FUNÇÃO ==========
    FUNCTION_CALL_STARTED = "function_call_started"
    FUNCTION_CALL_COMPLETED = "function_call_completed"
    FUNCTION_CALL_FAILED = "function_call_failed"


@dataclass
class VoiceEvent:
    """Evento interno do Voice AI"""
    
    type: VoiceEventType
    call_uuid: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = "internal"  # "internal", "esl", "provider", "websocket"
    
    def __repr__(self):
        return f"VoiceEvent({self.type.value}, call={self.call_uuid[:8]}..., data={self.data})"
```

### 1.2 `realtime/core/event_bus.py`

Event Bus assíncrono para comunicação desacoplada.

```python
import asyncio
import logging
from typing import Callable, Dict, List, Optional, Set
from weakref import WeakSet
import anyio

from .events import VoiceEvent, VoiceEventType

logger = logging.getLogger(__name__)


class EventBus:
    """
    Event Bus interno para comunicação desacoplada.
    
    Benefícios:
    - Handlers não precisam conhecer quem emite eventos
    - Fácil adicionar novos listeners
    - Suporte a wait_for() com timeout
    - Thread-safe
    """
    
    def __init__(self, call_uuid: str):
        self.call_uuid = call_uuid
        self._handlers: Dict[VoiceEventType, List[Callable]] = {}
        self._waiters: Dict[VoiceEventType, Set[asyncio.Event]] = {}
        self._lock = asyncio.Lock()
        self._event_history: List[VoiceEvent] = []
        self._max_history = 100
    
    def on(self, event_type: VoiceEventType, handler: Callable) -> 'EventBus':
        """
        Registra handler para tipo de evento.
        
        Args:
            event_type: Tipo do evento
            handler: Função async ou sync a ser chamada
            
        Returns:
            self para permitir chaining
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
            logger.debug(f"Handler registered for {event_type.value}")
        
        return self
    
    def off(self, event_type: VoiceEventType, handler: Callable) -> 'EventBus':
        """Remove handler"""
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass
        return self
    
    def once(self, event_type: VoiceEventType, handler: Callable) -> 'EventBus':
        """Registra handler que executa apenas uma vez"""
        async def wrapper(event: VoiceEvent):
            self.off(event_type, wrapper)
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        
        return self.on(event_type, wrapper)
    
    async def emit(self, event: VoiceEvent) -> None:
        """
        Emite evento para todos os handlers registrados.
        
        Args:
            event: Evento a ser emitido
        """
        # Guardar no histórico
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)
        
        logger.debug(f"Emitting {event.type.value}", extra={
            "call_uuid": self.call_uuid,
            "event_data": event.data
        })
        
        # Executar handlers
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error(f"Error in event handler for {event.type.value}: {e}")
        
        # Notificar waiters
        async with self._lock:
            waiters = self._waiters.get(event.type, set())
            for waiter in list(waiters):
                waiter.set()
    
    async def wait_for(
        self,
        event_type: VoiceEventType,
        timeout: Optional[float] = None,
        condition: Optional[Callable[[VoiceEvent], bool]] = None
    ) -> Optional[VoiceEvent]:
        """
        Aguarda evento com timeout opcional.
        
        Args:
            event_type: Tipo do evento a aguardar
            timeout: Timeout em segundos (None = infinito)
            condition: Função que valida se o evento é o esperado
            
        Returns:
            VoiceEvent se recebido, None se timeout
        """
        event_received = asyncio.Event()
        received_event: Optional[VoiceEvent] = None
        
        async def capture_event(event: VoiceEvent):
            nonlocal received_event
            if condition is None or condition(event):
                received_event = event
                event_received.set()
        
        self.on(event_type, capture_event)
        
        try:
            if timeout:
                with anyio.move_on_after(timeout) as scope:
                    await event_received.wait()
                
                if scope.cancelled_caught:
                    return None
            else:
                await event_received.wait()
            
            return received_event
            
        finally:
            self.off(event_type, capture_event)
    
    async def wait_for_any(
        self,
        event_types: List[VoiceEventType],
        timeout: Optional[float] = None
    ) -> Optional[VoiceEvent]:
        """Aguarda qualquer um dos eventos especificados"""
        event_received = asyncio.Event()
        received_event: Optional[VoiceEvent] = None
        
        async def capture_event(event: VoiceEvent):
            nonlocal received_event
            received_event = event
            event_received.set()
        
        for event_type in event_types:
            self.on(event_type, capture_event)
        
        try:
            if timeout:
                with anyio.move_on_after(timeout) as scope:
                    await event_received.wait()
                
                if scope.cancelled_caught:
                    return None
            else:
                await event_received.wait()
            
            return received_event
            
        finally:
            for event_type in event_types:
                self.off(event_type, capture_event)
    
    def get_history(
        self,
        event_type: Optional[VoiceEventType] = None,
        limit: int = 10
    ) -> List[VoiceEvent]:
        """Retorna histórico de eventos para debug"""
        if event_type:
            filtered = [e for e in self._event_history if e.type == event_type]
        else:
            filtered = self._event_history
        
        return filtered[-limit:]
```

### 1.3 `realtime/core/state_machine.py`

Máquina de estados com suporte a estados hierárquicos.

```python
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

from transitions.extensions.asyncio import AsyncMachine
from transitions.extensions import HierarchicalAsyncMachine

from .events import VoiceEvent, VoiceEventType
from .event_bus import EventBus

logger = logging.getLogger(__name__)


class CallState(Enum):
    """Estados de uma chamada"""
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    LISTENING = "listening"
    SPEAKING = "speaking"
    ON_HOLD = "on_hold"
    
    # Sub-estados de transferência
    TRANSFERRING = "transferring"
    TRANSFER_DIALING = "transfer_dialing"
    TRANSFER_ANNOUNCING = "transfer_announcing"
    TRANSFER_WAITING = "transfer_waiting"
    TRANSFER_ACCEPTED = "transfer_accepted"
    TRANSFER_REJECTED = "transfer_rejected"
    
    BRIDGED = "bridged"
    ENDING = "ending"
    ENDED = "ended"


# Definição de estados hierárquicos para transitions
STATES = [
    'idle',
    'connecting',
    'connected',
    {
        'name': 'active',
        'children': [
            'listening',
            'speaking',
            'processing'
        ],
        'initial': 'listening'
    },
    'on_hold',
    {
        'name': 'transferring',
        'children': [
            'validating',     # Validando destino
            'dialing',        # Discando
            'announcing',     # Falando com atendente
            'waiting',        # Aguardando resposta
            'bridging',       # Fazendo bridge
        ],
        'initial': 'validating'
    },
    'bridged',
    'ending',
    'ended'
]

# Definição de transições
TRANSITIONS = [
    # === Início da chamada ===
    {'trigger': 'start_call', 'source': 'idle', 'dest': 'connecting'},
    {'trigger': 'call_connected', 'source': 'connecting', 'dest': 'active'},
    
    # === Fluxo de conversa ===
    {'trigger': 'user_starts_speaking', 'source': 'active_listening', 'dest': 'active_listening'},
    {'trigger': 'user_stops_speaking', 'source': 'active_listening', 'dest': 'active_processing'},
    {'trigger': 'ai_starts_speaking', 'source': 'active_processing', 'dest': 'active_speaking'},
    {'trigger': 'ai_stops_speaking', 'source': 'active_speaking', 'dest': 'active_listening'},
    
    # === Hold ===
    {
        'trigger': 'hold', 
        'source': 'active', 
        'dest': 'on_hold',
        'before': '_before_hold'
    },
    {
        'trigger': 'unhold', 
        'source': 'on_hold', 
        'dest': 'active',
        'after': '_after_unhold'
    },
    
    # === Transferência ===
    {
        'trigger': 'request_transfer',
        'source': 'active',
        'dest': 'transferring',
        'conditions': ['_can_transfer'],
        'before': '_before_transfer'
    },
    {'trigger': 'destination_validated', 'source': 'transferring_validating', 'dest': 'transferring_dialing'},
    {'trigger': 'attendant_answered', 'source': 'transferring_dialing', 'dest': 'transferring_announcing'},
    {'trigger': 'announcement_done', 'source': 'transferring_announcing', 'dest': 'transferring_waiting'},
    {
        'trigger': 'transfer_accepted',
        'source': 'transferring_waiting',
        'dest': 'transferring_bridging',
        'after': '_after_transfer_accepted'
    },
    {
        'trigger': 'transfer_rejected',
        'source': 'transferring_waiting',
        'dest': 'active',
        'after': '_after_transfer_rejected'
    },
    {
        'trigger': 'transfer_timeout',
        'source': 'transferring',
        'dest': 'active',
        'after': '_after_transfer_timeout'
    },
    {'trigger': 'bridge_complete', 'source': 'transferring_bridging', 'dest': 'bridged'},
    
    # === Fim da chamada ===
    {'trigger': 'end_call', 'source': '*', 'dest': 'ending', 'unless': ['_is_ended']},
    {'trigger': 'call_ended', 'source': 'ending', 'dest': 'ended'},
    {'trigger': 'force_end', 'source': '*', 'dest': 'ended'},
]


class CallStateMachine:
    """
    Máquina de estados para gerenciar ciclo de vida da chamada.
    
    Vantagens:
    - Estados explícitos (não mais flags booleanas)
    - Transições validadas (guards)
    - Callbacks automáticos (before/after)
    - Histórico de transições
    - Suporte async nativo
    """
    
    def __init__(
        self,
        call_uuid: str,
        event_bus: EventBus,
        session: Any  # RealtimeSession
    ):
        self.call_uuid = call_uuid
        self.events = event_bus
        self.session = session
        self._transition_history: List[Dict] = []
        
        # Criar máquina hierárquica async
        self.machine = HierarchicalAsyncMachine(
            model=self,
            states=STATES,
            transitions=TRANSITIONS,
            initial='idle',
            ignore_invalid_triggers=True,
            auto_transitions=False,
            send_event=True  # Passa event_data para callbacks
        )
        
        # Callback global para mudanças de estado
        self.machine.on_enter_state = self._on_state_enter
    
    @property
    def current_state(self) -> str:
        """Estado atual"""
        return self.state
    
    @property
    def is_in_transfer(self) -> bool:
        """Verifica se está em qualquer sub-estado de transferência"""
        return self.state.startswith('transferring')
    
    @property
    def is_active(self) -> bool:
        """Verifica se está em conversa ativa"""
        return self.state.startswith('active')
    
    # ========== GUARDS ==========
    
    def _can_transfer(self, event_data=None) -> bool:
        """Guard: pode iniciar transferência?"""
        # Precisa ter caller_name e destination
        has_name = bool(getattr(self.session, 'caller_name', None))
        has_dest = bool(event_data and event_data.kwargs.get('destination'))
        
        if not has_name:
            logger.warning("Transfer blocked: caller_name not set")
        if not has_dest:
            logger.warning("Transfer blocked: destination not provided")
        
        return has_name and has_dest
    
    def _is_ended(self, event_data=None) -> bool:
        """Guard: já está finalizado?"""
        return self.state == 'ended'
    
    # ========== CALLBACKS ==========
    
    async def _on_state_enter(self, event_data=None):
        """Chamado sempre que entra em um estado"""
        old_state = getattr(event_data, 'transition', {}).source if event_data else 'unknown'
        new_state = self.state
        
        # Registrar histórico
        self._transition_history.append({
            'from': old_state,
            'to': new_state,
            'trigger': event_data.event.name if event_data else 'unknown',
            'timestamp': asyncio.get_event_loop().time()
        })
        
        # Emitir evento
        await self.events.emit(VoiceEvent(
            type=VoiceEventType.STATE_CHANGED,
            call_uuid=self.call_uuid,
            data={
                'old_state': old_state,
                'new_state': new_state,
                'trigger': event_data.event.name if event_data else 'unknown'
            }
        ))
        
        logger.info(
            f"State: {old_state} → {new_state}",
            extra={"call_uuid": self.call_uuid}
        )
    
    async def _before_hold(self, event_data=None):
        """Antes de colocar em hold"""
        await self.events.emit(VoiceEvent(
            type=VoiceEventType.HOLD_STARTED,
            call_uuid=self.call_uuid
        ))
    
    async def _after_unhold(self, event_data=None):
        """Depois de tirar do hold"""
        await self.events.emit(VoiceEvent(
            type=VoiceEventType.HOLD_ENDED,
            call_uuid=self.call_uuid
        ))
    
    async def _before_transfer(self, event_data=None):
        """Antes de iniciar transferência"""
        destination = event_data.kwargs.get('destination') if event_data else None
        reason = event_data.kwargs.get('reason') if event_data else None
        
        await self.events.emit(VoiceEvent(
            type=VoiceEventType.TRANSFER_REQUESTED,
            call_uuid=self.call_uuid,
            data={
                'destination': destination,
                'reason': reason
            }
        ))
    
    async def _after_transfer_accepted(self, event_data=None):
        """Depois que atendente aceitou"""
        await self.events.emit(VoiceEvent(
            type=VoiceEventType.TRANSFER_ACCEPTED,
            call_uuid=self.call_uuid
        ))
    
    async def _after_transfer_rejected(self, event_data=None):
        """Depois que atendente recusou"""
        await self.events.emit(VoiceEvent(
            type=VoiceEventType.TRANSFER_REJECTED,
            call_uuid=self.call_uuid
        ))
    
    async def _after_transfer_timeout(self, event_data=None):
        """Depois de timeout de transferência"""
        await self.events.emit(VoiceEvent(
            type=VoiceEventType.TRANSFER_TIMEOUT,
            call_uuid=self.call_uuid
        ))
    
    def get_history(self, limit: int = 20) -> List[Dict]:
        """Retorna histórico de transições para debug"""
        return self._transition_history[-limit:]
```

### 1.4 `realtime/core/heartbeat.py`

Monitor de saúde da conexão.

```python
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import anyio

from .events import VoiceEvent, VoiceEventType
from .event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class ConnectionHealth:
    """Estado de saúde da conexão"""
    
    # Timestamps
    last_audio_received: float = 0.0
    last_audio_sent: float = 0.0
    last_provider_response: float = 0.0
    last_websocket_ping: float = 0.0
    
    # Métricas
    audio_buffer_bytes: int = 0
    pending_audio_bytes: int = 0
    websocket_latency_ms: float = 0.0
    provider_latency_ms: float = 0.0
    
    # Contadores
    audio_chunks_received: int = 0
    audio_chunks_sent: int = 0
    
    # Estado
    is_healthy: bool = True
    issues: list = field(default_factory=list)


class HeartbeatMonitor:
    """
    Monitor de saúde da conexão.
    
    Detecta problemas ANTES do FreeSWITCH:
    - Silêncio prolongado (caller pode ter desligado)
    - Provider não responde (OpenAI lento)
    - WebSocket instável
    - Buffer de áudio baixo
    """
    
    def __init__(
        self,
        call_uuid: str,
        event_bus: EventBus,
        check_interval: float = 1.0,
        audio_silence_threshold: float = 10.0,
        provider_timeout_threshold: float = 30.0,
        buffer_low_threshold: int = 1280  # 2 chunks de 20ms
    ):
        self.call_uuid = call_uuid
        self.events = event_bus
        self.check_interval = check_interval
        self.audio_silence_threshold = audio_silence_threshold
        self.provider_timeout_threshold = provider_timeout_threshold
        self.buffer_low_threshold = buffer_low_threshold
        
        self.health = ConnectionHealth()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._paused = False
    
    # ========== ATUALIZAÇÕES ==========
    
    def audio_received(self, chunk_size: int = 640):
        """Chamado quando recebe áudio do caller"""
        self.health.last_audio_received = time.time()
        self.health.audio_chunks_received += 1
    
    def audio_sent(self, chunk_size: int = 640):
        """Chamado quando envia áudio para caller"""
        self.health.last_audio_sent = time.time()
        self.health.audio_chunks_sent += 1
    
    def provider_responded(self):
        """Chamado quando OpenAI responde"""
        self.health.last_provider_response = time.time()
    
    def update_buffer(self, pending_bytes: int, buffer_bytes: int = 0):
        """Atualiza métricas de buffer"""
        self.health.pending_audio_bytes = pending_bytes
        self.health.audio_buffer_bytes = buffer_bytes
    
    def update_latency(self, websocket_ms: float = None, provider_ms: float = None):
        """Atualiza métricas de latência"""
        if websocket_ms is not None:
            self.health.websocket_latency_ms = websocket_ms
        if provider_ms is not None:
            self.health.provider_latency_ms = provider_ms
    
    def pause(self):
        """Pausa monitoramento (durante transferência)"""
        self._paused = True
    
    def resume(self):
        """Retoma monitoramento"""
        self._paused = False
        # Resetar timestamps para evitar falsos positivos
        now = time.time()
        self.health.last_audio_received = now
        self.health.last_provider_response = now
    
    # ========== CONTROLE ==========
    
    async def start(self):
        """Inicia monitoramento em background"""
        if self._running:
            return
        
        self._running = True
        self.health.last_audio_received = time.time()
        self.health.last_provider_response = time.time()
        
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"HeartbeatMonitor started for {self.call_uuid}")
    
    async def stop(self):
        """Para monitoramento"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        logger.info(f"HeartbeatMonitor stopped for {self.call_uuid}")
    
    # ========== MONITORAMENTO ==========
    
    async def _monitor_loop(self):
        """Loop principal de monitoramento"""
        while self._running:
            try:
                if not self._paused:
                    await self._check_health()
                
                await anyio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"HeartbeatMonitor error: {e}")
    
    async def _check_health(self):
        """Verifica saúde da conexão"""
        now = time.time()
        issues = []
        
        # 1. Verificar áudio recebido do caller
        if self.health.last_audio_received > 0:
            audio_gap = now - self.health.last_audio_received
            if audio_gap > self.audio_silence_threshold:
                issues.append(f"no_audio_for_{audio_gap:.1f}s")
                
                await self.events.emit(VoiceEvent(
                    type=VoiceEventType.CONNECTION_DEGRADED,
                    call_uuid=self.call_uuid,
                    data={
                        "reason": "audio_silence",
                        "gap_seconds": audio_gap,
                        "threshold": self.audio_silence_threshold
                    }
                ))
        
        # 2. Verificar resposta do provider
        if self.health.last_provider_response > 0:
            provider_gap = now - self.health.last_provider_response
            if provider_gap > self.provider_timeout_threshold:
                issues.append(f"provider_silent_{provider_gap:.1f}s")
                
                await self.events.emit(VoiceEvent(
                    type=VoiceEventType.PROVIDER_TIMEOUT,
                    call_uuid=self.call_uuid,
                    data={
                        "gap_seconds": provider_gap,
                        "threshold": self.provider_timeout_threshold
                    }
                ))
        
        # 3. Verificar buffer de áudio
        if self.health.pending_audio_bytes < self.buffer_low_threshold:
            if self.health.pending_audio_bytes > 0:  # Só se estava falando
                await self.events.emit(VoiceEvent(
                    type=VoiceEventType.AI_AUDIO_BUFFER_LOW,
                    call_uuid=self.call_uuid,
                    data={
                        "buffer_bytes": self.health.pending_audio_bytes,
                        "threshold": self.buffer_low_threshold
                    }
                ))
        
        # Atualizar estado
        self.health.issues = issues
        self.health.is_healthy = len(issues) == 0
        
        if issues:
            logger.warning(
                f"Connection health issues: {issues}",
                extra={"call_uuid": self.call_uuid}
            )
```

### 1.5 `realtime/core/timeout_manager.py`

Gerenciador de timeouts interno.

```python
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Callable, Optional, List
from dataclasses import dataclass

import anyio

from .events import VoiceEvent, VoiceEventType
from .event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class TimeoutConfig:
    """Configuração de timeouts"""
    
    # Transferência
    transfer_dial_timeout: float = 30.0       # Tempo para atendente atender
    transfer_response_timeout: float = 60.0   # Tempo para aceitar/recusar
    transfer_announcement_timeout: float = 30.0  # Tempo do anúncio
    
    # Áudio
    audio_playback_timeout: float = 10.0      # Tempo máximo de playback
    audio_generation_timeout: float = 30.0    # Tempo para OpenAI gerar resposta
    
    # Chamada
    call_idle_timeout: float = 30.0           # Silêncio antes de encerrar
    call_max_duration: float = 600.0          # 10 minutos máximo


class TimeoutManager:
    """
    Gerenciador de timeouts internos.
    
    Vantagens sobre depender do ESL:
    - Não precisa esperar evento do FreeSWITCH
    - Controle granular por operação
    - Fácil cancelar/estender timeouts
    """
    
    def __init__(
        self,
        call_uuid: str,
        event_bus: EventBus,
        config: Optional[TimeoutConfig] = None
    ):
        self.call_uuid = call_uuid
        self.events = event_bus
        self.config = config or TimeoutConfig()
        
        self._active_timeouts: dict = {}
    
    @asynccontextmanager
    async def timeout_scope(
        self,
        name: str,
        seconds: float,
        on_timeout: Optional[Callable] = None
    ):
        """
        Context manager para timeout com callback opcional.
        
        Uso:
            async with timeout_mgr.timeout_scope("dial", 30) as scope:
                await dial_attendant()
            
            if scope.cancelled_caught:
                # Timeout!
                pass
        """
        self._active_timeouts[name] = seconds
        
        try:
            with anyio.move_on_after(seconds) as scope:
                yield scope
            
            if scope.cancelled_caught:
                logger.info(
                    f"Timeout '{name}' reached after {seconds}s",
                    extra={"call_uuid": self.call_uuid}
                )
                
                if on_timeout:
                    if asyncio.iscoroutinefunction(on_timeout):
                        await on_timeout()
                    else:
                        on_timeout()
        finally:
            self._active_timeouts.pop(name, None)
    
    async def wait_for_transfer_response(
        self,
        timeout: Optional[float] = None
    ) -> str:
        """
        Aguarda resposta de transferência com timeout interno.
        
        Returns:
            "accepted", "rejected", ou "timeout"
        """
        timeout = timeout or self.config.transfer_response_timeout
        
        # Aguardar qualquer um dos eventos
        event = await self.events.wait_for_any(
            [
                VoiceEventType.TRANSFER_ACCEPTED,
                VoiceEventType.TRANSFER_REJECTED,
            ],
            timeout=timeout
        )
        
        if event is None:
            await self.events.emit(VoiceEvent(
                type=VoiceEventType.TRANSFER_TIMEOUT,
                call_uuid=self.call_uuid,
                data={"reason": "no_response", "timeout": timeout}
            ))
            return "timeout"
        
        if event.type == VoiceEventType.TRANSFER_ACCEPTED:
            return "accepted"
        else:
            return "rejected"
    
    async def wait_for_audio_complete(
        self,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Aguarda áudio terminar de tocar com timeout.
        
        Returns:
            True se completou, False se timeout
        """
        timeout = timeout or self.config.audio_playback_timeout
        
        event = await self.events.wait_for(
            VoiceEventType.AI_AUDIO_COMPLETE,
            timeout=timeout
        )
        
        return event is not None
    
    async def wait_for_dial_answer(
        self,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Aguarda atendente atender com timeout.
        
        Returns:
            True se atendeu, False se timeout/não atendeu
        """
        timeout = timeout or self.config.transfer_dial_timeout
        
        event = await self.events.wait_for_any(
            [
                VoiceEventType.TRANSFER_ANSWERED,
                VoiceEventType.TRANSFER_FAILED,
            ],
            timeout=timeout
        )
        
        if event is None:
            return False
        
        return event.type == VoiceEventType.TRANSFER_ANSWERED
    
    def cancel_all(self):
        """Cancela todos os timeouts ativos"""
        self._active_timeouts.clear()
```

### 1.6 `realtime/core/__init__.py`

```python
"""
Core module - Controle interno de estado e eventos.

Este módulo contém a infraestrutura para controle de chamadas
sem dependência direta do FreeSWITCH para lógica de negócio.
"""

from .events import VoiceEvent, VoiceEventType
from .event_bus import EventBus
from .state_machine import CallStateMachine, CallState
from .heartbeat import HeartbeatMonitor, ConnectionHealth
from .timeout_manager import TimeoutManager, TimeoutConfig

__all__ = [
    # Eventos
    'VoiceEvent',
    'VoiceEventType',
    'EventBus',
    
    # Estado
    'CallStateMachine',
    'CallState',
    
    # Monitoramento
    'HeartbeatMonitor',
    'ConnectionHealth',
    
    # Timeouts
    'TimeoutManager',
    'TimeoutConfig',
]
```

---

## FASE 2: Integração com RealtimeSession

### 2.1 Inicialização

Adicionar ao `__init__` do `RealtimeSession`:

```python
from .core import (
    EventBus, 
    CallStateMachine, 
    HeartbeatMonitor,
    TimeoutManager,
    VoiceEvent,
    VoiceEventType
)

class RealtimeSession:
    def __init__(self, config: RealtimeSessionConfig, ...):
        # ... código existente ...
        
        # Novos componentes de controle interno
        self.events = EventBus(self.call_uuid)
        self.state_machine = CallStateMachine(
            call_uuid=self.call_uuid,
            event_bus=self.events,
            session=self
        )
        self.heartbeat = HeartbeatMonitor(
            call_uuid=self.call_uuid,
            event_bus=self.events
        )
        self.timeouts = TimeoutManager(
            call_uuid=self.call_uuid,
            event_bus=self.events
        )
        
        # Registrar handlers internos
        self._register_internal_handlers()
    
    def _register_internal_handlers(self):
        """Registra handlers para eventos internos"""
        self.events.on(VoiceEventType.TRANSFER_TIMEOUT, self._on_transfer_timeout)
        self.events.on(VoiceEventType.CONNECTION_DEGRADED, self._on_connection_issue)
        self.events.on(VoiceEventType.AI_AUDIO_COMPLETE, self._on_audio_complete)
```

### 2.2 Substituir Flags por Estados

Antes:
```python
self._transfer_in_progress = True
self._on_hold = True
self._handoff_pending = True
```

Depois:
```python
await self.state_machine.request_transfer(destination=dest, reason=reason)
# Estado agora é 'transferring_validating'

await self.state_machine.hold()
# Estado agora é 'on_hold'
```

---

## FASE 3: Refatorar TransferManager

### 3.1 Emitir Eventos

```python
# Antes
logger.info("Attendant answered")
# código de lógica...

# Depois
await self.events.emit(VoiceEvent(
    type=VoiceEventType.TRANSFER_ANSWERED,
    call_uuid=self.call_uuid,
    data={"b_leg_uuid": b_leg_uuid}
))
# Lógica movida para handler do evento
```

### 3.2 Usar Timeouts Internos

```python
# Antes
event = await self._esl.wait_for_event(
    ["CHANNEL_ANSWER", "CHANNEL_HANGUP"],
    uuid=b_leg_uuid,
    timeout=30
)

# Depois
answered = await self.timeouts.wait_for_dial_answer(timeout=30)
if not answered:
    await self.state_machine.transfer_timeout()
```

---

## FASE 4: Desacoplar ESL

### 4.1 ESLCommandExecutor

```python
class ESLCommandExecutor:
    """
    Executor de comandos ESL.
    
    IMPORTANTE: Esta classe apenas EXECUTA comandos.
    Não contém lógica de negócio.
    """
    
    async def pause_audio_stream(self, uuid: str) -> bool:
        """Pausa captura de áudio"""
        result = await self.execute_api(f"uuid_audio_stream {uuid} pause")
        return "+OK" in str(result)
    
    async def resume_audio_stream(self, uuid: str) -> bool:
        """Retoma captura de áudio"""
        result = await self.execute_api(f"uuid_audio_stream {uuid} resume")
        return "+OK" in str(result)
    
    async def originate(self, dial_string: str, app: str, **kwargs) -> dict:
        """Origina chamada"""
        # Apenas executa, não decide
        pass
    
    async def bridge(self, uuid1: str, uuid2: str) -> bool:
        """Faz bridge entre canais"""
        pass
```

---

## Dependências

**Nenhuma dependência externa necessária!**

A implementação usa apenas bibliotecas padrão do Python:
- `asyncio` - para operações assíncronas e timeouts
- `dataclasses` - para estruturas de dados
- `enum` - para tipos de eventos e estados
- `typing` - para type hints

> **Nota:** O plano original sugeria `transitions` e `anyio`, mas optamos por
> implementação customizada para evitar dependências externas e ter controle total.

---

## Status da Implementação

| Fase | Descrição | Status | Data |
|------|-----------|--------|------|
| 1 | Infraestrutura Core | ✅ Completo | Jan/2026 |
| 2 | Integração Session | ✅ Completo | Jan/2026 |
| 3 | Refatorar Transfer | ✅ Completo | Jan/2026 |
| 4 | Desacoplar ESL | ✅ Infraestrutura existe | Jan/2026 |
| 5 | Testes | ⏳ Pendente | - |

---

## Ganhos Obtidos

### Comparativo Antes x Depois

| Aspecto | Antes (FreeSWITCH controlando) | Depois (Python controlando) |
|---------|-------------------------------|----------------------------|
| **Fonte da verdade** | Eventos ESL espalhados | `CallStateMachine` centralizada |
| **Detecção de problemas** | Esperar ESL HANGUP (~5s delay) | `HeartbeatMonitor` proativo (~1s) |
| **Comunicação entre módulos** | Callbacks acoplados | `EventBus` desacoplado |
| **Controle de timeouts** | FreeSWITCH timers | `TimeoutManager` interno |
| **Debug** | Logs dispersos | Logs estruturados com emojis |
| **Extensibilidade** | Difícil adicionar features | Fácil (eventos tipados) |

### Benefícios Práticos

1. **Detecção proativa de problemas:**
   - OpenAI lento → `PROVIDER_TIMEOUT` antes de timeout genérico
   - Caller desligou → `CONNECTION_DEGRADED` antes de ESL HANGUP
   - Transferência travou → `TRANSFER_TIMEOUT` interno

2. **Estado consistente:**
   - Guards impedem transições inválidas
   - Histórico de transições para debug
   - Impossível chegar em estado inconsistente

3. **Logs estruturados:**
   - Identificação visual por emoji (📢 🔄 💓 ⏱️ 📞 ⚠️)
   - Filtro fácil por componente
   - Fluxo completo visível

4. **Observabilidade:**
   - `EventBus.get_history()` para debug
   - `StateMachine.history` para auditoria
   - Métricas de saúde via `HeartbeatMonitor`

---

## Riscos e Mitigações

| Risco | Mitigação | Status |
|-------|-----------|--------|
| Regressão em funcionalidades | Implementação gradual, código preservado | ✅ Mitigado |
| Performance do Event Bus | Histórico limitado a 100 eventos | ✅ Mitigado |
| Deadlocks em estado | Timeouts internos, transições validadas | ✅ Mitigado |
| Sincronização eventos/estados | Handlers automáticos sincronizam | ✅ Mitigado |

---

## Arquivos Criados/Modificados

### Novos (FASE 1)
- `realtime/core/__init__.py`
- `realtime/core/events.py` - VoiceEventType, VoiceEvent
- `realtime/core/event_bus.py` - EventBus (pub/sub async)
- `realtime/core/state_machine.py` - CallStateMachine, CallState
- `realtime/core/heartbeat.py` - HeartbeatMonitor
- `realtime/core/timeout_manager.py` - TimeoutManager

### Modificados (FASES 2-4)
- `realtime/session.py` - Integração com core, handlers de eventos
- `realtime/handlers/transfer_manager_conference.py` - Emissão de eventos
- `realtime/providers/openai_realtime.py` - Logs agregados
- `realtime/server.py` - Logs otimizados
- `realtime/utils/echo_canceller.py` - Logs reduzidos

---

## Próximos Passos (Opcional)

1. **Testes unitários** para componentes core
2. **Métricas Prometheus** baseadas em eventos
3. **Reconexão automática** ao provider usando HeartbeatMonitor
4. **Fallback de provider** usando estado da StateMachine
