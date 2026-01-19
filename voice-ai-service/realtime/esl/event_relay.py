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
from queue import Queue, Empty
import os
import threading
import weakref
from typing import Optional, Any, Dict, Callable
from datetime import datetime

import gevent
from greenswitch.esl import OutboundSession

logger = logging.getLogger(__name__)

# Timeout para correlação de sessão (WebSocket pode demorar a conectar)
CORRELATION_TIMEOUT_SECONDS = float(os.getenv("DUAL_MODE_CORRELATION_TIMEOUT", "5.0"))
CORRELATION_RETRY_INTERVAL = float(os.getenv("DUAL_MODE_CORRELATION_RETRY", "0.5"))

# Intervalo de poll do event loop (segundos)
EVENT_LOOP_INTERVAL = float(os.getenv("DUAL_MODE_EVENT_LOOP_INTERVAL", "0.1"))

# ========================================
# Thread-Safe Global State
# ========================================
_loop_lock = threading.Lock()
_main_asyncio_loop: Optional[asyncio.AbstractEventLoop] = None

# Registry de EventRelays ativos (para correlação reversa)
# Permite que a sessão WebSocket notifique o ESL quando terminar
_relay_registry_lock = threading.Lock()
_relay_registry: Dict[str, weakref.ref] = {}  # call_uuid -> weakref(EventRelay)


def set_main_asyncio_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Define o asyncio loop da thread principal (thread-safe)."""
    global _main_asyncio_loop
    with _loop_lock:
        _main_asyncio_loop = loop
    logger.info("Main asyncio loop registered for ESL event relay")


def get_main_asyncio_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Obtém o asyncio loop da thread principal (thread-safe)."""
    with _loop_lock:
        return _main_asyncio_loop


def register_relay(call_uuid: str, relay: 'DualModeEventRelay') -> None:
    """Registra um EventRelay para correlação reversa."""
    with _relay_registry_lock:
        _relay_registry[call_uuid] = weakref.ref(relay)


def unregister_relay(call_uuid: str) -> None:
    """Remove um EventRelay do registry."""
    with _relay_registry_lock:
        _relay_registry.pop(call_uuid, None)


def get_relay(call_uuid: str) -> Optional['DualModeEventRelay']:
    """Obtém EventRelay por call_uuid (para correlação reversa)."""
    with _relay_registry_lock:
        ref = _relay_registry.get(call_uuid)
        if ref:
            return ref()  # Retorna None se objeto foi coletado
    return None


def notify_session_ended(call_uuid: str) -> None:
    """
    Notifica o EventRelay que a sessão WebSocket terminou.
    
    Chamado pelo session_manager quando uma sessão é removida.
    """
    relay = get_relay(call_uuid)
    if relay:
        relay.on_websocket_session_ended()


