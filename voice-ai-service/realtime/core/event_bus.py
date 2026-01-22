"""
EventBus - Sistema de publicação/assinatura de eventos.

Permite comunicação desacoplada entre componentes do Voice AI.
Handlers podem reagir a eventos sem conhecer quem os emite.

Referência: voice-ai-ivr/docs/PLANO-ARQUITETURA-INTERNA.md
"""

import asyncio
import logging
from typing import Callable, Dict, List, Optional, Any

from .events import VoiceEvent, VoiceEventType

logger = logging.getLogger(__name__)


class EventBus:
    """
    Event Bus assíncrono para comunicação entre componentes.
    
    Funcionalidades:
    - on(event_type, handler): Registra handler
    - off(event_type, handler): Remove handler
    - once(event_type, handler): Handler executado uma vez
    - emit(event): Emite evento para handlers
    - wait_for(event_type, timeout): Aguarda evento
    - wait_for_any(event_types, timeout): Aguarda qualquer evento da lista
    
    Thread-safe e async-native.
    """
    
    def __init__(self, call_uuid: str):
        """
        Inicializa EventBus para uma chamada.
        
        Args:
            call_uuid: UUID da chamada (para logging)
        """
        self.call_uuid = call_uuid
        self._handlers: Dict[VoiceEventType, List[Callable]] = {}
        self._lock = asyncio.Lock()
        self._event_history: List[VoiceEvent] = []
        self._max_history = 100
        self._closed = False
    
    def on(self, event_type: VoiceEventType, handler: Callable) -> 'EventBus':
        """
        Registra handler para tipo de evento.
        
        O handler pode ser sync ou async.
        
        Args:
            event_type: Tipo do evento
            handler: Função a ser chamada quando evento ocorrer
            
        Returns:
            self para permitir chaining: bus.on(A, h1).on(B, h2)
        """
        if self._closed:
            logger.warning(f"EventBus closed, ignoring handler registration for {event_type.value}")
            return self
        
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
            logger.debug(
                f"Handler registered for {event_type.value}",
                extra={"call_uuid": self.call_uuid}
            )
        
        return self
    
    def off(self, event_type: VoiceEventType, handler: Callable) -> 'EventBus':
        """
        Remove handler.
        
        Args:
            event_type: Tipo do evento
            handler: Handler a remover
            
        Returns:
            self para chaining
        """
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
                logger.debug(
                    f"Handler removed for {event_type.value}",
                    extra={"call_uuid": self.call_uuid}
                )
            except ValueError:
                pass  # Handler não estava registrado
        
        return self
    
    def once(self, event_type: VoiceEventType, handler: Callable) -> 'EventBus':
        """
        Registra handler que executa apenas uma vez.
        
        Após a primeira execução, o handler é automaticamente removido.
        
        Args:
            event_type: Tipo do evento
            handler: Handler a executar uma vez
            
        Returns:
            self para chaining
        """
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
        
        Handlers são executados em sequência.
        Erros em handlers são logados mas não propagados.
        
        Args:
            event: Evento a emitir
        """
        if self._closed:
            return
        
        # Guardar no histórico
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)
        
        logger.debug(
            f"Emitting {event.type.value}",
            extra={
                "call_uuid": self.call_uuid,
                "event_data": str(event.data)[:100]
            }
        )
        
        # Executar handlers
        handlers = self._handlers.get(event.type, []).copy()
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error(
                    f"Error in event handler for {event.type.value}: {e}",
                    extra={"call_uuid": self.call_uuid},
                    exc_info=True
                )
    
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
            
        Example:
            # Aguardar qualquer TRANSFER_ACCEPTED
            event = await bus.wait_for(VoiceEventType.TRANSFER_ACCEPTED, timeout=30)
            
            # Aguardar DTMF específico
            event = await bus.wait_for(
                VoiceEventType.USER_DTMF,
                timeout=10,
                condition=lambda e: e.data.get("digit") == "1"
            )
        """
        event_received = asyncio.Event()
        received_event: List[VoiceEvent] = []  # Lista para permitir modificação no closure
        
        async def capture_event(event: VoiceEvent):
            if condition is None or condition(event):
                received_event.append(event)
                event_received.set()
        
        self.on(event_type, capture_event)
        
        try:
            if timeout:
                try:
                    await asyncio.wait_for(event_received.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    return None
            else:
                await event_received.wait()
            
            return received_event[0] if received_event else None
            
        finally:
            self.off(event_type, capture_event)
    
    async def wait_for_any(
        self,
        event_types: List[VoiceEventType],
        timeout: Optional[float] = None
    ) -> Optional[VoiceEvent]:
        """
        Aguarda qualquer um dos eventos especificados.
        
        Args:
            event_types: Lista de tipos de evento
            timeout: Timeout em segundos
            
        Returns:
            Primeiro evento recebido, ou None se timeout
            
        Example:
            event = await bus.wait_for_any([
                VoiceEventType.TRANSFER_ACCEPTED,
                VoiceEventType.TRANSFER_REJECTED,
            ], timeout=60)
            
            if event and event.type == VoiceEventType.TRANSFER_ACCEPTED:
                # Atendente aceitou
                pass
        """
        event_received = asyncio.Event()
        received_event: List[VoiceEvent] = []
        
        async def capture_event(event: VoiceEvent):
            if not received_event:  # Só captura o primeiro
                received_event.append(event)
                event_received.set()
        
        for event_type in event_types:
            self.on(event_type, capture_event)
        
        try:
            if timeout:
                try:
                    await asyncio.wait_for(event_received.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    return None
            else:
                await event_received.wait()
            
            return received_event[0] if received_event else None
            
        finally:
            for event_type in event_types:
                self.off(event_type, capture_event)
    
    def get_history(
        self,
        event_type: Optional[VoiceEventType] = None,
        limit: int = 10
    ) -> List[VoiceEvent]:
        """
        Retorna histórico de eventos para debug.
        
        Args:
            event_type: Filtrar por tipo (None = todos)
            limit: Número máximo de eventos
            
        Returns:
            Lista de eventos (mais recentes primeiro)
        """
        if event_type:
            filtered = [e for e in self._event_history if e.type == event_type]
        else:
            filtered = self._event_history.copy()
        
        return filtered[-limit:]
    
    def clear_handlers(self) -> None:
        """Remove todos os handlers"""
        self._handlers.clear()
    
    def close(self) -> None:
        """
        Fecha o EventBus.
        
        Novos eventos são ignorados após fechar.
        """
        self._closed = True
        self._handlers.clear()
        logger.debug(f"EventBus closed", extra={"call_uuid": self.call_uuid})
