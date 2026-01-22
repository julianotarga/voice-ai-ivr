"""
Transferência anunciada usando uuid_bridge do FreeSWITCH.

Abordagem SIMPLIFICADA que evita os problemas de conferência:
- Não usa mod_conference (evita problemas de hangup_after_conference, endconf, etc)
- Usa uuid_bridge que automaticamente derruba uma perna quando a outra desliga
- Menos comandos ESL = menos pontos de falha

Fluxo:
1. uuid_audio_stream PAUSE no A-leg (cliente em silêncio)
2. Originar B-leg com 'answer:,park:' inline
3. uuid_audio_stream START no B-leg (conecta com IA de anúncio)
4. IA anuncia, atendente decide
5a. SE ACEITO: uuid_bridge A-leg B-leg (hangup automático!)
5b. SE REJEITADO: uuid_kill B-leg, uuid_audio_stream RESUME A-leg

PONTOS DE ATENÇÃO:
- Race Conditions: Lock para serializar operações no audio_stream
- Timeouts: Configuráveis e explícitos em cada operação
- Cleanup: try/finally garante limpeza mesmo com exceções
- Logging: Cada transição é logada com estado
- Rollback: Se falhar, volta ao estado anterior (resume A-leg)
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable, Any
from uuid import uuid4

from .esl_client import AsyncESLClient
from .realtime_announcement_conference import ConferenceAnnouncementSession

# Core - Sistema de controle interno
from ..core import EventBus, VoiceEvent, VoiceEventType

logger = logging.getLogger(__name__)


class TransferDecision(Enum):
    """Decisão do atendente sobre a transferência."""
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    HANGUP = "hangup"
    ERROR = "error"


class TransferState(Enum):
    """Estado interno da transferência."""
    IDLE = "idle"
    A_LEG_PAUSED = "a_leg_paused"
    B_LEG_RINGING = "b_leg_ringing"
    B_LEG_ANSWERED = "b_leg_answered"
    ANNOUNCING = "announcing"
    BRIDGING = "bridging"
    BRIDGED = "bridged"
    ROLLING_BACK = "rolling_back"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BridgeTransferResult:
    """Resultado da transferência anunciada via bridge."""
    success: bool
    decision: TransferDecision
    b_leg_uuid: Optional[str] = None
    error: Optional[str] = None
    ticket_id: Optional[str] = None
    duration_ms: int = 0
    final_state: TransferState = TransferState.IDLE


@dataclass
class BridgeTransferConfig:
    """Configuração para transferência via bridge."""
    # Timeouts (em segundos)
    esl_command_timeout: float = 3.0      # Timeout para comandos ESL simples
    originate_timeout: int = 30           # Timeout para B-leg atender
    originate_poll_interval: float = 0.5  # Intervalo de polling durante originate
    announcement_timeout: float = 30.0    # Timeout para decisão do atendente
    bridge_timeout: float = 5.0           # Timeout para criar bridge
    cleanup_timeout: float = 2.0          # Timeout para operações de cleanup
    stabilization_delay: float = 0.5      # Delay após operações críticas
    
    # OpenAI
    openai_model: str = "gpt-realtime"
    openai_voice: str = "marin"
    
    # Comportamento
    accept_on_timeout: bool = False
    
    # Prompts customizados
    announcement_prompt: Optional[str] = None
    courtesy_message: Optional[str] = None


class BridgeTransferManager:
    """
    Gerencia transferências anunciadas usando uuid_bridge.
    
    Esta é uma abordagem MUITO MAIS SIMPLES que conferência:
    - A-leg fica em HOLD (audio stream pausado)
    - B-leg fica em PARK recebendo anúncio via audio stream
    - Se aceito: uuid_bridge conecta os dois diretamente
    - FreeSWITCH gerencia hangup automaticamente!
    
    PROTEÇÕES IMPLEMENTADAS:
    - _state_lock: Serializa operações para evitar race conditions
    - _state: Rastreia estado para rollback correto
    - try/finally: Garante cleanup em caso de exceção
    - Timeouts explícitos: Cada operação tem timeout configurável
    """
    
    def __init__(
        self,
        esl_client: AsyncESLClient,
        a_leg_uuid: str,
        domain: str,
        caller_id: str,
        config: Optional[BridgeTransferConfig] = None,
        on_resume: Optional[Callable[[], Awaitable[None]]] = None,
        secretary_uuid: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.esl = esl_client
        self.a_leg_uuid = a_leg_uuid
        self.domain = domain
        self.caller_id = caller_id
        self.config = config or BridgeTransferConfig()
        self.on_resume = on_resume
        self.secretary_uuid = secretary_uuid
        self.event_bus = event_bus
        
        # Estado interno
        self._state = TransferState.IDLE
        self._state_lock = asyncio.Lock()  # PROTEÇÃO: Serializa operações
        self._b_leg_uuid: Optional[str] = None
        self._start_time: float = 0
        
        # Eventos de hangup
        self._a_leg_hangup_event = asyncio.Event()
        self._b_leg_hangup_event = asyncio.Event()
        self._hangup_handler_id: Optional[str] = None
    
    # =========================================================================
    # LOGGING E ESTADO
    # =========================================================================
    
    def _elapsed(self) -> str:
        """Retorna tempo decorrido formatado."""
        return f"[{time.time() - self._start_time:.2f}s]"
    
    def _log_state_change(self, new_state: TransferState, reason: str = "") -> None:
        """Loga mudança de estado."""
        old_state = self._state
        self._state = new_state
        logger.info(
            f"{self._elapsed()} 🔄 STATE: {old_state.value} → {new_state.value}"
            + (f" ({reason})" if reason else "")
        )
    
    async def _emit_event(self, event_type: VoiceEventType, **kwargs) -> None:
        """Emite evento no EventBus."""
        if self.event_bus:
            try:
                await self.event_bus.emit(
                    event_type.value,
                    call_uuid=self.a_leg_uuid,
                    state=self._state.value,
                    **kwargs
                )
            except Exception as e:
                logger.debug(f"Could not emit event {event_type}: {e}")
    
    # =========================================================================
    # MONITOR DE HANGUP
    # =========================================================================
    
    async def _start_hangup_monitor(self) -> None:
        """Inicia monitor de hangup para ambas as pernas."""
        self._hangup_handler_id = str(uuid4())
        
        async def on_hangup(uuid: str, cause: str, **kwargs):
            if uuid == self.a_leg_uuid:
                logger.info(f"{self._elapsed()} 🔴 A-leg HANGUP: {cause}")
                self._a_leg_hangup_event.set()
                # CLEANUP: Se A-leg desliga, matar B-leg imediatamente
                if self._b_leg_uuid and self._state not in (TransferState.BRIDGED, TransferState.COMPLETED):
                    logger.info(f"{self._elapsed()} 🧹 Auto-cleanup: matando B-leg após A-leg hangup")
                    await self._kill_b_leg_safe()
                    
            elif uuid == self._b_leg_uuid:
                logger.info(f"{self._elapsed()} 🔴 B-leg HANGUP: {cause}")
                self._b_leg_hangup_event.set()
        
        if self.event_bus:
            self.event_bus.on("channel_hangup", on_hangup)
        
        logger.debug(f"[HANGUP_MONITOR] Registrado: {self._hangup_handler_id}")
    
    async def _stop_hangup_monitor(self) -> None:
        """Para monitor de hangup."""
        if self._hangup_handler_id:
            # TODO: Implementar remoção de handler no EventBus
            logger.debug(f"[HANGUP_MONITOR] Removido: {self._hangup_handler_id}")
            self._hangup_handler_id = None
    
    # =========================================================================
    # OPERAÇÕES ESL (COM TIMEOUT E LOGGING)
    # =========================================================================
    
    async def _esl_command(
        self,
        command: str,
        timeout: Optional[float] = None,
        description: str = ""
    ) -> tuple[bool, str]:
        """
        Executa comando ESL com timeout e logging.
        
        Returns:
            (success, result_string)
        """
        timeout = timeout or self.config.esl_command_timeout
        desc = description or command.split()[0]
        
        try:
            logger.debug(f"{self._elapsed()} ESL [{desc}]: {command}")
            result = await asyncio.wait_for(
                self.esl.execute_api(command),
                timeout=timeout
            )
            result_str = result if isinstance(result, str) else str(result)
            
            success = "+OK" in result_str or "Success" in result_str
            log_level = logging.DEBUG if success else logging.WARNING
            logger.log(log_level, f"{self._elapsed()} ESL [{desc}] → {result_str[:100]}")
            
            return success, result_str
            
        except asyncio.TimeoutError:
            logger.error(f"{self._elapsed()} ESL [{desc}] TIMEOUT ({timeout}s)")
            return False, "TIMEOUT"
        except Exception as e:
            logger.error(f"{self._elapsed()} ESL [{desc}] ERROR: {e}")
            return False, str(e)
    
    async def _verify_channel_exists(self, uuid: str, name: str = "channel") -> bool:
        """Verifica se um canal existe."""
        try:
            exists = await asyncio.wait_for(
                self.esl.uuid_exists(uuid),
                timeout=self.config.esl_command_timeout
            )
            logger.debug(f"{self._elapsed()} {name} exists: {exists}")
            return exists
        except Exception as e:
            logger.warning(f"{self._elapsed()} Could not verify {name}: {e}")
            return False
    
    # =========================================================================
    # OPERAÇÕES DE ÁUDIO (COM LOCK PARA EVITAR RACE CONDITIONS)
    # =========================================================================
    
    async def _pause_a_leg_audio(self) -> bool:
        """
        Pausa o audio stream do A-leg (cliente em silêncio).
        
        PROTEÇÃO: Usa lock para evitar race condition com resume.
        """
        async with self._state_lock:
            logger.info(f"{self._elapsed()} 📍 Pausando audio do A-leg...")
            
            success, result = await self._esl_command(
                f"uuid_audio_stream {self.a_leg_uuid} pause",
                description="PAUSE_A"
            )
            
            if success or "OK" in result:
                self._log_state_change(TransferState.A_LEG_PAUSED)
                return True
            else:
                logger.warning(f"{self._elapsed()} ⚠️ Pause falhou, mas continuando...")
                return True  # Continuar mesmo assim
    
    async def _resume_a_leg_audio(self) -> bool:
        """
        Resume o audio stream do A-leg.
        
        PROTEÇÃO: Usa lock para evitar race condition com pause.
        FALLBACK: Se resume falhar, tenta reconectar via start.
        """
        async with self._state_lock:
            logger.info(f"{self._elapsed()} 📍 Resumindo audio do A-leg...")
            
            # 1. Verificar se A-leg ainda existe
            if not await self._verify_channel_exists(self.a_leg_uuid, "A-leg"):
                logger.error(f"{self._elapsed()} ❌ A-leg não existe mais!")
                return False
            
            # 2. Tentar RESUME primeiro (rápido se WS aberto)
            success, result = await self._esl_command(
                f"uuid_audio_stream {self.a_leg_uuid} resume",
                description="RESUME_A"
            )
            
            if success:
                logger.info(f"{self._elapsed()} ✅ A-leg audio resumido via RESUME")
                return True
            
            # 3. Resume falhou - WebSocket pode ter fechado
            logger.warning(f"{self._elapsed()} Resume falhou, tentando reconectar via START...")
            
            # 3a. Stop para limpar estado inconsistente
            await self._esl_command(
                f"uuid_audio_stream {self.a_leg_uuid} stop",
                timeout=self.config.cleanup_timeout,
                description="STOP_A_CLEANUP"
            )
            await asyncio.sleep(0.1)
            
            # 3b. Construir URL do WebSocket
            ws_host = os.getenv("REALTIME_WS_HOST", "127.0.0.1")
            ws_port = os.getenv("REALTIME_WS_PORT", "8085")
            sec_uuid = self.secretary_uuid or "unknown"
            ws_url = f"ws://{ws_host}:{ws_port}/stream/{sec_uuid}/{self.a_leg_uuid}/{self.caller_id}"
            
            logger.info(f"{self._elapsed()} Reconectando: {ws_url}")
            
            # 3c. Iniciar nova conexão
            success, result = await self._esl_command(
                f"uuid_audio_stream {self.a_leg_uuid} start {ws_url} mono 8k",
                timeout=5.0,
                description="START_A_RECONNECT"
            )
            
            if success:
                logger.info(f"{self._elapsed()} ✅ A-leg audio reconectado via START")
                await asyncio.sleep(self.config.stabilization_delay)
                return True
            else:
                logger.error(f"{self._elapsed()} ❌ Falha total ao resumir A-leg")
                return False
    
    # =========================================================================
    # OPERAÇÕES B-LEG
    # =========================================================================
    
    async def _originate_b_leg(
        self,
        destination: str,
        direct_contact: Optional[str] = None
    ) -> Optional[str]:
        """
        Origina B-leg (atendente) para park.
        
        PROTEÇÃO: Verifica hangup do A-leg durante o polling.
        CLEANUP: Mata B-leg se A-leg desligar.
        """
        self._log_state_change(TransferState.B_LEG_RINGING, f"destination={destination}")
        
        # Gerar UUID para o B-leg
        b_leg_uuid = str(uuid4())
        self._b_leg_uuid = b_leg_uuid
        
        # Determinar dial string
        if direct_contact:
            contact = direct_contact.replace("sip:", "").replace("<", "").replace(">", "").strip()
            dial_string = f"sofia/internal/{contact}"
        else:
            dial_string = f"sofia/internal/{destination}"
        
        # Variáveis do originate
        originate_vars = (
            f"origination_uuid={b_leg_uuid},"
            f"origination_caller_id_number={self.caller_id},"
            f"origination_caller_id_name=Secretaria_Virtual,"
            f"originate_timeout={self.config.originate_timeout},"
            f"ignore_early_media=true,"
            f"hangup_after_bridge=true"
        )
        
        cmd = f"bgapi originate {{{originate_vars}}}{dial_string} 'answer:,park:' inline"
        
        logger.info(f"{self._elapsed()} 📞 Dial: {dial_string}")
        
        success, result = await self._esl_command(cmd, timeout=5.0, description="ORIGINATE_B")
        if not success and "Job-UUID" not in result:
            logger.error(f"{self._elapsed()} ❌ Originate falhou")
            return None
        
        # Polling para aguardar atendimento
        logger.info(f"{self._elapsed()} ⏳ Aguardando B-leg atender (max {self.config.originate_timeout}s)...")
        max_attempts = int(self.config.originate_timeout / self.config.originate_poll_interval)
        
        for attempt in range(max_attempts):
            # CLEANUP: Verificar hangup do A-leg
            if self._a_leg_hangup_event.is_set():
                logger.warning(f"{self._elapsed()} 🔴 A-leg desligou durante originate")
                await self._kill_b_leg_safe()
                return None
            
            # Verificar se B-leg existe (atendeu)
            if await self._verify_channel_exists(b_leg_uuid, "B-leg"):
                logger.info(f"{self._elapsed()} ✅ B-leg {b_leg_uuid[:8]}... atendeu!")
                self._log_state_change(TransferState.B_LEG_ANSWERED)
                return b_leg_uuid
            
            # Verificar hangup do B-leg (rejeitou)
            if self._b_leg_hangup_event.is_set():
                logger.warning(f"{self._elapsed()} 🔴 B-leg rejeitou/não atendeu")
                return None
            
            await asyncio.sleep(self.config.originate_poll_interval)
        
        logger.warning(f"{self._elapsed()} ⏰ Timeout aguardando B-leg")
        await self._kill_b_leg_safe()
        return None
    
    async def _kill_b_leg_safe(self) -> None:
        """Desliga B-leg se existir (com timeout e sem exceções)."""
        if self._b_leg_uuid:
            try:
                await self._esl_command(
                    f"uuid_kill {self._b_leg_uuid}",
                    timeout=self.config.cleanup_timeout,
                    description="KILL_B"
                )
            except Exception as e:
                logger.debug(f"Kill B-leg error (ignorado): {e}")
    
    # =========================================================================
    # ANÚNCIO
    # =========================================================================
    
    async def _run_announcement(
        self,
        announcement: str,
        caller_name: str
    ) -> TransferDecision:
        """
        Executa anúncio para o atendente via OpenAI Realtime.
        
        PROTEÇÃO: Verifica hangup do A-leg durante anúncio.
        """
        self._log_state_change(TransferState.ANNOUNCING)
        
        session = ConferenceAnnouncementSession(
            b_leg_uuid=self._b_leg_uuid,
            esl=self.esl,
            openai_model=self.config.openai_model,
            openai_voice=self.config.openai_voice,
            announcement_prompt=self.config.announcement_prompt,
            timeout=self.config.announcement_timeout,
            a_leg_hangup_event=self._a_leg_hangup_event,
        )
        
        try:
            accepted, rejected = await session.run(
                initial_message=announcement,
                caller_name=caller_name
            )
            
            # CLEANUP: Verificar se A-leg desligou durante anúncio
            if self._a_leg_hangup_event.is_set():
                logger.info(f"{self._elapsed()} A-leg desligou durante anúncio")
                return TransferDecision.HANGUP
            
            if accepted:
                return TransferDecision.ACCEPTED
            elif rejected:
                return TransferDecision.REJECTED
            else:
                return TransferDecision.TIMEOUT
                
        except Exception as e:
            logger.error(f"{self._elapsed()} ❌ Erro no anúncio: {e}")
            return TransferDecision.ERROR
    
    # =========================================================================
    # BRIDGE
    # =========================================================================
    
    async def _bridge_legs(self) -> bool:
        """
        Cria bridge entre A-leg e B-leg.
        
        IMPORTANTE: hangup_after_bridge=true foi setado no originate,
        então quando qualquer perna desligar, a outra também será desligada.
        """
        self._log_state_change(TransferState.BRIDGING)
        
        try:
            # 1. Parar audio stream do B-leg (estava com anúncio)
            await self._esl_command(
                f"uuid_audio_stream {self._b_leg_uuid} stop",
                timeout=self.config.cleanup_timeout,
                description="STOP_B"
            )
            
            # 2. Parar audio stream do A-leg (estava pausado)
            await self._esl_command(
                f"uuid_audio_stream {self.a_leg_uuid} stop",
                timeout=self.config.cleanup_timeout,
                description="STOP_A"
            )
            
            # 3. Delay para garantir que streams pararam
            await asyncio.sleep(self.config.stabilization_delay)
            
            # 4. Criar bridge
            success, result = await self._esl_command(
                f"uuid_bridge {self.a_leg_uuid} {self._b_leg_uuid}",
                timeout=self.config.bridge_timeout,
                description="BRIDGE"
            )
            
            if success:
                self._log_state_change(TransferState.BRIDGED)
                logger.info(f"{self._elapsed()} ✅ Bridge estabelecido!")
                return True
            else:
                logger.error(f"{self._elapsed()} ❌ Bridge falhou: {result}")
                return False
                
        except Exception as e:
            logger.error(f"{self._elapsed()} ❌ Erro ao criar bridge: {e}")
            return False
    
    # =========================================================================
    # ROLLBACK
    # =========================================================================
    
    async def _rollback(self, reason: str) -> None:
        """
        Rollback: volta ao estado anterior ao transfer.
        
        Dependendo do estado atual:
        - A_LEG_PAUSED: resume A-leg
        - B_LEG_*: kill B-leg, resume A-leg
        - ANNOUNCING: kill B-leg, resume A-leg
        """
        self._log_state_change(TransferState.ROLLING_BACK, reason)
        
        # Matar B-leg se existir
        if self._b_leg_uuid:
            await self._kill_b_leg_safe()
        
        # Resumir A-leg se estava pausado
        if self._state in (
            TransferState.A_LEG_PAUSED,
            TransferState.B_LEG_RINGING,
            TransferState.B_LEG_ANSWERED,
            TransferState.ANNOUNCING,
            TransferState.BRIDGING,
            TransferState.ROLLING_BACK,
        ):
            await self._resume_a_leg_audio()
    
    # =========================================================================
    # MÉTODO PRINCIPAL
    # =========================================================================
    
    async def execute_announced_transfer(
        self,
        destination: str,
        context: str,
        announcement: str,
        caller_name: str = "Cliente",
        direct_contact: Optional[str] = None,
    ) -> BridgeTransferResult:
        """
        Executa transferência anunciada usando bridge.
        
        GARANTIAS:
        - try/finally garante cleanup em caso de exceção
        - Cada passo verifica estado e faz rollback se necessário
        - Hangup do A-leg em qualquer momento cancela transferência
        """
        self._start_time = time.time()
        self._state = TransferState.IDLE
        
        logger.info("=" * 70)
        logger.info("🎯 ANNOUNCED TRANSFER - uuid_bridge")
        logger.info(f"   A-leg: {self.a_leg_uuid[:12]}...")
        logger.info(f"   Destination: {destination}")
        logger.info(f"   Context: {context}")
        logger.info(f"   Caller: {caller_name}")
        logger.info(f"   Timeouts: originate={self.config.originate_timeout}s, "
                   f"announce={self.config.announcement_timeout}s")
        logger.info("=" * 70)
        
        try:
            # STEP 0: Iniciar monitor de hangup
            await self._start_hangup_monitor()
            
            # STEP 1: Verificar A-leg existe
            logger.info(f"{self._elapsed()} 📍 STEP 1: Verificando A-leg...")
            if not await self._verify_channel_exists(self.a_leg_uuid, "A-leg"):
                self._log_state_change(TransferState.FAILED, "A-leg não existe")
                return BridgeTransferResult(
                    success=False,
                    decision=TransferDecision.ERROR,
                    error="A-leg não existe",
                    final_state=self._state
                )
            
            # STEP 2: Pausar A-leg (cliente em silêncio)
            logger.info(f"{self._elapsed()} 📍 STEP 2: Pausando A-leg...")
            if not await self._pause_a_leg_audio():
                self._log_state_change(TransferState.FAILED, "Falha ao pausar A-leg")
                return BridgeTransferResult(
                    success=False,
                    decision=TransferDecision.ERROR,
                    error="Falha ao pausar A-leg",
                    final_state=self._state
                )
            
            # STEP 3: Originar B-leg
            logger.info(f"{self._elapsed()} 📍 STEP 3: Originando B-leg...")
            b_leg_uuid = await self._originate_b_leg(destination, direct_contact)
            
            if not b_leg_uuid:
                # B-leg não atendeu - ROLLBACK
                logger.warning(f"{self._elapsed()} ❌ B-leg não atendeu - ROLLBACK")
                await self._rollback("B-leg não atendeu")
                await self._emit_event(VoiceEventType.TRANSFER_TIMEOUT, reason="no_answer")
                return BridgeTransferResult(
                    success=False,
                    decision=TransferDecision.TIMEOUT,
                    error="Ramal não atendeu",
                    final_state=self._state
                )
            
            await self._emit_event(VoiceEventType.TRANSFER_ANSWERED)
            
            # Estabilização após atendimento
            await asyncio.sleep(self.config.stabilization_delay * 2)
            
            # STEP 4: Anúncio
            logger.info(f"{self._elapsed()} 📍 STEP 4: Anunciando para atendente...")
            decision = await self._run_announcement(announcement, caller_name)
            logger.info(f"{self._elapsed()} 📋 Decisão: {decision.value}")
            
            # STEP 5: Processar decisão
            logger.info(f"{self._elapsed()} 📍 STEP 5: Processando decisão...")
            
            if decision == TransferDecision.ACCEPTED:
                # Criar bridge
                if await self._bridge_legs():
                    self._log_state_change(TransferState.COMPLETED, "Bridge OK")
                    await self._emit_event(VoiceEventType.TRANSFER_COMPLETED)
                    return BridgeTransferResult(
                        success=True,
                        decision=TransferDecision.ACCEPTED,
                        b_leg_uuid=b_leg_uuid,
                        duration_ms=int((time.time() - self._start_time) * 1000),
                        final_state=self._state
                    )
                else:
                    # Bridge falhou - ROLLBACK
                    logger.error(f"{self._elapsed()} ❌ Bridge falhou - ROLLBACK")
                    await self._rollback("Bridge falhou")
                    return BridgeTransferResult(
                        success=False,
                        decision=TransferDecision.ERROR,
                        error="Falha ao criar bridge",
                        final_state=self._state
                    )
            
            elif decision == TransferDecision.HANGUP:
                # Cliente desligou - só cleanup
                self._log_state_change(TransferState.FAILED, "Cliente desligou")
                await self._kill_b_leg_safe()
                return BridgeTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    error="Cliente desligou",
                    final_state=self._state
                )
            
            else:
                # Rejeitado, timeout ou erro - ROLLBACK
                logger.info(f"{self._elapsed()} ❌ Não aceito ({decision.value}) - ROLLBACK")
                await self._rollback(f"decision={decision.value}")
                await self._emit_event(VoiceEventType.TRANSFER_REJECTED, reason=decision.value)
                return BridgeTransferResult(
                    success=False,
                    decision=decision,
                    b_leg_uuid=b_leg_uuid,
                    error="Atendente não aceitou",
                    final_state=self._state
                )
        
        except Exception as e:
            # EXCEÇÃO INESPERADA - ROLLBACK
            logger.exception(f"{self._elapsed()} ❌ EXCEÇÃO: {e}")
            await self._rollback(f"exception: {e}")
            return BridgeTransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error=str(e),
                final_state=self._state
            )
        
        finally:
            # CLEANUP: Sempre parar monitor de hangup
            await self._stop_hangup_monitor()
            
            duration = time.time() - self._start_time
            logger.info(f"{self._elapsed()} 🏁 Transfer finalizado em {duration:.2f}s - Estado: {self._state.value}")
