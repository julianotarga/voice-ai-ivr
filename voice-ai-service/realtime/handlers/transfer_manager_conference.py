"""
Transferência anunciada usando mod_conference do FreeSWITCH.

Substitui a abordagem de &park() que apresentava problemas de áudio.
Usa conferência temporária para conectar A-leg (cliente) e B-leg (atendente).

Ref: voice-ai-ivr/docs/announced-transfer-conference.md
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Awaitable, Any
from uuid import uuid4

from .esl_client import AsyncESLClient

logger = logging.getLogger(__name__)


class TransferDecision(Enum):
    """Decisão do atendente sobre a transferência."""
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    HANGUP = "hangup"
    ERROR = "error"


@dataclass
class ConferenceTransferResult:
    """Resultado da transferência anunciada via conferência."""
    success: bool
    decision: TransferDecision
    b_leg_uuid: Optional[str] = None
    conference_name: Optional[str] = None
    error: Optional[str] = None
    ticket_id: Optional[str] = None
    duration_ms: int = 0


@dataclass
class ConferenceTransferConfig:
    """Configuração para transferência via conferência."""
    # Timeouts
    originate_timeout: int = 30
    announcement_timeout: float = 15.0
    dtmf_timeout: float = 10.0
    
    # Conferência
    conference_profile: str = "default"
    moh_sound: str = "local_stream://default"
    
    # OpenAI
    openai_model: str = "gpt-realtime"
    openai_voice: str = "marin"
    
    # Comportamento
    accept_on_timeout: bool = True  # Se timeout, assume aceitação


class ConferenceTransferManager:
    """
    Gerencia transferências anunciadas usando mod_conference.
    
    Fluxo:
    1. Cria conferência temporária única
    2. Move A-leg (cliente) para conferência com flags {mute}
    3. Origina B-leg (atendente) para conferência como moderador
    4. OpenAI anuncia para B-leg via uuid_audio_stream
    5. B-leg aceita/recusa via função call ou DTMF
    6. Se aceito: unmute A-leg, ambos conversam
    7. Se recusado: kick B-leg, retornar A-leg ao Voice AI
    
    Uso:
        manager = ConferenceTransferManager(
            esl_client=esl,
            a_leg_uuid="xxx",
            domain="empresa.com.br",
            caller_id="5511999999999",
        )
        
        result = await manager.execute_announced_transfer(
            destination="1001",
            context="vendas",
            announcement="Cliente João solicitando informações sobre planos"
        )
        
        if result.success:
            # Chamada conectada
        else:
            # Criar ticket, retornar ao IVR
    """
    
    def __init__(
        self,
        esl_client: AsyncESLClient,
        a_leg_uuid: str,
        domain: str,
        caller_id: str,
        config: Optional[ConferenceTransferConfig] = None,
        openai_api_key: Optional[str] = None,
        on_resume: Optional[Callable[[], Awaitable[Any]]] = None,
        omniplay_api: Any = None,
    ):
        """
        Inicializa o transfer manager.
        
        Args:
            esl_client: Cliente ESL para comandos FreeSWITCH
            a_leg_uuid: UUID do A-leg (cliente)
            domain: Domínio SIP (ex: empresa.com.br)
            caller_id: Caller ID do cliente
            config: Configurações opcionais
            openai_api_key: API key OpenAI (usa env se não fornecida)
            on_resume: Callback para retomar Voice AI após falha
            omniplay_api: API OmniPlay para criar tickets (opcional)
        """
        self.esl = esl_client
        self.a_leg_uuid = a_leg_uuid
        self.domain = domain
        self.caller_id = caller_id
        self.config = config or ConferenceTransferConfig()
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.on_resume = on_resume
        self.omniplay_api = omniplay_api
        
        # Estado da transferência
        self.b_leg_uuid: Optional[str] = None
        self.conference_name: Optional[str] = None
        self._announcement_session = None
        self._decision: Optional[TransferDecision] = None
        
        # Monitoramento de hangup em tempo real
        self._a_leg_hangup_event = asyncio.Event()
        self._b_leg_hangup_event = asyncio.Event()
        self._hangup_monitor_task: Optional[asyncio.Task] = None
        self._transfer_active = False
        self._hangup_handler_id: Optional[str] = None
    
    async def execute_announced_transfer(
        self,
        destination: str,
        context: str,
        announcement: str,
        caller_name: Optional[str] = None,
    ) -> ConferenceTransferResult:
        """
        Executa transferência anunciada completa.
        
        Este é o método PRINCIPAL que orquestra todo o fluxo.
        
        Args:
            destination: Extensão destino (ex: "1001")
            context: Contexto da transferência (ex: "vendas")
            announcement: Texto do anúncio para o atendente
            caller_name: Nome do cliente (opcional)
            
        Returns:
            ConferenceTransferResult com resultado da operação
        """
        start_time = time.time()
        
        def elapsed() -> str:
            """Retorna tempo decorrido formatado."""
            return f"[{time.time() - start_time:.2f}s]"
        
        logger.info("=" * 70)
        logger.info("🎯 ANNOUNCED TRANSFER - mod_conference")
        logger.info(f"   A-leg UUID: {self.a_leg_uuid}")
        logger.info(f"   Destination: {destination}@{self.domain}")
        logger.info(f"   Context: {context}")
        logger.info(f"   Caller: {caller_name or self.caller_id}")
        logger.info("=" * 70)
        
        try:
            # ============================================================
            # STEP 0: Verificar e garantir conexão ESL + Iniciar monitor
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 0: Verificando conexão ESL...")
            logger.info(f"{elapsed()} STEP 0: ESL client type: {type(self.esl).__name__}")
            
            # Verificar se ESL está conectado
            is_connected = False
            if hasattr(self.esl, '_connected'):
                is_connected = self.esl._connected
            elif hasattr(self.esl, 'is_connected'):
                is_connected = self.esl.is_connected
            
            logger.info(f"{elapsed()} STEP 0: ESL connected = {is_connected}")
            
            if not is_connected:
                logger.info(f"{elapsed()} STEP 0: ESL not connected, attempting connection...")
                try:
                    await asyncio.wait_for(self.esl.connect(), timeout=5.0)
                    logger.info(f"{elapsed()} STEP 0: ✅ ESL connected successfully")
                except Exception as e:
                    logger.error(f"{elapsed()} STEP 0: ❌ Failed to connect ESL: {e}")
                    return ConferenceTransferResult(
                        success=False,
                        decision=TransferDecision.ERROR,
                        error="Falha na conexão com FreeSWITCH"
                    )
            else:
                logger.info(f"{elapsed()} STEP 0: ✅ ESL already connected")
            
            # Iniciar monitoramento de hangup em tempo real
            logger.info(f"{elapsed()} STEP 0: Iniciando monitor de hangup...")
            await self._start_hangup_monitor()
            logger.info(f"{elapsed()} STEP 0: ✅ Monitor de hangup ativo")
            
            # ============================================================
            # STEP 1: Verificar A-leg ainda existe
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 1: Verificando se A-leg existe...")
            
            # Usar método que combina event check + uuid_exists
            if not await self._verify_a_leg_alive("STEP 1"):
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    error="Cliente desligou antes da transferência"
                )
            logger.info(f"{elapsed()} STEP 1: ✅ A-leg exists")
            
            # ============================================================
            # STEP 2: Verificar disponibilidade do ramal ANTES de colocar em espera
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 2: Verificando disponibilidade do ramal {destination}...")
            
            # Checar hangup antes de operação longa
            if self._check_a_leg_hangup():
                logger.warning(f"{elapsed()} STEP 2: 🚨 Cliente desligou antes de verificar ramal")
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    error="Cliente desligou antes da transferência"
                )
            
            # Armazena contact para usar no originate (evita loop de lookup)
            direct_contact: Optional[str] = None
            
            try:
                is_registered, contact, check_ok = await asyncio.wait_for(
                    self.esl.check_extension_registered(destination, self.domain),
                    timeout=5.0
                )
                logger.info(f"{elapsed()} STEP 2: Ramal registrado: {is_registered}, contact: {contact}")
                
                # Guardar contact para usar no originate
                if is_registered and contact:
                    direct_contact = contact
                    logger.info(f"{elapsed()} STEP 2: 📍 Direct contact disponível: {direct_contact}")
                    
            except asyncio.TimeoutError:
                logger.warning(f"{elapsed()} STEP 2: ⚠️ Timeout verificando ramal, assumindo disponível")
                is_registered = True
                check_ok = False
            except Exception as e:
                logger.warning(f"{elapsed()} STEP 2: ⚠️ Erro verificando ramal: {e}, assumindo disponível")
                is_registered = True
                check_ok = False
            
            if check_ok and not is_registered:
                logger.warning(f"{elapsed()} STEP 2: ❌ Ramal {destination} não está registrado/online")
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.REJECTED,
                    error=f"Ramal {destination} não está disponível no momento",
                    duration_ms=int((time.time() - start_time) * 1000)
                )
            logger.info(f"{elapsed()} STEP 2: ✅ Ramal disponível")
            
            # ============================================================
            # STEP 3: Colocar cliente em espera (conferência mutada)
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 3: Colocando cliente em espera...")
            
            # Checar hangup antes de modificar estado
            if self._check_a_leg_hangup():
                logger.warning(f"{elapsed()} STEP 3: 🚨 Cliente desligou antes de entrar na conferência")
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    error="Cliente desligou antes de ser colocado em espera"
                )
            
            self.conference_name = self._generate_conference_name()
            logger.info(f"{elapsed()} STEP 3: Conference name: {self.conference_name}")
            
            logger.info(f"{elapsed()} STEP 3: Parando Voice AI stream...")
            await self._stop_voiceai_stream()
            logger.info(f"{elapsed()} STEP 3: ✅ Voice AI stream parado")
            
            logger.info(f"{elapsed()} STEP 3: Movendo A-leg para conferência (mutado = em espera)...")
            await self._move_a_leg_to_conference()
            logger.info(f"{elapsed()} STEP 3: ✅ Cliente em espera (conferência mutada)")
            
            # Verificar se A-leg ainda existe após mover
            logger.info(f"{elapsed()} STEP 3: Verificando se cliente ainda está na linha...")
            if not await self._verify_a_leg_alive("STEP 3"):
                logger.warning(f"{elapsed()} STEP 3: ❌ Cliente desligou durante espera")
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    error="Cliente desligou durante transferência"
                )
            logger.info(f"{elapsed()} STEP 3: ✅ Cliente ainda na linha")
            
            # ============================================================
            # STEP 4: Chamar o ramal (B-leg)
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 4: Chamando ramal {destination}...")
            
            # Checar hangup antes de originar
            if self._check_a_leg_hangup():
                logger.warning(f"{elapsed()} STEP 4: 🚨 Cliente desligou antes de chamar ramal")
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    conference_name=self.conference_name,
                    error="Cliente desligou durante transferência"
                )
            
            originate_success = await self._originate_b_leg(destination, direct_contact)
            
            if not originate_success:
                # Verificar se foi hangup do cliente
                if self._check_a_leg_hangup():
                    logger.warning(f"{elapsed()} STEP 4: 🚨 Cliente desligou enquanto ramal tocava")
                    await self._stop_hangup_monitor()
                    return ConferenceTransferResult(
                        success=False,
                        decision=TransferDecision.HANGUP,
                        conference_name=self.conference_name,
                        error="Cliente desligou durante transferência"
                    )
                
                logger.warning(f"{elapsed()} STEP 4: ❌ Ramal não atendeu")
                # Tirar cliente da espera e dar feedback
                await self._cleanup_and_return(reason="Ramal não atendeu")
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.REJECTED,
                    conference_name=self.conference_name,
                    error="Ramal não atendeu. Você pode deixar um recado.",
                    duration_ms=int((time.time() - start_time) * 1000)
                )
            logger.info(f"{elapsed()} STEP 4: ✅ Ramal atendeu: {self.b_leg_uuid}")
            
            # Aguardar B-leg estabilizar - verificando hangup
            logger.info(f"{elapsed()} STEP 4: Aguardando estabilização (1.5s)...")
            hangup_during_wait = await self._wait_for_hangup_or_timeout(1.5)
            if hangup_during_wait == 'a_leg':
                logger.warning(f"{elapsed()} STEP 4: 🚨 Cliente desligou durante estabilização")
                await self._cleanup_b_leg()
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    b_leg_uuid=self.b_leg_uuid,
                    conference_name=self.conference_name,
                    error="Cliente desligou durante transferência"
                )
            logger.info(f"{elapsed()} STEP 4: ✅ Ramal estável")
            
            # ============================================================
            # STEP 5: Anunciar para o atendente
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 5: Anunciando cliente para o atendente...")
            
            # Checar hangup antes de anunciar
            if self._check_a_leg_hangup():
                logger.warning(f"{elapsed()} STEP 5: 🚨 Cliente desligou antes do anúncio")
                await self._cleanup_b_leg()
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    b_leg_uuid=self.b_leg_uuid,
                    conference_name=self.conference_name,
                    error="Cliente desligou durante transferência"
                )
            
            decision = await self._announce_to_b_leg(announcement, context)
            
            # Verificar se hangup ocorreu durante anúncio
            if self._check_a_leg_hangup():
                logger.warning(f"{elapsed()} STEP 5: 🚨 Cliente desligou durante anúncio")
                await self._cleanup_b_leg()
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    b_leg_uuid=self.b_leg_uuid,
                    conference_name=self.conference_name,
                    error="Cliente desligou durante transferência"
                )
            
            logger.info(f"{elapsed()} STEP 5: ✅ Decisão do atendente: {decision.value}")
            
            # ============================================================
            # STEP 6: Processar decisão do atendente
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 6: Processando decisão...")
            
            # Última verificação de hangup antes de finalizar
            if self._check_a_leg_hangup():
                logger.warning(f"{elapsed()} STEP 6: 🚨 Cliente desligou antes de processar decisão")
                await self._cleanup_b_leg()
                await self._stop_hangup_monitor()
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.HANGUP,
                    b_leg_uuid=self.b_leg_uuid,
                    conference_name=self.conference_name,
                    error="Cliente desligou durante transferência"
                )
            
            result = await self._process_decision(decision, context)
            result.duration_ms = int((time.time() - start_time) * 1000)
            
            # Parar monitor após sucesso
            await self._stop_hangup_monitor()
            
            logger.info(f"{elapsed()} STEP 6: ✅ Resultado: success={result.success}, decision={result.decision.value}")
            
            return result
            
        except asyncio.CancelledError:
            logger.info("Transfer cancelled")
            await self._stop_hangup_monitor()
            await self._cleanup_on_error()
            raise
            
        except Exception as e:
            logger.error(f"Transfer failed: {e}", exc_info=True)
            await self._stop_hangup_monitor()
            await self._cleanup_on_error()
            
            return ConferenceTransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000)
            )
    
    def _generate_conference_name(self) -> str:
        """
        Gera nome único para conferência temporária.
        
        Format: transfer_SHORTID_TIMESTAMP_RANDOM
        
        NOTA: Inclui componente randômico para evitar colisão se duas
        transferências acontecerem no mesmo segundo para o mesmo A-leg.
        """
        short_id = self.a_leg_uuid[:8]
        timestamp = int(time.time())
        # Adicionar 4 chars randômicos para garantir unicidade
        random_suffix = str(uuid4())[:4]
        return f"transfer_{short_id}_{timestamp}_{random_suffix}"
    
    # =========================================================================
    # MONITORAMENTO DE HANGUP EM TEMPO REAL
    # =========================================================================
    
    async def _start_hangup_monitor(self) -> None:
        """
        Inicia monitoramento de eventos CHANNEL_HANGUP para A-leg e B-leg.
        
        Usa ESL event subscription para receber notificações em tempo real.
        Quando detecta hangup, seta o asyncio.Event correspondente.
        """
        self._transfer_active = True
        self._a_leg_hangup_event.clear()
        self._b_leg_hangup_event.clear()
        
        # Registrar handler para eventos de hangup
        async def on_hangup(event):
            if not self._transfer_active:
                return
            
            uuid = event.uuid if hasattr(event, 'uuid') else event.headers.get('Unique-ID', '')
            hangup_cause = event.headers.get('Hangup-Cause', 'UNKNOWN')
            
            if uuid == self.a_leg_uuid:
                logger.warning(f"🚨 [HANGUP_MONITOR] A-leg hangup detected: {hangup_cause}")
                self._a_leg_hangup_event.set()
            elif uuid == self.b_leg_uuid:
                logger.info(f"📞 [HANGUP_MONITOR] B-leg hangup detected: {hangup_cause}")
                self._b_leg_hangup_event.set()
        
        # Registrar handler no ESL client
        if hasattr(self.esl, 'register_event_handler'):
            self._hangup_handler_id = await self.esl.register_event_handler(
                event_name="CHANNEL_HANGUP",
                callback=on_hangup,
                uuid_filter=None  # Monitorar todos, filtrar no callback
            )
            logger.debug(f"[HANGUP_MONITOR] Handler registrado: {self._hangup_handler_id}")
        else:
            logger.debug("[HANGUP_MONITOR] ESL não suporta event handlers, usando polling")
    
    async def _stop_hangup_monitor(self) -> None:
        """Para o monitoramento de hangup."""
        self._transfer_active = False
        
        # Remover handler se registrado
        if self._hangup_handler_id and hasattr(self.esl, 'unregister_event_handler'):
            try:
                await self.esl.unregister_event_handler(self._hangup_handler_id)
                logger.debug(f"[HANGUP_MONITOR] Handler removido: {self._hangup_handler_id}")
            except Exception as e:
                logger.debug(f"[HANGUP_MONITOR] Erro removendo handler: {e}")
            self._hangup_handler_id = None
    
    def _check_a_leg_hangup(self) -> bool:
        """
        Verifica se A-leg (cliente) desligou.
        
        Returns:
            True se cliente desligou, False caso contrário
        """
        return self._a_leg_hangup_event.is_set()
    
    def _check_b_leg_hangup(self) -> bool:
        """
        Verifica se B-leg (atendente) desligou.
        
        Returns:
            True se atendente desligou, False caso contrário
        """
        return self._b_leg_hangup_event.is_set()
    
    async def _wait_for_hangup_or_timeout(self, timeout: float) -> Optional[str]:
        """
        Aguarda hangup de qualquer lado ou timeout.
        
        Args:
            timeout: Tempo máximo de espera em segundos
            
        Returns:
            'a_leg' se A-leg desligou
            'b_leg' se B-leg desligou
            None se timeout
        """
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(self._a_leg_hangup_event.wait()),
                asyncio.create_task(self._b_leg_hangup_event.wait()),
            ],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Cancelar tasks pendentes
        for task in pending:
            task.cancel()
        
        if self._a_leg_hangup_event.is_set():
            return 'a_leg'
        if self._b_leg_hangup_event.is_set():
            return 'b_leg'
        return None
    
    async def _verify_a_leg_alive(self, step_name: str) -> bool:
        """
        Verifica se A-leg ainda está ativo.
        Combinação de event check + uuid_exists.
        
        Args:
            step_name: Nome do step para logging
            
        Returns:
            True se A-leg está ativo, False se desligou
        """
        # Verificação rápida via event
        if self._check_a_leg_hangup():
            logger.warning(f"🚨 [{step_name}] A-leg hangup detectado via event")
            return False
        
        # Verificação via ESL (backup)
        try:
            exists = await asyncio.wait_for(
                self.esl.uuid_exists(self.a_leg_uuid),
                timeout=2.0
            )
            if not exists:
                logger.warning(f"🚨 [{step_name}] A-leg não existe mais (uuid_exists=False)")
                self._a_leg_hangup_event.set()  # Sincronizar event
                return False
            return True
        except asyncio.TimeoutError:
            # Timeout não significa que desligou, assumir ativo
            logger.debug(f"[{step_name}] uuid_exists timeout, assumindo A-leg ativo")
            return True
        except Exception as e:
            logger.debug(f"[{step_name}] uuid_exists error: {e}, assumindo A-leg ativo")
            return True
    
    async def _stop_voiceai_stream(self) -> None:
        """Para o stream de áudio do Voice AI no A-leg."""
        logger.debug(f"_stop_voiceai_stream: Sending uuid_audio_stream stop for {self.a_leg_uuid}")
        try:
            result = await asyncio.wait_for(
                self.esl.execute_api(f"uuid_audio_stream {self.a_leg_uuid} stop"),
                timeout=3.0
            )
            logger.debug(f"_stop_voiceai_stream: Result: {result}")
        except asyncio.TimeoutError:
            logger.warning("_stop_voiceai_stream: TIMEOUT (continuing anyway)")
        except Exception as e:
            logger.debug(f"_stop_voiceai_stream: Error: {e} (continuing anyway)")
    
    async def _move_a_leg_to_conference(self) -> None:
        """
        Move A-leg (cliente) para conferência com flags especiais.
        
        Flags:
        - mute: Cliente não pode falar (ainda)
        
        A conferência será criada automaticamente.
        """
        logger.info(f"_move_a_leg_to_conference: START - A-leg={self.a_leg_uuid}")
        
        # Comando: uuid_transfer UUID 'conference:NAME@PROFILE+flags{...}' inline
        # Nota: FreeSWITCH 1.10+ aceita essa sintaxe
        profile = self.config.conference_profile
        
        # Usar uuid_transfer com inline dialplan
        # NOTA: As chaves simples {mute} são interpretadas pelo FreeSWITCH
        # Python f-string requer {{ }} para escapar, resultando em { } no output
        transfer_cmd = (
            f"uuid_transfer {self.a_leg_uuid} "
            f"'conference:{self.conference_name}@{profile}+flags{{mute}}' inline"
        )
        
        logger.info(f"_move_a_leg_to_conference: Sending command: {transfer_cmd}")
        
        try:
            logger.debug("_move_a_leg_to_conference: Awaiting ESL execute_api...")
            result = await asyncio.wait_for(
                self.esl.execute_api(transfer_cmd),
                timeout=5.0
            )
            logger.info(f"_move_a_leg_to_conference: ESL returned: {result}")
            
            if "-ERR" in str(result):
                logger.error(f"_move_a_leg_to_conference: ❌ Command failed: {result}")
                raise Exception(f"uuid_transfer failed: {result}")
            
            logger.info("_move_a_leg_to_conference: ✅ Transfer command successful")
            
            # Aguardar A-leg entrar na conferência
            logger.debug("_move_a_leg_to_conference: Waiting 0.5s for A-leg to join conference...")
            await asyncio.sleep(0.5)
            logger.info("_move_a_leg_to_conference: END - A-leg should be in conference now")
            
        except asyncio.TimeoutError:
            logger.error("_move_a_leg_to_conference: ❌ TIMEOUT waiting for ESL response")
            raise Exception("uuid_transfer timeout")
        except Exception as e:
            logger.error(f"_move_a_leg_to_conference: ❌ Failed: {e}")
            raise
    
    async def _originate_b_leg(self, destination: str, direct_contact: Optional[str] = None) -> bool:
        """
        Origina B-leg (atendente) direto para conferência.
        
        B-leg entra como moderador - pode falar e ouvir normalmente.
        
        Args:
            destination: Extensão destino (ex: "1001")
            direct_contact: Contact SIP direto do ramal (ex: "sip:1001@177.72.14.10:46522")
                           Se fornecido, usa direto evitando lookup que pode causar loop.
            
        Returns:
            bool: True se originate teve sucesso
        """
        logger.info(f"_originate_b_leg: START - destination={destination}@{self.domain}")
        if direct_contact:
            logger.info(f"_originate_b_leg: Direct contact available: {direct_contact}")
        
        # Gerar UUID para B-leg (local até confirmar que existe)
        candidate_uuid = str(uuid4())
        logger.info(f"_originate_b_leg: Generated candidate UUID: {candidate_uuid}")
        
        profile = self.config.conference_profile
        timeout = self.config.originate_timeout
        
        # Construir dial string
        # PRIORIDADE: Usar contact direto se disponível (evita loop de lookup)
        if direct_contact:
            # Extrair user@host:port do contact SIP
            # Formatos possíveis:
            #   "sip:1001@177.72.14.10:46522"
            #   "<sip:1001@177.72.14.10:46522>"
            #   "<sip:1001@177.72.14.10:46522;transport=UDP>"
            #   "sip:1001@177.72.14.10:46522;rinstance=abc"
            contact_clean = direct_contact
            
            # Remover < > se existir
            if '<' in contact_clean:
                match = re.search(r'<([^>]+)>', contact_clean)
                if match:
                    contact_clean = match.group(1)
            
            # Remover prefixo sip: ou sips:
            contact_clean = contact_clean.replace('sips:', '').replace('sip:', '')
            
            # Remover parâmetros após ; (ex: ;transport=UDP;rinstance=abc)
            if ';' in contact_clean:
                contact_clean = contact_clean.split(';')[0]
            
            contact_clean = contact_clean.strip()
            
            logger.debug(f"_originate_b_leg: Contact cleaned: '{direct_contact}' -> '{contact_clean}'")
            
            dial_string = (
                f"{{origination_uuid={candidate_uuid},"
                f"origination_caller_id_number={self.caller_id},"
                f"origination_caller_id_name=Secretaria_Virtual,"
                f"originate_timeout={timeout},"
                f"ignore_early_media=true}}"
                f"sofia/internal/{contact_clean}"
            )
            logger.info(f"_originate_b_leg: ✅ Using DIRECT contact (no lookup)")
        else:
            # Fallback: user lookup (pode causar loop em alguns casos)
            dial_string = (
                f"{{origination_uuid={candidate_uuid},"
                f"origination_caller_id_number={self.caller_id},"
                f"origination_caller_id_name=Secretaria_Virtual,"
                f"originate_timeout={timeout},"
                f"ignore_early_media=true,"
                f"sip_invite_domain={self.domain}}}"
                f"sofia/internal/{destination}@{self.domain}"
            )
            logger.warning(f"_originate_b_leg: ⚠️ Using user lookup (no direct contact, may cause loop)")
        
        # App: conferência como moderador
        # moderator flag libera os membros que estão em wait-mod
        app = f"&conference({self.conference_name}@{profile}+flags{{moderator}})"
        
        logger.info(f"_originate_b_leg: Dial string: {dial_string}")
        logger.info(f"_originate_b_leg: App: {app}")
        
        try:
            # Executar originate via bgapi (assíncrono)
            # bgapi retorna Job-UUID, não o resultado imediato
            logger.info("_originate_b_leg: Sending bgapi originate...")
            try:
                result = await asyncio.wait_for(
                    self.esl.execute_api(f"bgapi originate {dial_string} {app}"),
                    timeout=5.0
                )
                logger.info(f"_originate_b_leg: bgapi result: {result}")
            except asyncio.TimeoutError:
                logger.error("_originate_b_leg: ❌ bgapi TIMEOUT after 5s")
                return False
            
            # Polling para verificar se B-leg foi criado
            # Máximo de tentativas baseado no timeout de originate
            max_attempts = min(timeout, 30)  # Máximo 30 tentativas (30 segundos)
            logger.info(f"_originate_b_leg: Starting polling (max {max_attempts} attempts)...")
            
            for attempt in range(int(max_attempts)):
                await asyncio.sleep(1.0)
                
                # Verificar se B-leg existe (timeout curto)
                try:
                    b_exists = await asyncio.wait_for(
                        self.esl.uuid_exists(candidate_uuid),
                        timeout=3.0
                    )
                    logger.debug(f"_originate_b_leg: Attempt {attempt + 1}: B-leg exists = {b_exists}")
                except asyncio.TimeoutError:
                    logger.warning(f"_originate_b_leg: Attempt {attempt + 1}: uuid_exists TIMEOUT")
                    continue  # Tentar novamente
                
                if b_exists:
                    # SUCESSO: Agora podemos atribuir o UUID ao estado da classe
                    self.b_leg_uuid = candidate_uuid
                    logger.info(f"_originate_b_leg: ✅ B-leg {self.b_leg_uuid} answered after {attempt + 1}s")
                    return True
                
                # Verificar se A-leg ainda existe (timeout curto)
                try:
                    a_exists = await asyncio.wait_for(
                        self.esl.uuid_exists(self.a_leg_uuid),
                        timeout=3.0
                    )
                except asyncio.TimeoutError:
                    logger.debug(f"_originate_b_leg: Attempt {attempt + 1}: A-leg check timeout, assuming exists")
                    a_exists = True  # Assumir que existe
                
                if not a_exists:
                    logger.warning(f"_originate_b_leg: ❌ A-leg gone during originate wait (attempt {attempt + 1})")
                    # NÃO atribuir b_leg_uuid - nunca existiu
                    return False
                
                # Log a cada 5 segundos
                if (attempt + 1) % 5 == 0:
                    logger.info(f"_originate_b_leg: Still waiting for B-leg... ({attempt + 1}s)")
            
            logger.warning(f"_originate_b_leg: ❌ B-leg {candidate_uuid} not answered after {max_attempts}s")
            # NÃO atribuir b_leg_uuid - originate falhou
            return False
            
        except Exception as e:
            logger.error(f"Failed to originate B-leg: {e}")
            # NÃO atribuir b_leg_uuid - exceção ocorreu
            return False
    
    async def _announce_to_b_leg(
        self,
        announcement: str,
        context: str,
    ) -> TransferDecision:
        """
        Faz anúncio para B-leg via OpenAI Realtime.
        
        O sistema irá:
        1. Conectar ao B-leg via uuid_audio_stream
        2. Falar o anúncio usando voz OpenAI
        3. Aguardar resposta verbal do B-leg
        4. Detectar aceitação/recusa via patterns ou function calls
        
        Args:
            announcement: Texto do anúncio
            context: Contexto da transferência
            
        Returns:
            TransferDecision
        """
        logger.info("📋 Step 4: Announcing to B-leg via OpenAI...")
        
        # Verificar se ambos os legs ainda existem antes do anúncio (timeout curto)
        try:
            a_exists = await asyncio.wait_for(
                self.esl.uuid_exists(self.a_leg_uuid),
                timeout=3.0
            )
        except asyncio.TimeoutError:
            logger.warning("A-leg check timeout, assuming exists")
            a_exists = True
        
        if not a_exists:
            logger.warning("A-leg (client) gone before announcement")
            return TransferDecision.HANGUP
        
        try:
            b_exists = await asyncio.wait_for(
                self.esl.uuid_exists(self.b_leg_uuid),
                timeout=3.0
            )
        except asyncio.TimeoutError:
            logger.warning("B-leg check timeout, assuming exists")
            b_exists = True
        
        if not b_exists:
            logger.warning("B-leg (attendant) gone before announcement")
            return TransferDecision.HANGUP
        
        # Importar aqui para evitar circular import
        from .realtime_announcement_conference import ConferenceAnnouncementSession
        
        # Prompt para o agente
        system_prompt = self._build_announcement_prompt(context)
        
        # Mensagem inicial - apenas o anúncio, a IA vai perguntar naturalmente
        initial_message = (
            f"Olá, tenho um cliente na linha. {announcement}. "
            f"Você pode atender agora?"
        )
        
        try:
            # Criar sessão de anúncio
            self._announcement_session = ConferenceAnnouncementSession(
                esl_client=self.esl,
                b_leg_uuid=self.b_leg_uuid,
                system_prompt=system_prompt,
                initial_message=initial_message,
                model=self.config.openai_model,
                voice=self.config.openai_voice,
            )
            
            # Executar anúncio
            result = await self._announcement_session.run(
                timeout=self.config.announcement_timeout
            )
            
            # Mapear resultado para TransferDecision
            if result.accepted:
                return TransferDecision.ACCEPTED
            elif result.rejected:
                return TransferDecision.REJECTED
            else:
                # Timeout - verificar se B-leg ainda existe antes de assumir aceitação
                try:
                    b_exists = await asyncio.wait_for(
                        self.esl.uuid_exists(self.b_leg_uuid),
                        timeout=3.0
                    )
                except asyncio.TimeoutError:
                    b_exists = True  # Assumir que existe
                
                if not b_exists:
                    logger.info("Timeout + B-leg gone = HANGUP")
                    return TransferDecision.HANGUP
                
                if self.config.accept_on_timeout:
                    logger.info("Timeout - B-leg still connected, assuming acceptance")
                    return TransferDecision.ACCEPTED
                else:
                    return TransferDecision.TIMEOUT
            
        except asyncio.TimeoutError:
            logger.warning(f"Announcement timeout after {self.config.announcement_timeout}s")
            return TransferDecision.TIMEOUT
            
        except Exception as e:
            logger.error(f"Announcement error: {e}")
            return TransferDecision.ERROR
    
    def _build_announcement_prompt(self, context: str) -> str:
        """Constrói prompt de sistema para o anúncio."""
        return f"""Você é uma secretária virtual de uma empresa. Você está anunciando uma ligação para um atendente humano.

