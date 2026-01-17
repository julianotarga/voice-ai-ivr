"""
DualModeEventRelay - Relay de eventos ESL para sessões WebSocket.

Usado no modo AUDIO_MODE=dual para:
1. Receber eventos ESL (HANGUP, DTMF, BRIDGE)
2. Correlacionar com sessão WebSocket existente via call_uuid
3. Disparar ações na sessão (stop, handle_dtmf, etc.)

IMPORTANTE: Esta classe NÃO processa áudio - o áudio vem via mod_audio_stream (WebSocket).
O ESL Outbound é usado apenas para eventos e controle.

Referências:
- voice-ai-ivr/openspec/changes/dual-mode-esl-websocket/proposal.md
- https://github.com/EvoluxBR/greenswitch
"""

import asyncio
import logging
import os
import threading
from typing import Optional, Any
from datetime import datetime

import gevent
from greenswitch.esl import OutboundSession

logger = logging.getLogger(__name__)

# Timeout para correlação de sessão (WebSocket pode demorar a conectar)
CORRELATION_TIMEOUT_SECONDS = float(os.getenv("DUAL_MODE_CORRELATION_TIMEOUT", "5.0"))
CORRELATION_RETRY_INTERVAL = float(os.getenv("DUAL_MODE_CORRELATION_RETRY", "0.5"))

# Referência global ao asyncio loop da thread principal
_main_asyncio_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_asyncio_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Define o asyncio loop da thread principal."""
    global _main_asyncio_loop
    _main_asyncio_loop = loop
    logger.info("Main asyncio loop registered for ESL event relay")


def get_main_asyncio_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Obtém o asyncio loop da thread principal."""
    return _main_asyncio_loop


