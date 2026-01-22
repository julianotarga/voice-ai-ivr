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
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
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


@dataclass
class BridgeTransferResult:
    """Resultado da transferência anunciada via bridge."""
    success: bool
    decision: TransferDecision
    b_leg_uuid: Optional[str] = None
    error: Optional[str] = None
    ticket_id: Optional[str] = None
    duration_ms: int = 0


@dataclass
class BridgeTransferConfig:
    """Configuração para transferência via bridge."""
    # Timeouts
    originate_timeout: int = 30
    announcement_timeout: float = 30.0
    
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
    
    Uso:
        manager = BridgeTransferManager(
            esl_client=esl,
            a_leg_uuid="xxx",
            domain="empresa.com.br",
            caller_id="5511999999999",
        )
        
        result = await manager.execute_announced_transfer(
            destination="1001",
            context="internet lenta",
            announcement="Cliente João com problema de internet"
        )
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
        
        # Estado
        self._b_leg_uuid: Optional[str] = None
        self._start_time: float = 0
        self._a_leg_hangup_event = asyncio.Event()
        self._b_leg_hangup_event = asyncio.Event()
        self._hangup_monitor_id: Optional[str] = None
        
    def _elapsed(self) -> str:
        """Retorna tempo decorrido formatado."""
        return f"[{time.time() - self._start_time:.2f}s]"
    
    async def _emit_event(self, event_type: VoiceEventType, **kwargs) -> None:
        """Emite evento no EventBus."""
        if self.event_bus:
            try:
                await self.event_bus.emit(
                    event_type.value,
                    call_uuid=self.a_leg_uuid,
                    **kwargs
                )
            except Exception as e:
                logger.debug(f"Could not emit event {event_type}: {e}")
    
    async def _start_hangup_monitor(self) -> None:
        """Inicia monitor de hangup para ambas as pernas."""
        self._hangup_monitor_id = str(uuid4())
        
        async def on_hangup(uuid: str, cause: str, **kwargs):
            if uuid == self.a_leg_uuid:
                logger.info(f"🔴 [HANGUP_MONITOR] A-leg hangup: {cause}")
                self._a_leg_hangup_event.set()
            elif uuid == self._b_leg_uuid:
                logger.info(f"🔴 [HANGUP_MONITOR] B-leg hangup: {cause}")
                self._b_leg_hangup_event.set()
        
        if self.event_bus:
            self.event_bus.on("channel_hangup", on_hangup)
        
        logger.debug(f"[HANGUP_MONITOR] Handler registrado: {self._hangup_monitor_id}")
    
    async def _stop_hangup_monitor(self) -> None:
        """Para monitor de hangup."""
        if self._hangup_monitor_id:
            logger.debug(f"[HANGUP_MONITOR] Handler removido: {self._hangup_monitor_id}")
            self._hangup_monitor_id = None
    
    async def _verify_a_leg_exists(self) -> bool:
        """Verifica se A-leg ainda existe."""
        try:
            return await asyncio.wait_for(
                self.esl.uuid_exists(self.a_leg_uuid),
                timeout=2.0
            )
        except Exception:
            return False
    
    async def _pause_a_leg_audio(self) -> bool:
        """Pausa o audio stream do A-leg (cliente em silêncio)."""
        logger.info(f"{self._elapsed()} Pausando audio do A-leg...")
        try:
            result = await asyncio.wait_for(
                self.esl.execute_api(f"uuid_audio_stream {self.a_leg_uuid} pause"),
                timeout=3.0
            )
            result_str = result if isinstance(result, str) else str(result)
            if "+OK" in result_str or "Success" in result_str:
                logger.info(f"{self._elapsed()} ✅ A-leg audio pausado")
                return True
            else:
                logger.warning(f"{self._elapsed()} ⚠️ Pause retornou: {result_str}")
                return True  # Continuar mesmo assim
        except Exception as e:
            logger.error(f"{self._elapsed()} ❌ Erro ao pausar A-leg: {e}")
            return False
    
    async def _resume_a_leg_audio(self) -> bool:
        """
        Resume o audio stream do A-leg.
        
        IMPORTANTE: Durante o HOLD, o WebSocket pode ter fechado por timeout.
        Por isso tentamos:
        1. resume - se WebSocket ainda está aberto, funciona
        2. start - reconecta WebSocket se fechou
        
        O RealtimeServer é projetado para REUTILIZAR a sessão existente
        quando uma nova conexão chega para o mesmo call_uuid.
        """
        logger.info(f"{self._elapsed()} Resumindo audio do A-leg...")
        
        # Primeiro verificar se A-leg ainda existe
        try:
            a_exists = await asyncio.wait_for(
                self.esl.uuid_exists(self.a_leg_uuid),
                timeout=2.0
            )
            if not a_exists:
                logger.error(f"{self._elapsed()} ❌ A-leg não existe mais!")
                return False
        except Exception as e:
            logger.warning(f"{self._elapsed()} ⚠️ Não foi possível verificar A-leg: {e}")
            # Continuar mesmo assim
        
        try:
            # Tentar resume primeiro (mais rápido se WebSocket ainda está aberto)
            result = await asyncio.wait_for(
                self.esl.execute_api(f"uuid_audio_stream {self.a_leg_uuid} resume"),
                timeout=3.0
            )
            result_str = result if isinstance(result, str) else str(result)
            
            if "+OK" in result_str or "Success" in result_str:
                logger.info(f"{self._elapsed()} ✅ A-leg audio resumido via RESUME")
                return True
            
            # Resume falhou - WebSocket pode ter fechado
            # Tentar reconectar com start
            logger.warning(f"{self._elapsed()} Resume falhou ({result_str}), tentando START...")
            
            # Construir URL do WebSocket
            ws_host = os.getenv("REALTIME_WS_HOST", "127.0.0.1")
            ws_port = os.getenv("REALTIME_WS_PORT", "8085")
            sec_uuid = self.secretary_uuid or "unknown"
            ws_url = f"ws://{ws_host}:{ws_port}/stream/{sec_uuid}/{self.a_leg_uuid}/{self.caller_id}"
            
            logger.info(f"{self._elapsed()} Reconectando: {ws_url}")
            
            # Parar stream anterior (pode estar em estado inconsistente)
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_audio_stream {self.a_leg_uuid} stop"),
                    timeout=2.0
                )
                await asyncio.sleep(0.1)  # Pequeno delay
            except Exception:
                pass  # Ignorar erros no stop
            
            # Iniciar nova conexão
            result = await asyncio.wait_for(
                self.esl.execute_api(f"uuid_audio_stream {self.a_leg_uuid} start {ws_url} mono 8k"),
                timeout=5.0
            )
            result_str = result if isinstance(result, str) else str(result)
            
            if "+OK" in result_str or "Success" in result_str:
                logger.info(f"{self._elapsed()} ✅ A-leg audio reconectado via START")
                
                # Aguardar conexão estabelecer
                await asyncio.sleep(0.3)
                return True
            else:
                logger.error(f"{self._elapsed()} ❌ START também falhou: {result_str}")
                return False
                
        except asyncio.TimeoutError:
            logger.error(f"{self._elapsed()} ❌ Timeout ao resumir A-leg")
            return False
        except Exception as e:
            logger.error(f"{self._elapsed()} ❌ Erro ao resumir A-leg: {e}")
            return False
    
    async def _originate_b_leg(
        self,
        destination: str,
        direct_contact: Optional[str] = None
    ) -> Optional[str]:
        """
        Origina B-leg (atendente) para park.
        
        Returns:
            UUID do B-leg se atendeu, None caso contrário
        """
        logger.info(f"{self._elapsed()} 📞 Originando B-leg para {destination}...")
        
        # Gerar UUID para o B-leg
        b_leg_uuid = str(uuid4())
        self._b_leg_uuid = b_leg_uuid
        
        # Determinar dial string
        if direct_contact:
            # Usar contato direto (já registrado)
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
            f"hangup_after_bridge=true"  # IMPORTANTE: hangup automático!
        )
        
        # Comando: origina e vai para park
        cmd = f"bgapi originate {{{originate_vars}}}{dial_string} 'answer:,park:' inline"
        
        logger.info(f"{self._elapsed()} Dial string: {dial_string}")
        logger.debug(f"{self._elapsed()} Originate vars: {originate_vars}")
        
        try:
            result = await asyncio.wait_for(
                self.esl.execute_api(cmd),
                timeout=5.0
            )
            logger.info(f"{self._elapsed()} bgapi result: {result}")
        except Exception as e:
            logger.error(f"{self._elapsed()} ❌ Originate falhou: {e}")
            return None
        
        # Polling para aguardar atendimento
        logger.info(f"{self._elapsed()} Aguardando B-leg atender...")
        max_attempts = self.config.originate_timeout * 2  # 2x por segundo
        
        for attempt in range(max_attempts):
            # Verificar hangup do A-leg
            if self._a_leg_hangup_event.is_set():
                logger.warning(f"{self._elapsed()} A-leg desligou durante originate")
                await self._kill_b_leg()
                return None
            
            # Verificar se B-leg existe (atendeu)
            try:
                b_exists = await asyncio.wait_for(
                    self.esl.uuid_exists(b_leg_uuid),
                    timeout=1.0
                )
                if b_exists:
                    logger.info(f"{self._elapsed()} ✅ B-leg {b_leg_uuid} atendeu!")
                    return b_leg_uuid
            except Exception:
                pass
            
            # Verificar hangup do B-leg (rejeitou)
            if self._b_leg_hangup_event.is_set():
                logger.warning(f"{self._elapsed()} B-leg rejeitou/não atendeu")
                return None
            
            await asyncio.sleep(0.5)
        
        logger.warning(f"{self._elapsed()} ⏰ Timeout aguardando B-leg")
        await self._kill_b_leg()
        return None
    
    async def _kill_b_leg(self) -> None:
        """Desliga B-leg se existir."""
        if self._b_leg_uuid:
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_kill {self._b_leg_uuid}"),
                    timeout=2.0
                )
                logger.info(f"{self._elapsed()} B-leg {self._b_leg_uuid} desligado")
            except Exception as e:
                logger.debug(f"Kill B-leg error: {e}")
    
    async def _run_announcement(
        self,
        announcement: str,
        caller_name: str
    ) -> TransferDecision:
        """
        Executa anúncio para o atendente via OpenAI Realtime.
        
        O B-leg está em PARK e recebe audio stream conectado ao OpenAI.
        """
        logger.info(f"{self._elapsed()} 🎤 Iniciando anúncio para B-leg...")
        
        # Criar sessão de anúncio (reutiliza a existente)
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
            
            if self._a_leg_hangup_event.is_set():
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
    
    async def _bridge_legs(self) -> bool:
        """
        Cria bridge entre A-leg e B-leg.
        
        IMPORTANTE: hangup_after_bridge=true já foi setado no originate,
        então quando qualquer perna desligar, a outra também será desligada.
        """
        logger.info(f"{self._elapsed()} 🔗 Criando bridge entre A-leg e B-leg...")
        
        try:
            # Parar audio streams primeiro
            logger.info(f"{self._elapsed()} Parando audio streams...")
            
            # Stop no B-leg (estava com anúncio)
            await asyncio.wait_for(
                self.esl.execute_api(f"uuid_audio_stream {self._b_leg_uuid} stop"),
                timeout=2.0
            )
            
            # Stop no A-leg (estava pausado)
            await asyncio.wait_for(
                self.esl.execute_api(f"uuid_audio_stream {self.a_leg_uuid} stop"),
                timeout=2.0
            )
            
            # Pequeno delay para garantir que streams pararam
            await asyncio.sleep(0.2)
            
            # Criar bridge
            # uuid_bridge <a_leg> <b_leg>
            result = await asyncio.wait_for(
                self.esl.execute_api(f"uuid_bridge {self.a_leg_uuid} {self._b_leg_uuid}"),
                timeout=5.0
            )
            result_str = result if isinstance(result, str) else str(result)
            
            if "+OK" in result_str or "Success" in result_str:
                logger.info(f"{self._elapsed()} ✅ Bridge criado com sucesso!")
                return True
            else:
                logger.error(f"{self._elapsed()} ❌ Bridge falhou: {result_str}")
                return False
                
        except Exception as e:
            logger.error(f"{self._elapsed()} ❌ Erro ao criar bridge: {e}")
            return False
    
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
        
        Args:
            destination: Ramal destino (ex: "1001@domain.com")
            context: Contexto/motivo da transferência
            announcement: Mensagem de anúncio para o atendente
            caller_name: Nome do cliente
            direct_contact: Contato SIP direto (se disponível)
            
        Returns:
            BridgeTransferResult com o resultado
        """
        self._start_time = time.time()
        
        logger.info("=" * 70)
        logger.info("🎯 ANNOUNCED TRANSFER - uuid_bridge")
        logger.info(f"   A-leg UUID: {self.a_leg_uuid}")
        logger.info(f"   Destination: {destination}")
        logger.info(f"   Context: {context}")
        logger.info(f"   Caller: {caller_name}")
        logger.info("=" * 70)
        
        # STEP 0: Iniciar monitor de hangup
        await self._start_hangup_monitor()
        
        # STEP 1: Verificar A-leg
        logger.info(f"{self._elapsed()} 📍 STEP 1: Verificando A-leg...")
        if not await self._verify_a_leg_exists():
            logger.error(f"{self._elapsed()} ❌ A-leg não existe!")
            await self._stop_hangup_monitor()
            return BridgeTransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error="A-leg não existe"
            )
        logger.info(f"{self._elapsed()} ✅ A-leg existe")
        
        # STEP 2: Pausar A-leg (cliente em silêncio)
        logger.info(f"{self._elapsed()} 📍 STEP 2: Pausando A-leg...")
        await self._pause_a_leg_audio()
        logger.info(f"{self._elapsed()} ✅ A-leg em silêncio")
        
        # STEP 3: Originar B-leg
        logger.info(f"{self._elapsed()} 📍 STEP 3: Originando B-leg...")
        b_leg_uuid = await self._originate_b_leg(destination, direct_contact)
        
        if not b_leg_uuid:
            # B-leg não atendeu
            logger.warning(f"{self._elapsed()} ❌ B-leg não atendeu")
            await self._resume_a_leg_audio()
            await self._stop_hangup_monitor()
            await self._emit_event(VoiceEventType.TRANSFER_TIMEOUT, reason="no_answer")
            return BridgeTransferResult(
                success=False,
                decision=TransferDecision.TIMEOUT,
                error="Ramal não atendeu"
            )
        
        logger.info(f"{self._elapsed()} ✅ B-leg atendeu: {b_leg_uuid}")
        await self._emit_event(VoiceEventType.TRANSFER_ANSWERED)
        
        # Aguardar estabilização
        await asyncio.sleep(1.0)
        
        # STEP 4: Anúncio
        logger.info(f"{self._elapsed()} 📍 STEP 4: Anunciando para atendente...")
        decision = await self._run_announcement(announcement, caller_name)
        logger.info(f"{self._elapsed()} Decisão: {decision.value}")
        
        # STEP 5: Processar decisão
        logger.info(f"{self._elapsed()} 📍 STEP 5: Processando decisão...")
        
        if decision == TransferDecision.ACCEPTED:
            # Criar bridge
            bridge_ok = await self._bridge_legs()
            
            if bridge_ok:
                logger.info(f"{self._elapsed()} ✅ SUCESSO - Bridge estabelecido!")
                await self._stop_hangup_monitor()
                await self._emit_event(VoiceEventType.TRANSFER_COMPLETED)
                return BridgeTransferResult(
                    success=True,
                    decision=TransferDecision.ACCEPTED,
                    b_leg_uuid=b_leg_uuid,
                    duration_ms=int((time.time() - self._start_time) * 1000)
                )
            else:
                # Bridge falhou - retornar ao Voice AI
                logger.error(f"{self._elapsed()} ❌ Bridge falhou")
                await self._kill_b_leg()
                await self._resume_a_leg_audio()
                await self._stop_hangup_monitor()
                return BridgeTransferResult(
                    success=False,
                    decision=TransferDecision.ERROR,
                    error="Falha ao criar bridge"
                )
        
        elif decision == TransferDecision.HANGUP:
            # Cliente desligou
            logger.info(f"{self._elapsed()} 📞 Cliente desligou durante transferência")
            await self._kill_b_leg()
            await self._stop_hangup_monitor()
            return BridgeTransferResult(
                success=False,
                decision=TransferDecision.HANGUP,
                error="Cliente desligou"
            )
        
        else:
            # Rejeitado, timeout ou erro - retornar ao Voice AI
            logger.info(f"{self._elapsed()} ❌ Transferência não aceita: {decision.value}")
            await self._kill_b_leg()
            await self._resume_a_leg_audio()
            await self._stop_hangup_monitor()
            await self._emit_event(VoiceEventType.TRANSFER_REJECTED, reason=decision.value)
            return BridgeTransferResult(
                success=False,
                decision=decision,
                b_leg_uuid=b_leg_uuid,
                error="Atendente não aceitou"
            )
