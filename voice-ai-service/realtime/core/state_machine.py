"""
CallStateMachine - Máquina de estados para chamadas.

Gerencia o ciclo de vida de uma chamada com estados explícitos,
transições validadas (guards) e callbacks automáticos.

Implementação async nativa sem dependências externas.

Referência: voice-ai-ivr/docs/PLANO-ARQUITETURA-INTERNA.md
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Callable
from enum import Enum
from dataclasses import dataclass, field
import time

from .events import VoiceEvent, VoiceEventType
from .event_bus import EventBus

logger = logging.getLogger(__name__)


class CallState(Enum):
    """
    Estados possíveis de uma chamada.
    
    Estados hierárquicos são representados com underscore:
    - TRANSFERRING_DIALING = estado 'dialing' dentro de 'transferring'
    """
    
    # Estados principais
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    
    # Estados de conversa ativa
    LISTENING = "listening"
    SPEAKING = "speaking"
    PROCESSING = "processing"
    
    # Estados de espera
    ON_HOLD = "on_hold"
    
    # Estados de transferência (hierárquicos)
    TRANSFERRING = "transferring"
    TRANSFERRING_VALIDATING = "transferring_validating"
    TRANSFERRING_DIALING = "transferring_dialing"
    TRANSFERRING_ANNOUNCING = "transferring_announcing"
    TRANSFERRING_WAITING = "transferring_waiting"
    TRANSFERRING_BRIDGING = "transferring_bridging"
    
    # Estados finais
    BRIDGED = "bridged"
    ENDING = "ending"
    ENDED = "ended"


@dataclass
class StateTransition:
    """Registro de uma transição de estado"""
    from_state: str
    to_state: str
    trigger: str
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)


class CallStateMachine:
    """
    Máquina de estados para gerenciar ciclo de vida da chamada.
    
    Implementação simplificada sem dependência externa (transitions).
    Pode ser substituída pela biblioteca 'transitions' se necessário.
    
    Vantagens:
    - Estados explícitos (não mais flags booleanas)
    - Transições validadas (guards)
    - Callbacks automáticos (before/after)
    - Histórico de transições
    - Suporte async nativo
    
    Uso:
        sm = CallStateMachine(call_uuid, event_bus, session)
        await sm.connect()  # idle -> connecting
        await sm.connected()  # connecting -> connected
        await sm.start_listening()  # connected -> listening
    """
    
    # Definição de transições permitidas
    # Formato: (estado_atual, trigger) -> estado_destino
    TRANSITIONS = {
        # Início da chamada
        (CallState.IDLE, "connect"): CallState.CONNECTING,
        (CallState.CONNECTING, "connected"): CallState.CONNECTED,
        (CallState.CONNECTED, "start_listening"): CallState.LISTENING,
        
        # Fluxo de conversa
        (CallState.LISTENING, "ai_start_speaking"): CallState.SPEAKING,
        (CallState.SPEAKING, "ai_stop_speaking"): CallState.LISTENING,
        (CallState.LISTENING, "processing"): CallState.PROCESSING,
        (CallState.PROCESSING, "ai_start_speaking"): CallState.SPEAKING,
        (CallState.PROCESSING, "done_processing"): CallState.LISTENING,
        
        # Hold (de qualquer estado ativo)
        (CallState.LISTENING, "hold"): CallState.ON_HOLD,
        (CallState.SPEAKING, "hold"): CallState.ON_HOLD,
        (CallState.PROCESSING, "hold"): CallState.ON_HOLD,
        (CallState.ON_HOLD, "unhold"): CallState.LISTENING,
        
        # Transferência (de qualquer estado ativo)
        (CallState.LISTENING, "request_transfer"): CallState.TRANSFERRING_VALIDATING,
        (CallState.SPEAKING, "request_transfer"): CallState.TRANSFERRING_VALIDATING,
        (CallState.PROCESSING, "request_transfer"): CallState.TRANSFERRING_VALIDATING,
        (CallState.TRANSFERRING_VALIDATING, "destination_validated"): CallState.TRANSFERRING_DIALING,
        (CallState.TRANSFERRING_VALIDATING, "validation_failed"): CallState.LISTENING,
        (CallState.TRANSFERRING_DIALING, "attendant_answered"): CallState.TRANSFERRING_ANNOUNCING,
        (CallState.TRANSFERRING_DIALING, "dial_failed"): CallState.LISTENING,
        (CallState.TRANSFERRING_DIALING, "dial_timeout"): CallState.LISTENING,
        (CallState.TRANSFERRING_ANNOUNCING, "announcement_done"): CallState.TRANSFERRING_WAITING,
        (CallState.TRANSFERRING_WAITING, "transfer_accepted"): CallState.TRANSFERRING_BRIDGING,
        (CallState.TRANSFERRING_WAITING, "transfer_rejected"): CallState.LISTENING,
        (CallState.TRANSFERRING_WAITING, "transfer_timeout"): CallState.LISTENING,
        (CallState.TRANSFERRING_BRIDGING, "bridge_complete"): CallState.BRIDGED,
        (CallState.TRANSFERRING_BRIDGING, "bridge_failed"): CallState.LISTENING,
        
        # Cancelamento de transferência (de qualquer sub-estado)
        (CallState.TRANSFERRING_VALIDATING, "cancel_transfer"): CallState.LISTENING,
        (CallState.TRANSFERRING_DIALING, "cancel_transfer"): CallState.LISTENING,
        (CallState.TRANSFERRING_ANNOUNCING, "cancel_transfer"): CallState.LISTENING,
        (CallState.TRANSFERRING_WAITING, "cancel_transfer"): CallState.LISTENING,
        
        # Fim da chamada (de qualquer estado)
        (CallState.LISTENING, "end_call"): CallState.ENDING,
        (CallState.SPEAKING, "end_call"): CallState.ENDING,
        (CallState.PROCESSING, "end_call"): CallState.ENDING,
        (CallState.ON_HOLD, "end_call"): CallState.ENDING,
        (CallState.CONNECTED, "end_call"): CallState.ENDING,
        (CallState.BRIDGED, "end_call"): CallState.ENDING,
        (CallState.TRANSFERRING_VALIDATING, "end_call"): CallState.ENDING,
        (CallState.TRANSFERRING_DIALING, "end_call"): CallState.ENDING,
        (CallState.TRANSFERRING_ANNOUNCING, "end_call"): CallState.ENDING,
        (CallState.TRANSFERRING_WAITING, "end_call"): CallState.ENDING,
        (CallState.TRANSFERRING_BRIDGING, "end_call"): CallState.ENDING,
        (CallState.ENDING, "call_ended"): CallState.ENDED,
        
        # Fim forçado (de qualquer estado)
        (CallState.IDLE, "force_end"): CallState.ENDED,
        (CallState.CONNECTING, "force_end"): CallState.ENDED,
        (CallState.CONNECTED, "force_end"): CallState.ENDED,
        (CallState.LISTENING, "force_end"): CallState.ENDED,
        (CallState.SPEAKING, "force_end"): CallState.ENDED,
        (CallState.PROCESSING, "force_end"): CallState.ENDED,
        (CallState.ON_HOLD, "force_end"): CallState.ENDED,
        (CallState.TRANSFERRING_VALIDATING, "force_end"): CallState.ENDED,
        (CallState.TRANSFERRING_DIALING, "force_end"): CallState.ENDED,
        (CallState.TRANSFERRING_ANNOUNCING, "force_end"): CallState.ENDED,
        (CallState.TRANSFERRING_WAITING, "force_end"): CallState.ENDED,
        (CallState.TRANSFERRING_BRIDGING, "force_end"): CallState.ENDED,
        (CallState.BRIDGED, "force_end"): CallState.ENDED,
        (CallState.ENDING, "force_end"): CallState.ENDED,
    }
    
    def __init__(
        self,
        call_uuid: str,
        event_bus: EventBus,
        session: Any = None  # RealtimeSession (opcional para evitar import circular)
    ):
        """
        Inicializa máquina de estados.
        
        Args:
            call_uuid: UUID da chamada
            event_bus: EventBus para emitir eventos
            session: RealtimeSession (para acessar dados em guards)
        """
        self.call_uuid = call_uuid
        self.events = event_bus
        self.session = session
        
        self._state = CallState.IDLE
        self._history: List[StateTransition] = []
        self._guards: Dict[str, Callable] = {}
        self._before_callbacks: Dict[str, List[Callable]] = {}
        self._after_callbacks: Dict[str, List[Callable]] = {}
        
        # Registrar guards padrão
        self._register_default_guards()
    
    @property
    def state(self) -> CallState:
        """Estado atual"""
        return self._state
    
    @property
    def state_name(self) -> str:
        """Nome do estado atual"""
        return self._state.value
    
    @property
    def is_transferring(self) -> bool:
        """Verifica se está em qualquer estado de transferência"""
        return self._state.value.startswith("transferring")
    
    @property
    def is_active(self) -> bool:
        """Verifica se está em conversa ativa"""
        return self._state in [
            CallState.LISTENING,
            CallState.SPEAKING,
            CallState.PROCESSING
        ]
    
    @property
    def is_ended(self) -> bool:
        """Verifica se chamada terminou"""
        return self._state in [CallState.ENDING, CallState.ENDED]
    
    def _register_default_guards(self):
        """Registra guards padrão"""
        
        # Guard para transferência: precisa ter dados necessários
        def can_transfer(trigger: str, data: Dict) -> bool:
            if not self.session:
                return True  # Sem session, permite
            
            # Verificar se tem caller_name
            caller_name = getattr(self.session, 'caller_name', None)
            if not caller_name:
                logger.warning(
                    "Transfer blocked: caller_name not set",
                    extra={"call_uuid": self.call_uuid}
                )
                return False
            
            # Verificar se tem destination
            destination = data.get('destination')
            if not destination:
                logger.warning(
                    "Transfer blocked: destination not provided",
                    extra={"call_uuid": self.call_uuid}
                )
                return False
            
            return True
        
        self._guards["request_transfer"] = can_transfer
    
    def add_guard(self, trigger: str, guard: Callable[[str, Dict], bool]) -> None:
        """
        Adiciona guard para trigger.
        
        Args:
            trigger: Nome do trigger
            guard: Função que retorna True se transição é permitida
        """
        self._guards[trigger] = guard
    
    def before(self, trigger: str, callback: Callable) -> None:
        """
        Adiciona callback executado ANTES da transição.
        
        Args:
            trigger: Nome do trigger
            callback: Função a executar
        """
        if trigger not in self._before_callbacks:
            self._before_callbacks[trigger] = []
        self._before_callbacks[trigger].append(callback)
    
    def after(self, trigger: str, callback: Callable) -> None:
        """
        Adiciona callback executado APÓS a transição.
        
        Args:
            trigger: Nome do trigger
            callback: Função a executar
        """
        if trigger not in self._after_callbacks:
            self._after_callbacks[trigger] = []
        self._after_callbacks[trigger].append(callback)
    
    async def trigger(self, trigger_name: str, **data) -> bool:
        """
        Executa trigger para transição de estado.
        
        Args:
            trigger_name: Nome do trigger
            **data: Dados adicionais para guards e callbacks
            
        Returns:
            True se transição ocorreu, False se bloqueada
        """
        key = (self._state, trigger_name)
        
        # Verificar se transição existe
        if key not in self.TRANSITIONS:
            logger.debug(
                f"No transition for '{trigger_name}' from state '{self._state.value}'",
                extra={"call_uuid": self.call_uuid}
            )
            return False
        
        # Verificar guard
        if trigger_name in self._guards:
            guard = self._guards[trigger_name]
            try:
                allowed = guard(trigger_name, data)
                if asyncio.iscoroutine(allowed):
                    allowed = await allowed
                
                if not allowed:
                    logger.info(
                        f"Transition '{trigger_name}' blocked by guard",
                        extra={"call_uuid": self.call_uuid}
                    )
                    
                    await self.events.emit(VoiceEvent(
                        type=VoiceEventType.STATE_TRANSITION_BLOCKED,
                        call_uuid=self.call_uuid,
                        data={
                            "trigger": trigger_name,
                            "from_state": self._state.value,
                            "reason": "guard_blocked"
                        }
                    ))
                    return False
                    
            except Exception as e:
                logger.error(f"Guard error for '{trigger_name}': {e}")
                return False
        
        # Estado de destino
        target_state = self.TRANSITIONS[key]
        old_state = self._state
        
        # Executar callbacks BEFORE
        for callback in self._before_callbacks.get(trigger_name, []):
            try:
                result = callback(old_state, target_state, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Before callback error: {e}")
        
        # Fazer transição
        self._state = target_state
        
        # Registrar histórico
        transition = StateTransition(
            from_state=old_state.value,
            to_state=target_state.value,
            trigger=trigger_name,
            data=data
        )
        self._history.append(transition)
        
        logger.info(
            f"State: {old_state.value} --[{trigger_name}]--> {target_state.value}",
            extra={"call_uuid": self.call_uuid}
        )
        
        # Emitir evento
        await self.events.emit(VoiceEvent(
            type=VoiceEventType.STATE_CHANGED,
            call_uuid=self.call_uuid,
            data={
                "old_state": old_state.value,
                "new_state": target_state.value,
                "trigger": trigger_name,
                **data
            }
        ))
        
        # Executar callbacks AFTER
        for callback in self._after_callbacks.get(trigger_name, []):
            try:
                result = callback(old_state, target_state, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"After callback error: {e}")
        
        return True
    
    # ========================================
    # MÉTODOS DE CONVENIÊNCIA
    # ========================================
    
    async def connect(self) -> bool:
        """idle -> connecting"""
        return await self.trigger("connect")
    
    async def connected(self) -> bool:
        """connecting -> connected"""
        return await self.trigger("connected")
    
    async def start_listening(self) -> bool:
        """connected -> listening"""
        return await self.trigger("start_listening")
    
    async def ai_start_speaking(self) -> bool:
        """listening -> speaking"""
        return await self.trigger("ai_start_speaking")
    
    async def ai_stop_speaking(self) -> bool:
        """speaking -> listening"""
        return await self.trigger("ai_stop_speaking")
    
    async def hold(self) -> bool:
        """listening/speaking -> on_hold"""
        return await self.trigger("hold")
    
    async def unhold(self) -> bool:
        """on_hold -> listening"""
        return await self.trigger("unhold")
    
    async def request_transfer(self, destination: str, reason: str = None, caller_name: str = None) -> bool:
        """listening/speaking -> transferring_validating"""
        return await self.trigger(
            "request_transfer",
            destination=destination,
            reason=reason,
            caller_name=caller_name
        )
    
    async def destination_validated(self, destination: Any = None) -> bool:
        """transferring_validating -> transferring_dialing"""
        return await self.trigger("destination_validated", destination=destination)
    
    async def attendant_answered(self, b_leg_uuid: str = None) -> bool:
        """transferring_dialing -> transferring_announcing"""
        return await self.trigger("attendant_answered", b_leg_uuid=b_leg_uuid)
    
    async def announcement_done(self) -> bool:
        """transferring_announcing -> transferring_waiting"""
        return await self.trigger("announcement_done")
    
    async def transfer_accepted(self) -> bool:
        """transferring_waiting -> transferring_bridging"""
        return await self.trigger("transfer_accepted")
    
    async def transfer_rejected(self, reason: str = None) -> bool:
        """transferring_waiting -> listening"""
        return await self.trigger("transfer_rejected", reason=reason)
    
    async def transfer_timeout(self) -> bool:
        """transferring_* -> listening"""
        return await self.trigger("transfer_timeout")
    
    async def bridge_complete(self) -> bool:
        """transferring_bridging -> bridged"""
        return await self.trigger("bridge_complete")
    
    async def cancel_transfer(self, reason: str = None) -> bool:
        """transferring_* -> listening"""
        return await self.trigger("cancel_transfer", reason=reason)
    
    async def end_call(self, reason: str = None) -> bool:
        """* -> ending"""
        return await self.trigger("end_call", reason=reason)
    
    async def call_ended(self) -> bool:
        """ending -> ended"""
        return await self.trigger("call_ended")
    
    async def force_end(self, reason: str = None) -> bool:
        """* -> ended"""
        return await self.trigger("force_end", reason=reason)
    
    # ========================================
    # DEBUG
    # ========================================
    
    def get_history(self, limit: int = 20) -> List[Dict]:
        """Retorna histórico de transições para debug"""
        return [
            {
                "from": t.from_state,
                "to": t.to_state,
                "trigger": t.trigger,
                "timestamp": t.timestamp,
                "data": t.data
            }
            for t in self._history[-limit:]
        ]
    
    def get_available_triggers(self) -> List[str]:
        """Retorna triggers disponíveis no estado atual"""
        return [
            trigger
            for (state, trigger) in self.TRANSITIONS.keys()
            if state == self._state
        ]