CONTEXTO DA LIGAÇÃO: {context}

SEU OBJETIVO:
1. Anunciar que há um cliente na linha aguardando
2. Perguntar se o atendente pode atender AGORA
3. Aguardar uma resposta CLARA do atendente

FLUXO DA CONVERSA:
1. PRIMEIRO: Faça o anúncio (máximo 2 frases)
2. DEPOIS: Aguarde a resposta do atendente
3. SÓ ENTÃO: Chame a função apropriada

QUANDO CHAMAR AS FUNÇÕES:
- accept_transfer(): APENAS se o atendente disser CLARAMENTE: "sim", "pode passar", "pode conectar", "aceito", "manda", "transfira"
- reject_transfer(): Se o atendente disser: "não", "não posso", "ocupado", "estou em reunião", "depois", "liga depois"

IMPORTANTE:
- "Alô", "Oi", "Olá", "Pois não" NÃO SÃO aceitação - são apenas saudações. Continue a conversa!
- Se o atendente apenas atender com saudação, REPITA a pergunta se ele pode atender
- NÃO assuma aceitação sem confirmação explícita
- Seja BREVE - o cliente está aguardando na linha

EXEMPLO DE CONVERSA CORRETA:
Você: "Olá, tenho um cliente na linha sobre vendas. Você pode atender agora?"
Atendente: "Alô"
Você: "Há um cliente aguardando para falar sobre vendas. Pode atendê-lo?"
Atendente: "Sim, pode passar"
[Agora sim chamar accept_transfer()]
"""
    
    async def _process_decision(
        self,
        decision: TransferDecision,
        context: str,
    ) -> ConferenceTransferResult:
        """
        Processa decisão do B-leg.
        
        Args:
            decision: Decisão do atendente
            context: Contexto original
            
        Returns:
            ConferenceTransferResult
        """
        logger.info(f"📋 Step 5: Processing decision: {decision.value}")
        
        if decision == TransferDecision.ACCEPTED:
            return await self._handle_accepted()
            
        elif decision == TransferDecision.REJECTED:
            return await self._handle_rejected(context, "Atendente recusou", TransferDecision.REJECTED)
            
        elif decision == TransferDecision.TIMEOUT:
            # TIMEOUT pode significar que:
            # 1. Atendente não respondeu (mas está conectado) - se accept_on_timeout=True, já foi tratado
            # 2. Atendente desligou durante timeout - tratado como HANGUP
            # Se chegou aqui, é um timeout real com accept_on_timeout=False
            return await self._handle_rejected(context, "Timeout sem resposta", TransferDecision.TIMEOUT)
            
        elif decision == TransferDecision.HANGUP:
            return await self._handle_rejected(context, "Atendente desligou", TransferDecision.HANGUP)
            
        else:  # ERROR
            return await self._handle_rejected(context, "Erro no anúncio", TransferDecision.ERROR)
    
    async def _handle_accepted(self) -> ConferenceTransferResult:
        """
        B-leg aceitou - unmute A-leg para iniciar conversa.
        
        A conferência permanece ativa com ambos os participantes.
        
        IMPORTANTE: 
        1. Parar stream de áudio do B-leg ANTES de unmute
        2. Configurar conferência para terminar quando um sair
        """
        logger.info("✅ Transfer ACCEPTED")
        
        try:
            # CRÍTICO: Parar stream de áudio do OpenAI no B-leg ANTES de unmute
            # Isso evita que o áudio do OpenAI continue tocando após a conexão
            if self.b_leg_uuid:
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop"),
                        timeout=3.0
                    )
                    logger.debug("B-leg audio stream stopped before unmute")
                    # Pequeno delay para garantir que o stream parou
                    await asyncio.sleep(0.2)
                except (asyncio.TimeoutError, Exception) as e:
                    logger.debug(f"Could not stop B-leg stream: {e}")
            
            # Desmute A-leg na conferência (timeout curto)
            unmute_cmd = f"conference {self.conference_name} unmute {self.a_leg_uuid}"
            logger.debug(f"Unmute command: {unmute_cmd}")
            
            try:
                result = await asyncio.wait_for(
                    self.esl.execute_api(unmute_cmd),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                logger.warning("Unmute command timeout")
                result = ""
            
            if "-ERR" in str(result):
                logger.warning(f"Unmute may have failed: {result}")
            else:
                logger.info(f"A-leg unmuted: {result}")
            
            # CRÍTICO: Configurar para terminar conferência quando um participante sair
            # Opção 1: Setar variável no A-leg para kickar quando B-leg sair
            # Opção 2: Setar variável no B-leg para kickar quando A-leg sair
            # 
            # Usamos conference_set_auto_outcall para monitorar e encerrar
            # Mas a forma mais simples é usar uuid_bridge após a conferência
            #
            # SOLUÇÃO: Usar uuid_bridge direto entre A-leg e B-leg
            # Isso é mais simples e funciona melhor que conferência para 2 pessoas
            logger.info("🔄 Converting conference to direct bridge...")
            
            try:
                # Primeiro, tirar ambos da conferência
                await asyncio.wait_for(
                    self.esl.execute_api(f"conference {self.conference_name} kick {self.a_leg_uuid}"),
                    timeout=2.0
                )
            except (asyncio.TimeoutError, Exception):
                pass
            
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"conference {self.conference_name} kick {self.b_leg_uuid}"),
                    timeout=2.0
                )
            except (asyncio.TimeoutError, Exception):
                pass
            
            # Pequeno delay para os kicks processarem
            await asyncio.sleep(0.3)
            
            # Agora fazer bridge direto entre A-leg e B-leg
            # hangup_after_bridge garante que ambos desligam juntos
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_setvar {self.a_leg_uuid} hangup_after_bridge true"),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                pass
            
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_setvar {self.b_leg_uuid} hangup_after_bridge true"),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                pass
            
            # Bridge direto
            bridge_cmd = f"uuid_bridge {self.a_leg_uuid} {self.b_leg_uuid}"
            logger.info(f"Bridge command: {bridge_cmd}")
            
            try:
                bridge_result = await asyncio.wait_for(
                    self.esl.execute_api(bridge_cmd),
                    timeout=3.0
                )
                logger.info(f"Bridge result: {bridge_result}")
            except asyncio.TimeoutError:
                logger.warning("Bridge command timeout")
            
            logger.info("🎉 Transfer completed - both parties bridged directly")
            
            return ConferenceTransferResult(
                success=True,
                decision=TransferDecision.ACCEPTED,
                b_leg_uuid=self.b_leg_uuid,
                conference_name=self.conference_name
            )
            
        except Exception as e:
            logger.error(f"Failed to complete transfer: {e}")
            return ConferenceTransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error=str(e)
            )
    
    async def _handle_rejected(
        self,
        context: str,
        reason: str,
        original_decision: Optional[TransferDecision] = None,
    ) -> ConferenceTransferResult:
        """
        B-leg recusou/timeout/hangup - cleanup e retornar cliente ao Voice AI.
        
        Fluxo:
        1. Parar stream de áudio do B-leg
        2. Desligar B-leg
        3. Criar ticket no OmniPlay (opcional)
        4. Retornar A-leg ao Voice AI com opção de deixar recado
        
        O Voice AI (callback on_resume) deve informar ao cliente:
        - "O atendente não pode atender no momento"
        - "Você gostaria de deixar um recado?"
        
        Args:
            context: Contexto da transferência
            reason: Razão da rejeição
            original_decision: Decisão original (para preservar no resultado)
            
        Returns:
            ConferenceTransferResult com mensagem para feedback
        """
        logger.info(f"❌ Atendente não aceitou: {reason}")
        
        # Determinar decisão para o resultado
        result_decision = original_decision or TransferDecision.REJECTED
        
        ticket_id = None
        
        try:
            # 1. Parar stream de áudio do B-leg (timeout curto)
            if self.b_leg_uuid:
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop"),
                        timeout=2.0
                    )
                except (asyncio.TimeoutError, Exception):
                    pass
            
            # 2. Kick B-leg (timeout curto)
            if self.b_leg_uuid:
                try:
                    b_exists = await asyncio.wait_for(
                        self.esl.uuid_exists(self.b_leg_uuid),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    b_exists = False
                
                if b_exists:
                    try:
                        await asyncio.wait_for(
                            self.esl.execute_api(f"uuid_kill {self.b_leg_uuid}"),
                            timeout=2.0
                        )
                        logger.debug("B-leg killed")
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.debug(f"Could not kill B-leg: {e}")
            
            # 3. Criar ticket (opcional)
            if self.omniplay_api:
                ticket_id = await self._create_ticket(context, reason)
            
            # 4. Retornar A ao Voice AI
            await self._return_a_leg_to_voiceai()
            
            return ConferenceTransferResult(
                success=False,
                decision=result_decision,
                ticket_id=ticket_id,
                conference_name=self.conference_name,
            )
            
        except Exception as e:
            logger.error(f"Error handling rejection: {e}")
            return ConferenceTransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error=str(e)
            )
    
    async def _create_ticket(self, context: str, reason: str) -> Optional[str]:
        """Cria ticket no OmniPlay."""
        logger.info("🎫 Creating ticket in OmniPlay...")
        
        try:
            ticket_data = {
                "caller_id": self.caller_id,
                "context": context,
                "reason": reason,
                "uuid": self.a_leg_uuid,
                "timestamp": time.time()
            }
            
            ticket = await self.omniplay_api.create_ticket(ticket_data)
            ticket_id = ticket.get("id")
            
            logger.info(f"✅ Ticket created: {ticket_id}")
            return ticket_id
            
        except Exception as e:
            logger.error(f"Failed to create ticket: {e}")
            return None
    
    async def _return_a_leg_to_voiceai(self) -> None:
        """
        Retorna A-leg ao Voice AI.
        
        Remove da conferência e reinicia stream de áudio.
        """
        logger.info("🔙 Returning A-leg to Voice AI...")
        
        try:
            # Verificar se A-leg existe (timeout curto)
            try:
                a_exists = await asyncio.wait_for(
                    self.esl.uuid_exists(self.a_leg_uuid),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                a_exists = True  # Tentar mesmo assim
            
            if not a_exists:
                logger.info("A-leg no longer exists")
                return
            
            # Kick A-leg da conferência (timeout curto)
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"conference {self.conference_name} kick {self.a_leg_uuid}"),
                    timeout=2.0
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug(f"Could not kick A-leg from conference: {e}")
            
            await asyncio.sleep(0.3)
            
            # Retomar Voice AI via callback
            if self.on_resume:
                logger.info("Resuming Voice AI session")
                try:
                    result = self.on_resume()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Failed to resume Voice AI: {e}")
                    # Fallback: park (timeout curto)
                    try:
                        await asyncio.wait_for(
                            self.esl.execute_api(f"uuid_park {self.a_leg_uuid}"),
                            timeout=2.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass
            else:
                # Sem callback - park (timeout curto)
                logger.warning("No resume callback - parking A-leg")
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_park {self.a_leg_uuid}"),
                        timeout=2.0
                    )
                except (asyncio.TimeoutError, Exception):
                    pass
            
            logger.info("A-leg returned to Voice AI")
            
        except Exception as e:
            logger.error(f"Failed to return A-leg: {e}")
    
    async def _cleanup_and_return(self, reason: str = "") -> None:
        """Cleanup parcial e retorna A-leg."""
        if self.b_leg_uuid:
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_kill {self.b_leg_uuid}"),
                    timeout=2.0
                )
            except (asyncio.TimeoutError, Exception):
                pass
        
        await self._return_a_leg_to_voiceai()
    
    async def _cleanup_b_leg(self) -> None:
        """
        Cleanup apenas do B-leg (atendente).
        
        Usado quando cliente desliga e precisamos limpar apenas o B-leg,
        sem tentar retornar A-leg ao Voice AI.
        """
        logger.info("🧹 Cleaning up B-leg only...")
        
        try:
            # 1. Parar stream de áudio do B-leg
            if self.b_leg_uuid:
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop"),
                        timeout=2.0
                    )
                except (asyncio.TimeoutError, Exception):
                    pass
                
                # 2. Matar B-leg
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_kill {self.b_leg_uuid}"),
                        timeout=2.0
                    )
                    logger.info(f"✅ B-leg {self.b_leg_uuid} killed")
                except (asyncio.TimeoutError, Exception):
                    pass
            
            # 3. Destruir conferência (se existir)
            if self.conference_name:
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"conference {self.conference_name} kick all"),
                        timeout=2.0
                    )
                except (asyncio.TimeoutError, Exception):
                    pass
                    
        except Exception as e:
            logger.warning(f"B-leg cleanup error (non-fatal): {e}")
    
    async def _cleanup_on_error(self) -> None:
        """
        Cleanup em caso de erro.
        
        Garante que:
        - Stream de áudio seja parado
        - B-leg seja desligado
        - Conferência seja destruída (se existir)
        - A-leg retorne ao Voice AI
        
        ORDEM IMPORTA: Parar streams -> Matar legs -> Destruir conferência -> Retornar A-leg
        """
        logger.info("🧹 Cleaning up after error...")
        
        try:
            # 1. Parar streams de áudio (evita áudio residual) - timeout curto
            if self.b_leg_uuid:
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop"),
                        timeout=2.0
                    )
                except (asyncio.TimeoutError, Exception):
                    pass
            
            if self.a_leg_uuid:
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_audio_stream {self.a_leg_uuid} stop"),
                        timeout=2.0
                    )
                except (asyncio.TimeoutError, Exception):
                    pass
            
            # 2. Hangup B-leg primeiro (se existir) - timeout curto
            if self.b_leg_uuid:
                try:
                    b_exists = await asyncio.wait_for(
                        self.esl.uuid_exists(self.b_leg_uuid),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    b_exists = False
                
                if b_exists:
                    try:
                        await asyncio.wait_for(
                            self.esl.execute_api(f"uuid_kill {self.b_leg_uuid}"),
                            timeout=2.0
                        )
                        logger.debug("B-leg killed in cleanup")
                    except (asyncio.TimeoutError, Exception):
                        pass
            
            # 3. Destruir conferência (se foi criada) - timeout curto
            if self.conference_name:
                try:
                    # Verificar se conferência existe antes de kick
                    result = await asyncio.wait_for(
                        self.esl.execute_api(f"conference {self.conference_name} list"),
                        timeout=2.0
                    )
                    if result and "-ERR" not in str(result):
                        await asyncio.wait_for(
                            self.esl.execute_api(f"conference {self.conference_name} kick all"),
                            timeout=2.0
                        )
                        logger.debug("Conference destroyed in cleanup")
                except (asyncio.TimeoutError, Exception):
                    pass
            
            # 4. Retornar A ao Voice AI
            await self._return_a_leg_to_voiceai()
            
            logger.info("Cleanup completed")
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