class DualModeEventRelay:
    """
    Relay de eventos ESL para sessões WebSocket.
    
    No modo dual:
    - WebSocket Server (8085) processa áudio e cria RealtimeSession
    - ESL Outbound (8022) recebe eventos e os retransmite para a sessão
    
    Esta classe é instanciada para cada conexão ESL Outbound.
    """
    
    def __init__(self, session: OutboundSession):
        """
        Args:
            session: OutboundSession do greenswitch
        """
        self.session = session
        self._start_time = datetime.utcnow()
        
        # Identificadores
        self._uuid: Optional[str] = None
        self._caller_id: Optional[str] = None
        self._domain_uuid: Optional[str] = None
        self._secretary_uuid: Optional[str] = None
        
        # Referência à sessão WebSocket (será correlacionada)
        self._realtime_session: Optional[Any] = None
        self._correlation_attempted = False
        
        # Estado
        self._should_stop = False
        self._connected = False
    
    def run(self) -> None:
        """
        Entry point principal - chamado pelo greenswitch.
        
        Este método é executado em uma greenlet separada (gevent).
        """
        try:
            # 1. Conectar ao canal FreeSWITCH
            self._connect()
            
            # 2. Extrair variáveis do canal
            self._extract_channel_vars()
            
            logger.info(
                f"[{self._uuid}] ESL EventRelay started - "
                f"caller={self._caller_id}, domain={self._domain_uuid}"
            )
            
            # 3. Tentar correlacionar com sessão WebSocket
            self._correlate_session()
            
            # 4. Loop de eventos
            self._event_loop()
            
        except Exception as e:
            logger.exception(f"[{self._uuid}] Error in ESL EventRelay: {e}")
        finally:
            # Cleanup
            self._cleanup()
    
    def _connect(self) -> None:
        """Conecta ao canal FreeSWITCH via ESL."""
        try:
            self.session.connect()
            
            # Subscrever apenas eventos que nos interessam
            self.session.myevents()
            
            # CRÍTICO: linger() mantém a sessão ESL ativa após hangup
            # Isso nos permite receber o evento de hangup
            self.session.linger()
            
            self._uuid = self.session.uuid
            self._connected = True
            
            logger.debug(f"[{self._uuid}] ESL EventRelay connected with linger")
            
        except Exception as e:
            logger.error(f"Failed to connect ESL session: {e}")
            raise
    
    def _extract_channel_vars(self) -> None:
        """Extrai variáveis importantes do canal."""
        data = self.session.session_data or {}
        
        self._caller_id = data.get("Caller-Caller-ID-Number", "unknown")
        
        # Suportar múltiplos nomes de variáveis (compatibilidade)
        self._domain_uuid = (
            data.get("variable_VOICE_AI_DOMAIN_UUID") or
            data.get("variable_domain_uuid") or
            data.get("variable_voiceai_domain_uuid")
        )
        self._secretary_uuid = (
            data.get("variable_VOICE_AI_SECRETARY_UUID") or
            data.get("variable_secretary_uuid") or
            data.get("variable_voiceai_secretary_uuid")
        )
        
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[{self._uuid}] Channel vars extracted", extra={
                "caller_id": self._caller_id,
                "domain_uuid": self._domain_uuid,
                "secretary_uuid": self._secretary_uuid,
            })
    
    def _correlate_session(self) -> None:
        """
        Tenta correlacionar com sessão WebSocket existente.
        
        O WebSocket pode conectar antes ou depois do ESL socket.
        Fazemos retry com backoff até encontrar a sessão.
        """
        if not self._uuid:
            logger.warning("Cannot correlate: no call_uuid")
            return
        
        from ..session_manager import get_session_manager
        manager = get_session_manager()
        
        # Retry loop
        start_time = datetime.utcnow()
        retries = 0
        max_retries = int(CORRELATION_TIMEOUT_SECONDS / CORRELATION_RETRY_INTERVAL)
        
        while retries < max_retries and not self._should_stop:
            self._realtime_session = manager.get_session(self._uuid)
            
            if self._realtime_session:
                # Sucesso!
                logger.info(
                    f"[{self._uuid}] Session correlated successfully after {retries} retries",
                    extra={
                        "elapsed_ms": (datetime.utcnow() - start_time).total_seconds() * 1000,
                    }
                )
                
                # Notificar a sessão que ESL está conectado
                self._notify_session_esl_connected()
                return
            
            # Aguardar e tentar novamente
            gevent.sleep(CORRELATION_RETRY_INTERVAL)
            retries += 1
        
        # Não encontrou sessão
        logger.warning(
            f"[{self._uuid}] Could not correlate with WebSocket session after {retries} retries. "
            "Events will not be relayed. Check if mod_audio_stream is configured correctly."
        )
        self._correlation_attempted = True
    
    def _notify_session_esl_connected(self) -> None:
        """Notifica a sessão que ESL está conectado."""
        if not self._realtime_session:
            return
        
        loop = get_main_asyncio_loop()
        if not loop:
            logger.warning("No asyncio loop available for notification")
            return
        
        # Chamar método async de forma thread-safe
        try:
            if hasattr(self._realtime_session, 'set_esl_connected'):
                asyncio.run_coroutine_threadsafe(
                    self._realtime_session.set_esl_connected(True),
                    loop
                )
        except Exception as e:
            logger.debug(f"Could not notify ESL connected: {e}")
    
    def _event_loop(self) -> None:
        """Loop principal de eventos."""
        logger.debug(f"[{self._uuid}] Starting event loop")
        
        while not self._should_stop and self._connected:
            try:
                # Aguardar evento com timeout
                event = self._wait_for_event(timeout=1.0)
                
                if event:
                    self._handle_event(event)
                
            except Exception as e:
                if not self._should_stop:
                    logger.error(f"[{self._uuid}] Error in event loop: {e}")
                break
        
        logger.debug(f"[{self._uuid}] Event loop ended")
    
    def _wait_for_event(self, timeout: float = 1.0) -> Optional[dict]:
        """
        Aguarda evento do FreeSWITCH.
        
        Greenswitch usa gevent.Timeout para isso.
        """
        try:
            with gevent.Timeout(timeout, False):
                # Ler dados do socket
                data = self.session.receive()
                if data:
                    return self._parse_event(data)
        except Exception:
            pass
        return None
    
    def _parse_event(self, data: str) -> dict:
        """Parseia evento ESL."""
        event = {}
        for line in data.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                event[key.strip()] = value.strip()
        return event
    
    def _handle_event(self, event: dict) -> None:
        """Processa evento recebido."""
        event_name = event.get("Event-Name", "")
        
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[{self._uuid}] Event received: {event_name}")
        
        if event_name == "CHANNEL_HANGUP":
            self._on_channel_hangup(event)
        
        elif event_name == "DTMF":
            self._on_dtmf(event)
        
        elif event_name == "CHANNEL_BRIDGE":
            self._on_channel_bridge(event)
        
        elif event_name == "CHANNEL_UNBRIDGE":
            self._on_channel_unbridge(event)
        
        elif event_name == "CHANNEL_HOLD":
            self._on_channel_hold(event)
        
        elif event_name == "CHANNEL_UNHOLD":
            self._on_channel_unhold(event)
    
    def _on_channel_hangup(self, event: dict) -> None:
        """Handler para CHANNEL_HANGUP."""
        hangup_cause = event.get("Hangup-Cause", "NORMAL_CLEARING")
        
        logger.info(
            f"[{self._uuid}] CHANNEL_HANGUP detected",
            extra={
                "hangup_cause": hangup_cause,
                "has_session": self._realtime_session is not None,
            }
        )
        
        # Sinalizar para parar o loop
        self._should_stop = True
        
        # Notificar sessão WebSocket
        if self._realtime_session:
            self._dispatch_to_session(
                "stop",
                f"esl_hangup:{hangup_cause}"
            )
    
    def _on_dtmf(self, event: dict) -> None:
        """Handler para DTMF."""
        digit = event.get("DTMF-Digit", "")
        duration = event.get("DTMF-Duration", "0")
        
        logger.info(
            f"[{self._uuid}] DTMF received: {digit}",
            extra={
                "digit": digit,
                "duration": duration,
            }
        )
        
        if self._realtime_session and digit:
            self._dispatch_to_session("handle_dtmf", digit)
    
    def _on_channel_bridge(self, event: dict) -> None:
        """Handler para CHANNEL_BRIDGE (chamada conectada a outro canal)."""
        other_uuid = event.get("Other-Leg-Unique-ID", "")
        
        logger.info(
            f"[{self._uuid}] CHANNEL_BRIDGE: connected to {other_uuid}",
        )
        
        if self._realtime_session:
            self._dispatch_to_session("handle_bridge", other_uuid)
    
    def _on_channel_unbridge(self, event: dict) -> None:
        """Handler para CHANNEL_UNBRIDGE (chamada desconectada de outro canal)."""
        logger.info(f"[{self._uuid}] CHANNEL_UNBRIDGE")
        
        if self._realtime_session:
            self._dispatch_to_session("handle_unbridge", None)
    
    def _on_channel_hold(self, event: dict) -> None:
        """Handler para CHANNEL_HOLD."""
        logger.info(f"[{self._uuid}] CHANNEL_HOLD")
        
        if self._realtime_session:
            self._dispatch_to_session("handle_hold", True)
    
    def _on_channel_unhold(self, event: dict) -> None:
        """Handler para CHANNEL_UNHOLD."""
        logger.info(f"[{self._uuid}] CHANNEL_UNHOLD")
        
        if self._realtime_session:
            self._dispatch_to_session("handle_hold", False)
    
    def _dispatch_to_session(self, method_name: str, arg: Any) -> None:
        """
        Despacha chamada de método para a sessão WebSocket.
        
        Usa asyncio.run_coroutine_threadsafe para chamar de forma thread-safe,
        já que estamos em uma greenlet (gevent) e a sessão roda em asyncio.
        """
        if not self._realtime_session:
            return
        
        loop = get_main_asyncio_loop()
        if not loop:
            logger.warning(f"[{self._uuid}] No asyncio loop available for dispatch")
            return
        
        try:
            method = getattr(self._realtime_session, method_name, None)
            if method and callable(method):
                # Se o método é async, usar run_coroutine_threadsafe
                if asyncio.iscoroutinefunction(method):
                    future = asyncio.run_coroutine_threadsafe(
                        method(arg) if arg is not None else method(),
                        loop
                    )
                    # Não bloquear esperando resultado
                    # future.result(timeout=5.0)
                else:
                    # Método síncrono - executar diretamente
                    loop.call_soon_threadsafe(method, arg)
            else:
                logger.debug(f"[{self._uuid}] Method {method_name} not found on session")
                
        except Exception as e:
            logger.error(f"[{self._uuid}] Error dispatching {method_name}: {e}")
    
    def _cleanup(self) -> None:
        """Cleanup ao encerrar."""
        self._should_stop = True
        self._connected = False
        
        elapsed = (datetime.utcnow() - self._start_time).total_seconds()
        
        logger.info(
            f"[{self._uuid}] ESL EventRelay ended",
            extra={
                "duration_seconds": elapsed,
                "session_correlated": self._realtime_session is not None,
            }
        )


# Factory function para compatibilidade com greenswitch
def create_event_relay(session: OutboundSession) -> DualModeEventRelay:
    """Factory para criar DualModeEventRelay."""
    return DualModeEventRelay(session)
