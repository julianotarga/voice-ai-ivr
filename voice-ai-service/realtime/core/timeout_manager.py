"""
TimeoutManager - Gerenciamento de timeouts internos.

Permite controle preciso de timeouts sem depender do FreeSWITCH.
Usa asyncio/anyio para timeouts com cancel scopes.

Referência: voice-ai-ivr/docs/PLANO-ARQUITETURA-INTERNA.md
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Any
import time

from .events import VoiceEvent, VoiceEventType
from .event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class TimeoutConfig:
    """
    Configuração de timeouts.
    
    Todos os valores em segundos.
    """
    
    # Transferência
    transfer_dial_timeout: float = 30.0         # Tempo para atendente atender
    transfer_response_timeout: float = 60.0     # Tempo para aceitar/recusar
    transfer_announcement_timeout: float = 30.0 # Tempo do anúncio
    transfer_bridge_timeout: float = 10.0       # Tempo para fazer bridge
    
    # Áudio
    audio_playback_timeout: float = 15.0        # Tempo máximo de playback
    audio_generation_timeout: float = 30.0      # Tempo para OpenAI gerar resposta
    audio_complete_timeout: float = 10.0        # Tempo para áudio terminar
    
    # Chamada
    call_idle_timeout: float = 30.0             # Silêncio antes de encerrar
    call_max_duration: float = 600.0            # 10 minutos máximo
    call_greeting_timeout: float = 10.0         # Tempo para saudação
    
    # ESL
    esl_command_timeout: float = 5.0            # Timeout para comandos ESL
    esl_event_timeout: float = 30.0             # Timeout para eventos ESL


@dataclass
class ActiveTimeout:
    """Representa um timeout ativo"""
    name: str
    seconds: float
    started_at: float
    deadline: float
    cancelled: bool = False


class TimeoutManager:
    """
    Gerenciador de timeouts internos.
    
    Funcionalidades:
    - timeout_scope: Context manager para operações com timeout
    - wait_for_*: Métodos convenientes para esperas comuns
    - Tracking de timeouts ativos para debug
    
    Vantagens sobre depender do ESL:
    - Não precisa esperar evento do FreeSWITCH
    - Controle granular por operação
    - Fácil cancelar/estender timeouts
    - Integrado com EventBus
    """
    
    def __init__(
        self,
        call_uuid: str,
        event_bus: EventBus,
        config: Optional[TimeoutConfig] = None
    ):
        """
        Inicializa TimeoutManager.
        
        Args:
            call_uuid: UUID da chamada
            event_bus: EventBus para emitir eventos e aguardar
            config: Configuração de timeouts (usa padrão se não fornecido)
        """
        self.call_uuid = call_uuid
        self.events = event_bus
        self.config = config or TimeoutConfig()
        
        self._active_timeouts: Dict[str, ActiveTimeout] = {}
        
        logger.info(
            "⏱️ [TIMEOUT_MGR] Initialized",
            extra={
                "call_uuid": self.call_uuid,
                "transfer_dial_timeout": self.config.transfer_dial_timeout,
                "transfer_response_timeout": self.config.transfer_response_timeout,
            }
        )
    
    @asynccontextmanager
    async def timeout_scope(
        self,
        name: str,
        seconds: float,
        on_timeout: Optional[Callable] = None,
        emit_event: bool = True
    ):
        """
        Context manager para timeout.
        
        Args:
            name: Nome do timeout (para logging/debug)
            seconds: Tempo em segundos
            on_timeout: Callback opcional quando timeout ocorrer
            emit_event: Se deve emitir evento de timeout
            
        Yields:
            Dict com 'cancelled_caught' após o bloco
            
        Example:
            async with timeout_mgr.timeout_scope("dial", 30) as scope:
                await dial_attendant()
            
            if scope['cancelled_caught']:
                # Timeout atingido!
                pass
        """
        started_at = time.time()
        deadline = started_at + seconds
        
        timeout_info = ActiveTimeout(
            name=name,
            seconds=seconds,
            started_at=started_at,
            deadline=deadline
        )
        self._active_timeouts[name] = timeout_info
        
        logger.debug(
            f"⏱️ [TIMEOUT_MGR] Started: {name} ({seconds}s)",
            extra={
                "call_uuid": self.call_uuid,
                "timeout_name": name,
                "timeout_seconds": seconds,
            }
        )
        
        scope_result = {'cancelled_caught': False}
        
        try:
            try:
                async with asyncio.timeout(seconds):
                    yield scope_result
            except asyncio.TimeoutError:
                scope_result['cancelled_caught'] = True
                timeout_info.cancelled = True
                
                elapsed = time.time() - started_at
                logger.warning(
                    f"⏱️ [TIMEOUT_MGR] EXPIRED: {name} after {elapsed:.1f}s (limit: {seconds}s)",
                    extra={
                        "call_uuid": self.call_uuid,
                        "timeout_name": name,
                        "timeout_seconds": seconds,
                        "elapsed_seconds": elapsed,
                    }
                )
                
                # Emitir evento se configurado
                # NOTA: Só emite TRANSFER_TIMEOUT para timeouts de transferência
                # Outros timeouts são apenas logados
                if emit_event and name.startswith("transfer"):
                    await self.events.emit(VoiceEvent(
                        type=VoiceEventType.TRANSFER_TIMEOUT,
                        call_uuid=self.call_uuid,
                        data={
                            "timeout_name": name,
                            "timeout_seconds": seconds,
                            "elapsed_seconds": elapsed
                        },
                        source="timeout_manager"
                    ))
                
                # Callback opcional
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
        
        Args:
            timeout: Timeout em segundos (usa config se None)
            
        Returns:
            "accepted", "rejected", ou "timeout"
        """
        timeout = timeout or self.config.transfer_response_timeout
        
        logger.info(
            f"Waiting for transfer response (timeout: {timeout}s)",
            extra={"call_uuid": self.call_uuid}
        )
        
        # Aguardar qualquer um dos eventos
        event = await self.events.wait_for_any(
            [
                VoiceEventType.TRANSFER_ACCEPTED,
                VoiceEventType.TRANSFER_REJECTED,
            ],
            timeout=timeout
        )
        
        if event is None:
            logger.info(
                f"Transfer response timeout after {timeout}s",
                extra={"call_uuid": self.call_uuid}
            )
            
            await self.events.emit(VoiceEvent(
                type=VoiceEventType.TRANSFER_TIMEOUT,
                call_uuid=self.call_uuid,
                data={
                    "reason": "no_response",
                    "timeout": timeout
                }
            ))
            return "timeout"
        
        result = "accepted" if event.type == VoiceEventType.TRANSFER_ACCEPTED else "rejected"
        logger.info(
            f"Transfer response: {result}",
            extra={"call_uuid": self.call_uuid}
        )
        
        return result
    
    async def wait_for_audio_complete(
        self,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Aguarda áudio terminar de tocar com timeout.
        
        Args:
            timeout: Timeout em segundos
            
        Returns:
            True se completou, False se timeout
        """
        timeout = timeout or self.config.audio_complete_timeout
        
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
        
        Args:
            timeout: Timeout em segundos
            
        Returns:
            True se atendeu, False se timeout/não atendeu
        """
        timeout = timeout or self.config.transfer_dial_timeout
        
        logger.info(
            f"Waiting for dial answer (timeout: {timeout}s)",
            extra={"call_uuid": self.call_uuid}
        )
        
        event = await self.events.wait_for_any(
            [
                VoiceEventType.TRANSFER_ANSWERED,
                VoiceEventType.TRANSFER_FAILED,
            ],
            timeout=timeout
        )
        
        if event is None:
            logger.info(
                f"Dial answer timeout after {timeout}s",
                extra={"call_uuid": self.call_uuid}
            )
            return False
        
        answered = event.type == VoiceEventType.TRANSFER_ANSWERED
        logger.info(
            f"Dial result: {'answered' if answered else 'failed'}",
            extra={"call_uuid": self.call_uuid}
        )
        
        return answered
    
    async def wait_for_announcement_complete(
        self,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Aguarda anúncio para atendente completar.
        
        Args:
            timeout: Timeout em segundos
            
        Returns:
            True se completou, False se timeout
        """
        timeout = timeout or self.config.transfer_announcement_timeout
        
        event = await self.events.wait_for(
            VoiceEventType.TRANSFER_ANNOUNCEMENT_DONE,
            timeout=timeout
        )
        
        return event is not None
    
    def cancel(self, name: str) -> bool:
        """
        Marca um timeout como cancelado.
        
        NOTA: Este método apenas marca o timeout como cancelado para fins de
        tracking. O asyncio.timeout() dentro do timeout_scope() não pode ser
        cancelado externamente - a task precisa completar ou o timeout expirar.
        
        Use este método para evitar que o callback on_timeout seja executado
        ou para indicar que o resultado do timeout deve ser ignorado.
        
        Args:
            name: Nome do timeout
            
        Returns:
            True se marcou como cancelado, False se não existia
        """
        if name in self._active_timeouts:
            self._active_timeouts[name].cancelled = True
            logger.debug(
                f"Timeout '{name}' marked as cancelled",
                extra={"call_uuid": self.call_uuid}
            )
            return True
        return False
    
    def cancel_all(self) -> int:
        """
        Cancela todos os timeouts ativos.
        
        Returns:
            Número de timeouts cancelados
        """
        count = len(self._active_timeouts)
        for timeout in self._active_timeouts.values():
            timeout.cancelled = True
        
        if count > 0:
            logger.info(
                f"Cancelled {count} active timeouts",
                extra={"call_uuid": self.call_uuid}
            )
        
        return count
    
    def get_active_timeouts(self) -> List[Dict[str, Any]]:
        """Retorna lista de timeouts ativos para debug"""
        now = time.time()
        return [
            {
                "name": t.name,
                "seconds": t.seconds,
                "remaining": max(0, t.deadline - now),
                "elapsed": now - t.started_at,
                "cancelled": t.cancelled
            }
            for t in self._active_timeouts.values()
        ]