class DualModeEventRelay:
    """
    Relay de eventos ESL para sessões WebSocket.
    
    No modo dual:
    - WebSocket Server (8085) processa áudio e cria RealtimeSession
    - ESL Outbound (8022) recebe eventos e os retransmite para a sessão
    
    Esta classe é instanciada para cada conexão ESL Outbound.
    
    CORREÇÃO: greenswitch OutboundSession não tem método receive().
    Em vez disso, usamos event handlers registrados via session.register_handle().
    Ref: https://github.com/EvoluxBR/greenswitch/blob/master/examples/
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
        # CORREÇÃO: Usar weakref para evitar memory leak se session for coletada
        self._realtime_session_ref: Optional[weakref.ref] = None
        self._correlation_attempted = False
        
        # Estado
        self._should_stop = False
        self._connected = False
        self._hangup_received = False
        self._session_lock = threading.Lock()  # Protege acesso à sessão
        self._outbound_moh_active = False
        self._current_moh_source: Optional[str] = None  # Fonte de MOH atual
        self._command_queue: Queue = Queue()
        
        # Últimos eventos recebidos (para debug)
        self._last_dtmf: Optional[str] = None
        self._last_event: Optional[str] = None
    
    @property
    def _realtime_session(self) -> Optional[Any]:
        """Acesso thread-safe à sessão (resolve weakref)."""
        if self._realtime_session_ref:
            return self._realtime_session_ref()
        return None
    
    def on_websocket_session_ended(self) -> None:
        """
        Chamado quando a sessão WebSocket termina.
        
        Permite que o EventRelay saiba que não precisa mais
        tentar despachar eventos.
        """
        with self._session_lock:
            self._realtime_session_ref = None
        logger.debug(f"[{self._uuid}] WebSocket session ended, relay notified")
    
    def run(self) -> None:
        """
        Entry point principal - chamado pelo greenswitch.
        
        Este método é executado em uma greenlet separada (gevent).
        
        CORREÇÃO: Usar abordagem correta do greenswitch:
        1. connect() + myevents() + linger()
        2. Registrar handlers de eventos
        3. Loop com raise_if_disconnected()
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
            
            # 3. Registrar handlers de eventos
            self._register_event_handlers()
            
            # 4. Tentar correlacionar com sessão WebSocket
            self._correlate_session()
            
            # 5. Loop principal
            self._main_loop()
            
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
    
    def _register_event_handlers(self) -> None:
        """
        Registra handlers de eventos no greenswitch.
        
        CORREÇÃO: O greenswitch usa session.register_handle() para eventos,
        não um método receive(). Os handlers são chamados automaticamente.
        
        Ref: https://github.com/EvoluxBR/greenswitch
        """
        # Handler para CHANNEL_HANGUP
        self.session.register_handle("CHANNEL_HANGUP", self._on_channel_hangup_raw)
        
        # Handler para DTMF
        self.session.register_handle("DTMF", self._on_dtmf_raw)
        
        # Handler para CHANNEL_BRIDGE
        self.session.register_handle("CHANNEL_BRIDGE", self._on_channel_bridge_raw)
        
        # Handler para CHANNEL_UNBRIDGE
        self.session.register_handle("CHANNEL_UNBRIDGE", self._on_channel_unbridge_raw)
        
        # Handler para CHANNEL_HOLD
        self.session.register_handle("CHANNEL_HOLD", self._on_channel_hold_raw)
        
        # Handler para CHANNEL_UNHOLD
        self.session.register_handle("CHANNEL_UNHOLD", self._on_channel_unhold_raw)
        
        logger.debug(f"[{self._uuid}] Event handlers registered")
    
    def _correlate_session(self) -> None:
        """
        Tenta correlacionar com sessão WebSocket existente.
        
        O WebSocket pode conectar antes ou depois do ESL socket.
        Fazemos retry com backoff até encontrar a sessão.
        """
        if not self._uuid:
            logger.warning("Cannot correlate: no call_uuid")
            return
        
        # Registrar este relay para correlação reversa
        register_relay(self._uuid, self)
        
        from ..session_manager import get_session_manager
        manager = get_session_manager()
        
        # Retry loop
        start_time = datetime.utcnow()
        retries = 0
        max_retries = int(CORRELATION_TIMEOUT_SECONDS / CORRELATION_RETRY_INTERVAL)
        
        while retries < max_retries and not self._should_stop:
            session = manager.get_session(self._uuid)
            
            if session:
                # Sucesso! Guardar como weakref
                with self._session_lock:
                    self._realtime_session_ref = weakref.ref(session)
                
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
        
        # Não encontrou sessão no timeout inicial, mas continua tentando
        # em background no main loop
        logger.warning(
            f"[{self._uuid}] Could not correlate within {CORRELATION_TIMEOUT_SECONDS}s. "
            "Will continue trying in background. Check if mod_audio_stream is configured."
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
    
    def _main_loop(self) -> None:
        """
        Loop principal - mantém a sessão viva e processa eventos.
        
        CORREÇÃO: O greenswitch processa eventos automaticamente via handlers.
        Nós apenas precisamos:
        1. Manter a greenlet viva
        2. Verificar desconexão via raise_if_disconnected()
        3. Tentar correlação tardia se necessário
        """
        logger.debug(f"[{self._uuid}] Starting main loop")
        
        # Contador para retry de correlação em background
        retry_correlation_counter = 0
        RETRY_CORRELATION_INTERVAL = 100  # A cada 100 iterações (~10s)
        
        while not self._should_stop and self._connected and not self._hangup_received:
            try:
                # Verificar se caller desligou
                try:
                    self.session.raise_if_disconnected()
                except Exception:
                    logger.info(f"[{self._uuid}] Session disconnected")
                    self._on_disconnect()
                    break
                
                # Continuar tentando correlação em background se ainda não conseguiu
                if not self._realtime_session and self._correlation_attempted:
                    retry_correlation_counter += 1
                    if retry_correlation_counter >= RETRY_CORRELATION_INTERVAL:
                        retry_correlation_counter = 0
                        self._try_late_correlation()
                
                # Processar comandos pendentes (thread-safe)
                self._process_command_queue()
                
                # Yield para outras greenlets (CRÍTICO para greenswitch funcionar)
                gevent.sleep(EVENT_LOOP_INTERVAL)
                
            except Exception as e:
                if not self._should_stop:
                    logger.error(f"[{self._uuid}] Error in main loop: {e}")
                break
        
        logger.debug(f"[{self._uuid}] Main loop ended")

    def _process_command_queue(self) -> None:
        """Processa comandos enfileirados de outras threads."""
        while True:
            try:
                command, payload = self._command_queue.get_nowait()
            except Empty:
                break
            
            logger.info(
                f"🔄 [EVENT_RELAY] Processando comando da queue: {command}",
                extra={
                    "uuid": self._uuid,
                    "command": command,
                    "payload": payload,
                }
            )
            
            try:
                if command == "hold":
                    result = self._execute_outbound_hold(payload)
                    logger.info(f"🔄 [EVENT_RELAY] Comando hold executado: {result}")
            except Exception as e:
                logger.warning(f"🔄 [EVENT_RELAY] ERRO ao processar comando '{command}': {e}")
    
    def _try_late_correlation(self) -> None:
        """
        Tenta correlação tardia (WebSocket conectou depois do timeout).
        """
        from ..session_manager import get_session_manager
        manager = get_session_manager()
        
        session = manager.get_session(self._uuid)
        if session:
            with self._session_lock:
                self._realtime_session_ref = weakref.ref(session)
            
            logger.info(
                f"[{self._uuid}] Late correlation successful!",
                extra={"call_uuid": self._uuid}
            )
            
            self._notify_session_esl_connected()
    
    def _on_disconnect(self) -> None:
        """Chamado quando a sessão ESL desconecta."""
        self._should_stop = True
        
        # Notificar sessão WebSocket
        if self._realtime_session:
            self._dispatch_to_session("stop", "esl_disconnect")
    
    # ========================================
    # Event Handlers (chamados pelo greenswitch)
    # ========================================
    
    def _on_channel_hangup_raw(self, event: Any) -> None:
        """
        Handler para CHANNEL_HANGUP.
        
        Args:
            event: Objeto de evento do greenswitch (ESLEvent)
        """
        self._hangup_received = True
        
        # Extrair hangup cause do evento
        hangup_cause = "NORMAL_CLEARING"
        if hasattr(event, 'headers') and isinstance(event.headers, dict):
            hangup_cause = event.headers.get("Hangup-Cause", "NORMAL_CLEARING")
        elif hasattr(event, 'get_header'):
            hangup_cause = event.get_header("Hangup-Cause") or "NORMAL_CLEARING"
        
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
            self._dispatch_to_session("stop", f"esl_hangup:{hangup_cause}")
    
    def _on_dtmf_raw(self, event: Any) -> None:
        """Handler para DTMF."""
        # Extrair dígito do evento
        digit = ""
        duration = "0"
        
        if hasattr(event, 'headers') and isinstance(event.headers, dict):
            digit = event.headers.get("DTMF-Digit", "")
            duration = event.headers.get("DTMF-Duration", "0")
        elif hasattr(event, 'get_header'):
            digit = event.get_header("DTMF-Digit") or ""
            duration = event.get_header("DTMF-Duration") or "0"
        
        self._last_dtmf = digit
        
        logger.info(
            f"[{self._uuid}] DTMF received: {digit}",
            extra={
                "digit": digit,
                "duration": duration,
            }
        )
        
        if self._realtime_session and digit:
            self._dispatch_to_session("handle_dtmf", digit)
    
    def _on_channel_bridge_raw(self, event: Any) -> None:
        """Handler para CHANNEL_BRIDGE."""
        other_uuid = ""
        
        if hasattr(event, 'headers') and isinstance(event.headers, dict):
            other_uuid = event.headers.get("Other-Leg-Unique-ID", "")
        elif hasattr(event, 'get_header'):
            other_uuid = event.get_header("Other-Leg-Unique-ID") or ""
        
        logger.info(
            f"[{self._uuid}] CHANNEL_BRIDGE: connected to {other_uuid}",
        )
        
        if self._realtime_session:
            self._dispatch_to_session("handle_bridge", other_uuid)
    
    def _on_channel_unbridge_raw(self, event: Any) -> None:
        """Handler para CHANNEL_UNBRIDGE."""
        logger.info(f"[{self._uuid}] CHANNEL_UNBRIDGE")
        
        if self._realtime_session:
            self._dispatch_to_session("handle_unbridge", None)
    
    def _on_channel_hold_raw(self, event: Any) -> None:
        """Handler para CHANNEL_HOLD."""
        logger.info(f"[{self._uuid}] CHANNEL_HOLD")
        
        if self._realtime_session:
            self._dispatch_to_session("handle_hold", True)
    
    def _on_channel_unhold_raw(self, event: Any) -> None:
        """Handler para CHANNEL_UNHOLD."""
        logger.info(f"[{self._uuid}] CHANNEL_UNHOLD")
        
        if self._realtime_session:
            self._dispatch_to_session("handle_hold", False)
    
    # ========================================
    # Comandos ESL Outbound (para RealtimeSession)
    # ========================================
    # 
    # NOTA: O greenswitch OutboundSession NÃO tem método api().
    # Usamos session.hangup() para encerrar e session.execute() para comandos.
    # Para comandos API globais (uuid_hold, uuid_break, etc), usar ESL Inbound.
    # ========================================
    
    def hangup(self, cause: str = "NORMAL_CLEARING") -> bool:
        """
        Encerra a chamada via ESL Outbound.
        
        Este método é chamado pela RealtimeSession quando precisa
        desligar a chamada (ex: após despedida do usuário).
        
        NOTA: Usa timeout de 2s para evitar bloqueio indefinido
        se a sessão já tiver sido desconectada.
        
        Args:
            cause: Hangup cause (ex: NORMAL_CLEARING, USER_BUSY)
            
        Returns:
            True se comando foi enviado, False se falhou
        """
        if not self._connected or not self.session:
            logger.warning(f"[{self._uuid}] Cannot hangup: not connected")
            return False
        
        # Verificar se a sessão já foi desconectada usando raise_if_disconnected
        # Ref: https://github.com/EvoluxBR/greenswitch - método padrão do greenswitch
        try:
            self.session.raise_if_disconnected()
        except Exception:
            # Sessão já desconectada, não precisa fazer hangup
            logger.info(f"[{self._uuid}] Session already disconnected, no hangup needed")
            return True  # Não é erro, a chamada já encerrou
        
        try:
            import gevent
            
            # Usar timeout para evitar bloqueio indefinido
            with gevent.Timeout(2.0, False):
                self.session.hangup(cause)
                logger.info(f"[{self._uuid}] Hangup sent via ESL Outbound: {cause}")
                return True
            
            # Se chegou aqui, timeout expirou
            logger.warning(f"[{self._uuid}] Hangup via ESL Outbound timed out (session may be gone)")
            return False
            
        except Exception as e:
            # Se falhar, a sessão provavelmente já foi desconectada
            logger.warning(f"[{self._uuid}] Hangup via ESL Outbound failed: {e}")
            return False
    
    def execute_api(self, command: str) -> Optional[str]:
        """
        Executa comando API.
        
        NOTA: ESL Outbound NÃO suporta api() diretamente.
        Esta função sempre retorna None no modo Outbound.
        Use ESL Inbound para comandos API.
        
        Args:
            command: Comando FreeSWITCH (ignorado no modo Outbound)
            
        Returns:
            None (ESL Outbound não suporta api())
        """
        logger.debug(f"[{self._uuid}] execute_api not supported on ESL Outbound, use ESL Inbound")
        return None
    
    def uuid_hold(self, on: bool = True) -> bool:
        """
        Enfileira comando de espera no modo ESL Outbound.
        
        A execução real ocorre na greenlet do EventRelay para evitar
        bloqueios quando chamado a partir de threads asyncio.
        """
        logger.info(
            f"⏸️ [OUTBOUND_HOLD] Enfileirando comando {'HOLD' if on else 'UNHOLD'}...",
            extra={
                "uuid": self._uuid,
                "on": on,
                "connected": self._connected,
                "has_session": self.session is not None,
                "queue_size": self._command_queue.qsize(),
            }
        )
        
        if not self._connected or not self.session:
            logger.warning(f"⏸️ [OUTBOUND_HOLD] ERRO: Não conectado (connected={self._connected}, session={self.session is not None})")
            return False
        
        try:
            self._command_queue.put_nowait(("hold", on))
            logger.info(f"⏸️ [OUTBOUND_HOLD] Comando enfileirado com sucesso (queue_size={self._command_queue.qsize()})")
            return True
        except Exception as e:
            logger.warning(f"⏸️ [OUTBOUND_HOLD] ERRO ao enfileirar comando: {e}")
            return False

    def _execute_outbound_hold(self, on: bool) -> bool:
        """
        Executa hold/unhold no contexto da greenlet (seguro para greenswitch).
        """
        logger.info(
            f"⏸️ [EXECUTE_OUTBOUND_HOLD] Executando {'HOLD' if on else 'UNHOLD'}...",
            extra={
                "uuid": self._uuid,
                "on": on,
                "moh_active": self._outbound_moh_active,
            }
        )
        
        try:
            if on:
                # Tocar MOH em background (não bloqueante)
                # 
                # IMPORTANTE: local_stream retorna sucesso mesmo sem arquivos!
                # Então usamos tone_stream PRIMEIRO para garantir que o cliente ouça algo.
                # Se o usuário configurar MOH real no FreeSWITCH, pode mudar a ordem.
                #
                # Variável de ambiente para escolher MOH preferido
                preferred_moh = os.getenv("VOICE_AI_MOH_SOURCE", "tone_stream")
                
                if preferred_moh == "local_stream":
                    # Prioriza música de espera configurada
                    moh_sources = [
                        "local_stream://default",
                        "local_stream://moh",
                        "tone_stream://%(1000,4000,425);loops=-1",
                    ]
                else:
                    # Prioriza tom de ringback (garantido funcionar)
                    moh_sources = [
                        # Tom de ringback brasileiro (som de "chamando" - 425Hz)
                        "tone_stream://%(1000,4000,425);loops=-1",
                        # Fallback para MOH configurado
                        "local_stream://default",
                        "local_stream://moh",
                    ]
                
                for moh_source in moh_sources:
                    try:
                        logger.info(f"⏸️ [EXECUTE_OUTBOUND_HOLD] Tentando playback: {moh_source}")
                        self.session.playback(moh_source, block=False)
                        self._outbound_moh_active = True
                        self._current_moh_source = moh_source
                        logger.info(f"⏸️ [EXECUTE_OUTBOUND_HOLD] ✅ MOH iniciado com sucesso: {moh_source}")
                        return True
                    except Exception as e:
                        logger.warning(f"⏸️ [EXECUTE_OUTBOUND_HOLD] Falha com {moh_source}: {e}")
                        continue
                
                # Se nenhum funcionou, marcar como ativo mesmo assim
                self._outbound_moh_active = True
                logger.warning(f"⏸️ [EXECUTE_OUTBOUND_HOLD] ⚠️ Nenhum MOH disponível, continuando sem música")
                return True
            else:
                # Parar MOH (uuid_break é API global e requer permissão full)
                if self._outbound_moh_active:
                    logger.info(f"⏸️ [EXECUTE_OUTBOUND_HOLD] Parando MOH via uuid_break...")
                    try:
                        self.session.uuid_break()
                    except Exception as e:
                        logger.warning(f"⏸️ [EXECUTE_OUTBOUND_HOLD] uuid_break falhou: {e}")
                    self._outbound_moh_active = False
                    logger.info(f"⏸️ [EXECUTE_OUTBOUND_HOLD] ✅ MOH parado")
                else:
                    logger.info(f"⏸️ [EXECUTE_OUTBOUND_HOLD] MOH não estava ativo, nada a fazer")
            return True
        except Exception as e:
            logger.warning(f"⏸️ [EXECUTE_OUTBOUND_HOLD] ❌ ERRO: {e}", exc_info=True)
            return False
    
    def uuid_break(self) -> bool:
        """
        Interrompe qualquer mídia sendo reproduzida.
        
        NOTA: ESL Outbound não suporta uuid_break diretamente.
        Precisa usar ESL Inbound.
        
        Returns:
            False (não suportado no modo Outbound, usar Inbound)
        """
        logger.debug(f"[{self._uuid}] uuid_break not supported on ESL Outbound, use ESL Inbound")
        return False
    
    def uuid_broadcast(self, path: str, leg: str = "aleg") -> bool:
        """
        Reproduz mídia na chamada.
        
        NOTA: ESL Outbound não suporta uuid_broadcast diretamente.
        Precisa usar ESL Inbound.
        
        Args:
            path: Caminho do arquivo ou stream
            leg: Qual perna da chamada
            
        Returns:
            False (não suportado no modo Outbound, usar Inbound)
        """
        logger.debug(f"[{self._uuid}] uuid_broadcast not supported on ESL Outbound, use ESL Inbound")
        return False
    
    # ========================================
    # Dispatching para Sessão WebSocket
    # ========================================
    
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
                    # O resultado será processado pelo asyncio loop
                else:
                    # Método síncrono - agendar no loop
                    loop.call_soon_threadsafe(lambda: method(arg))
            else:
                logger.debug(f"[{self._uuid}] Method {method_name} not found on session")
                
        except Exception as e:
            logger.error(f"[{self._uuid}] Error dispatching {method_name}: {e}")
    
    def _cleanup(self) -> None:
        """Cleanup ao encerrar."""
        self._should_stop = True
        self._connected = False
        
        # Desregistrar do relay registry
        if self._uuid:
            unregister_relay(self._uuid)
        
        # Limpar referência à sessão
        with self._session_lock:
            self._realtime_session_ref = None
        
        elapsed = (datetime.utcnow() - self._start_time).total_seconds()
        
        logger.info(
            f"[{self._uuid}] ESL EventRelay ended",
            extra={
                "duration_seconds": elapsed,
                "was_correlated": self._correlation_attempted,
                "hangup_received": self._hangup_received,
            }
        )


# Factory function para compatibilidade com greenswitch
def create_event_relay(session: OutboundSession) -> DualModeEventRelay:
    """Factory para criar DualModeEventRelay."""
    return DualModeEventRelay(session)
