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

# Core - Sistema de controle interno
# Ref: voice-ai-ivr/docs/PLANO-ARQUITETURA-INTERNA.md
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
    announcement_timeout: float = 30.0  # 30s para permitir conversa com perguntas
    dtmf_timeout: float = 10.0
    
    # Conferência
    conference_profile: str = "default"
    # NOTA: MOH removido - cliente fica em silêncio durante transferência
    
    # OpenAI
    openai_model: str = "gpt-realtime"
    openai_voice: str = "marin"
    
    # Comportamento
    # IMPORTANTE: accept_on_timeout=False para evitar conectar quando atendente recusa
    # mas a IA não chama reject_transfer(). Melhor rejeitar por timeout do que conectar errado.
    accept_on_timeout: bool = False
    
    # Prompts customizados (do banco de dados via FusionPBX)
    # Se None, usa prompts padrão hardcoded como fallback
    announcement_prompt: Optional[str] = None  # Prompt para anúncio ao atendente
    courtesy_message: Optional[str] = None  # Mensagem de cortesia ao recusar


class ConferenceTransferManager:
    """
    Gerencia transferências anunciadas usando mod_conference.
    
    Fluxo:
    1. Cria conferência temporária única
    2. Move A-leg (cliente) para conferência com flags {mute|deaf}
    3. Origina B-leg (atendente) para conferência como moderador
    4. OpenAI anuncia para B-leg via uuid_audio_stream
    5. B-leg aceita/recusa via função call ou DTMF
    6. Se aceito: unmute+undeaf A-leg, ambos conversam
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
        secretary_uuid: Optional[str] = None,  # Mantido para compatibilidade
        event_bus: Optional[EventBus] = None,  # EventBus para emitir eventos
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
            secretary_uuid: UUID da secretária (para fallback de reconexão)
            event_bus: EventBus para emitir eventos internos (opcional)
        """
        self.esl = esl_client
        self.a_leg_uuid = a_leg_uuid
        self.domain = domain
        self.caller_id = caller_id
        self.config = config or ConferenceTransferConfig()
        self.events = event_bus  # Pode ser None (retrocompatível)
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.on_resume = on_resume
        self.omniplay_api = omniplay_api
        self.secretary_uuid = secretary_uuid  # Mantido para fallback
        
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
        
        # UUID pendente do B-leg durante originate (antes de ser confirmado)
        # Permite detectar rejeição de chamada mesmo antes de b_leg_uuid ser atribuído
        self._pending_b_leg_uuid: Optional[str] = None
        self._b_leg_hangup_cause: Optional[str] = None
    
    async def _ensure_esl_connected(self, context: str = "") -> bool:
        """
        Verifica e garante que ESL está conectado.
        
        Se desconectado, tenta reconectar automaticamente.
        
        Args:
            context: Contexto para log (ex: "STEP 3")
            
        Returns:
            True se conectado, False se falhou
        """
        try:
            is_connected = getattr(self.esl, '_connected', False) or getattr(self.esl, 'connected', False)
            
            if not is_connected:
                logger.warning(f"🔌 [{context}] ESL disconnected, attempting reconnect...")
                try:
                    await asyncio.wait_for(self.esl.connect(), timeout=5.0)
                    logger.info(f"🔌 [{context}] ESL reconnected successfully")
                    return True
                except Exception as e:
                    logger.error(f"🔌 [{context}] ESL reconnect failed: {e}")
                    return False
            return True
        except Exception as e:
            logger.error(f"🔌 [{context}] Error checking ESL connection: {e}")
            return False
    
    async def _emit_event(self, event_type: VoiceEventType, **data) -> None:
        """
        Emite evento de forma segura (só se EventBus estiver disponível).
        
        Args:
            event_type: Tipo do evento
            **data: Dados adicionais do evento
        """
        if self.events:
            try:
                await self.events.emit(VoiceEvent(
                    type=event_type,
                    call_uuid=self.a_leg_uuid,
                    data=data,
                    source="transfer_manager"
                ))
            except Exception as e:
                logger.warning(f"Error emitting event {event_type.value}: {e}")
    
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
        
        # NOTA: TRANSFER_DIALING será emitido após validações (ESL, A-leg, ramal)
        
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
                    await self._emit_event(
                        VoiceEventType.TRANSFER_FAILED,
                        reason="esl_connection_failed",
                        error=str(e),
                    )
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
                
                # Emitir evento TRANSFER_REJECTED - ramal offline
                await self._emit_event(
                    VoiceEventType.TRANSFER_REJECTED,
                    reason="destination_offline",
                    destination=destination,
                )
                
                # NOTA: Cliente ainda NÃO está na conferência neste ponto (STEP 2 < STEP 3)
                # O _execute_intelligent_handoff fará unhold_call() + _handle_transfer_result
                # que aplicará a proteção anti-corte
                
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.REJECTED,
                    error=f"Ramal {destination} não está disponível no momento",
                    duration_ms=int((time.time() - start_time) * 1000)
                )
            logger.info(f"{elapsed()} STEP 2: ✅ Ramal disponível")
            
            # Emitir evento TRANSFER_DIALING após todas as validações
            await self._emit_event(
                VoiceEventType.TRANSFER_DIALING,
                destination=destination,
                context=context,
                caller_name=caller_name,
            )
            
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
                
                # Determinar motivo da falha baseado no hangup_cause
                cause = self._b_leg_hangup_cause or "NO_ANSWER"
                cause_upper = cause.upper()
                
                if "BUSY" in cause_upper or "CONGESTION" in cause_upper:
                    reason = "busy"
                    error_msg = "Ramal ocupado. Você pode deixar um recado."
                    logger.warning(f"{elapsed()} STEP 4: ❌ Ramal OCUPADO ({cause})")
                elif "REJECTED" in cause_upper or "DECLINE" in cause_upper:
                    reason = "rejected"
                    error_msg = "Chamada não foi aceita. Você pode deixar um recado."
                    logger.warning(f"{elapsed()} STEP 4: ❌ Chamada REJEITADA ({cause})")
                elif "NOT_REGISTERED" in cause_upper or "ABSENT" in cause_upper or "UNALLOCATED" in cause_upper:
                    reason = "offline"
                    error_msg = "Ramal não está disponível. Você pode deixar um recado."
                    logger.warning(f"{elapsed()} STEP 4: ❌ Ramal OFFLINE ({cause})")
                else:
                    reason = "no_answer"
                    error_msg = "Ramal não atendeu. Você pode deixar um recado."
                    logger.warning(f"{elapsed()} STEP 4: ❌ Ramal NÃO ATENDEU ({cause})")
                
                # Tirar cliente da espera e dar feedback
                await self._cleanup_and_return(reason=error_msg.split('.')[0])
                await self._stop_hangup_monitor()
                
                # Emitir evento com o motivo correto
                await self._emit_event(
                    VoiceEventType.TRANSFER_TIMEOUT,
                    reason=reason,
                    destination=destination,
                    hangup_cause=cause,
                )
                
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.REJECTED,
                    conference_name=self.conference_name,
                    error=error_msg,
                    duration_ms=int((time.time() - start_time) * 1000)
                )
            logger.info(f"{elapsed()} STEP 4: ✅ Ramal atendeu: {self.b_leg_uuid}")
            
            # Emitir evento TRANSFER_ANSWERED
            await self._emit_event(
                VoiceEventType.TRANSFER_ANSWERED,
                destination=destination,
                b_leg_uuid=self.b_leg_uuid,
            )
            
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
            
            # Emitir evento TRANSFER_ANNOUNCING
            await self._emit_event(
                VoiceEventType.TRANSFER_ANNOUNCING,
                destination=destination,
                b_leg_uuid=self.b_leg_uuid,
                announcement=announcement,
            )
            
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
            
            decision = await self._announce_to_b_leg(announcement, context, caller_name)
            
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
            
            # NOTA: Evento de decisão (ACCEPTED/REJECTED/TIMEOUT) será emitido
            # pelo método correspondente (_handle_accepted, _handle_rejected)
            
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
            
            # Emitir evento TRANSFER_FAILED
            await self._emit_event(
                VoiceEventType.TRANSFER_FAILED,
                reason="unexpected_error",
                error=str(e),
            )
            
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
            elif uuid == self.b_leg_uuid or uuid == self._pending_b_leg_uuid:
                # Detecta hangup do B-leg confirmado OU do B-leg pendente (durante originate)
                # Isso captura rejeição de chamada antes mesmo do B-leg ser confirmado
                logger.info(f"📞 [HANGUP_MONITOR] B-leg hangup detected: {hangup_cause} (uuid={uuid[:8]}...)")
                self._b_leg_hangup_cause = hangup_cause
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
        """
        Para o stream de áudio do Voice AI no A-leg.
        
        IMPORTANTE: Usamos STOP porque o uuid_transfer vai fechar a conexão
        WebSocket de qualquer forma. O 'pause' não ajuda aqui.
        
        Quando a transferência terminar, _return_a_leg_to_voiceai() vai
        fazer um novo 'start' para reconectar ao RealtimeServer.
        
        O RealtimeServer (server.py) reutiliza a RealtimeSession existente
        quando o mesmo call_uuid reconecta, preservando o contexto.
        """
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
        - mute: Cliente não pode falar (ainda, será desmutado após aceitação)
        - deaf: Cliente não pode ouvir a conversa entre IA e atendente
        
        IMPORTANTE: NÃO setamos hangup_after_conference aqui!
        Isso é feito em _handle_accepted APENAS quando a transferência for aceita.
        Se o atendente desligar/recusar, o cliente fica na linha para deixar recado.
        
        A conferência será criada automaticamente.
        
        Ref: Context7 /signalwire/freeswitch-docs - conference, endconf
        """
        logger.info(f"_move_a_leg_to_conference: START - A-leg={self.a_leg_uuid}")
        
        profile = self.config.conference_profile
        
        # IMPORTANTE: NÃO setar hangup_after_conference aqui!
        # Isso será setado APENAS quando a transferência for ACEITA (em _handle_accepted).
        # Se setar antes, e o atendente desligar/recusar, o cliente também desliga
        # automaticamente, perdendo a chance de deixar recado.
        # Ref: Bug fix - cliente desligava junto com atendente que recusava
        
        # Comando: uuid_transfer UUID 'conference:NAME@PROFILE+flags{...}' inline
        # Nota: FreeSWITCH 1.10+ aceita essa sintaxe
        # NOTA: As chaves {mute|deaf} são interpretadas pelo FreeSWITCH
        # Python f-string requer {{ }} para escapar, resultando em { } no output
        #
        # FLAGS IMPORTANTES:
        # - mute: Cliente não pode falar na conferência
        # - deaf: Cliente não pode OUVIR a conferência (evita ouvir IA conversando com atendente)
        # O cliente fica em silêncio durante a transferência (MOH removido)
        transfer_cmd = (
            f"uuid_transfer {self.a_leg_uuid} "
            f"'conference:{self.conference_name}@{profile}+flags{{mute|deaf}}' inline"
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
        
        # IMPORTANTE: Originar B-leg para &park() primeiro, NÃO para conferência!
        # 
        # Motivo: uuid_audio_stream NÃO funciona em canais que já estão em conferência
        # porque mod_conference gerencia o áudio internamente.
        #
        # Fluxo CORRETO:
        # 1. Originar B-leg com inline dialplan (answer + park)
        # 2. Iniciar uuid_audio_stream no B-leg (funciona porque está answered)
        # 3. Fazer anúncio via OpenAI
        # 4. Se ACEITO: Mover B-leg para conferência via uuid_transfer
        # 5. Se RECUSADO: Desligar B-leg
        #
        # SINTAXE FREESWITCH para múltiplas aplicações:
        # - "&app()" aceita apenas UMA aplicação
        # - Para múltiplas: usar inline dialplan 'app1:arg1,app2:arg2' inline
        # - Ref: https://github.com/signalwire/freeswitch-docs - Inline-Dialplan
        #
        # Formato: 'answer:,park:' inline
        # - answer: faz answer do canal (estado ACTIVE)
        # - park: coloca em espera aguardando comandos
        app = "'answer:,park:' inline"
        
        logger.info(f"_originate_b_leg: Dial string: {dial_string}")
        logger.info(f"_originate_b_leg: App: {app}")
        
        try:
            # Registrar UUID pendente para detecção de rejeição via hangup_monitor
            self._pending_b_leg_uuid = candidate_uuid
            self._b_leg_hangup_cause = None
            self._b_leg_hangup_event.clear()
            
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
                self._pending_b_leg_uuid = None
                return False
            
            # Polling para verificar se B-leg foi criado
            # Máximo de tentativas baseado no timeout de originate
            max_attempts = min(timeout, 30)  # Máximo 30 tentativas (30 segundos)
            logger.info(f"_originate_b_leg: Starting polling (max {max_attempts} attempts)...")
            
            for attempt in range(int(max_attempts)):
                # Verificar PRIMEIRO se B-leg foi rejeitado (via evento, mais rápido)
                if self._b_leg_hangup_event.is_set():
                    cause = self._b_leg_hangup_cause or "UNKNOWN"
                    logger.warning(f"_originate_b_leg: ❌ B-leg REJECTED/HANGUP: {cause}")
                    self._pending_b_leg_uuid = None
                    return False
                
                # Sleep curto (0.3s) para resposta mais rápida
                await asyncio.sleep(0.3)
                
                # Verificar se B-leg existe (timeout curto)
                try:
                    b_exists = await asyncio.wait_for(
                        self.esl.uuid_exists(candidate_uuid),
                        timeout=1.0
                    )
                    logger.debug(f"_originate_b_leg: Attempt {attempt + 1}: B-leg exists = {b_exists}")
                except asyncio.TimeoutError:
                    logger.warning(f"_originate_b_leg: Attempt {attempt + 1}: uuid_exists TIMEOUT")
                    continue  # Tentar novamente
                
                if b_exists:
                    # SUCESSO: Agora podemos atribuir o UUID ao estado da classe
                    self.b_leg_uuid = candidate_uuid
                    self._pending_b_leg_uuid = None
                    logger.info(f"_originate_b_leg: ✅ B-leg {self.b_leg_uuid} answered after {(attempt + 1) * 0.3:.1f}s")
                    return True
                
                # Verificar se A-leg ainda existe (timeout curto)
                if self._check_a_leg_hangup():
                    logger.warning(f"_originate_b_leg: ❌ A-leg hangup detected (attempt {attempt + 1})")
                    self._pending_b_leg_uuid = None
                    return False
                
                # Log a cada ~5 segundos (15 * 0.3 = 4.5s)
                if (attempt + 1) % 15 == 0:
                    elapsed = (attempt + 1) * 0.3
                    logger.info(f"_originate_b_leg: Still waiting for B-leg... ({elapsed:.1f}s)")
            
            logger.warning(f"_originate_b_leg: ❌ B-leg {candidate_uuid} not answered after {max_attempts * 0.3:.1f}s")
            self._pending_b_leg_uuid = None
            # NÃO atribuir b_leg_uuid - originate falhou
            return False
            
        except Exception as e:
            logger.error(f"Failed to originate B-leg: {e}")
            self._pending_b_leg_uuid = None
            # NÃO atribuir b_leg_uuid - exceção ocorreu
            return False
    
    async def _announce_to_b_leg(
        self,
        announcement: str,
        context: str,
        caller_name: Optional[str] = None,
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
        
        # Prompt para o agente - inclui nome do cliente para responder perguntas
        system_prompt = self._build_announcement_prompt(context, caller_name)
        
        # Mensagem inicial - usar o anúncio já formatado (já contém "Olá, tenho...")
        # O announcement vem de _build_announcement_for_human e já está completo
        initial_message = (
            f"{announcement}. Você pode atender agora?"
        )
        
        try:
            # Criar sessão de anúncio - passar evento de hangup para cancelamento rápido
            self._announcement_session = ConferenceAnnouncementSession(
                esl_client=self.esl,
                b_leg_uuid=self.b_leg_uuid,
                system_prompt=system_prompt,
                initial_message=initial_message,
                model=self.config.openai_model,
                voice=self.config.openai_voice,
                courtesy_message=self.config.courtesy_message,
                a_leg_hangup_event=self._a_leg_hangup_event,
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
    
    def _build_announcement_prompt(self, context: str, caller_name: Optional[str] = None) -> str:
        """
        Constrói prompt de sistema para o anúncio.
        
        PRIORIDADE:
        1. Usa prompt customizado do banco de dados (config.announcement_prompt) se disponível
        2. Usa prompt padrão hardcoded como fallback
        
        IMPORTANTE: Este prompt é crítico para garantir que a IA:
        1. Não interprete saudações como aceitação
        2. Faça um anúncio claro e breve
        3. Aguarde confirmação EXPLÍCITA antes de chamar accept_transfer
        4. RESPONDA PERGUNTAS do atendente antes de aceitar
        
        Ref: Bug identificado no log - IA interpretou "Alô" como aceitação
        """
        # Informações do cliente para responder perguntas
        caller_info = ""
        if caller_name:
            caller_info = f"""
INFORMAÇÕES DO CLIENTE (USE EXATAMENTE ESTAS INFORMAÇÕES):
- Nome do cliente: {caller_name}
- Motivo da ligação: {context}
- Se perguntarem "como a pessoa se chama?", diga EXATAMENTE: "{caller_name}"
- Se perguntarem "qual o assunto?" ou "sobre o que?", diga EXATAMENTE: "{context}"
- IMPORTANTE: Use as PALAVRAS EXATAS acima, não resuma nem interprete
- NUNCA invente informações!
"""
        else:
            caller_info = f"""
INFORMAÇÕES DO CLIENTE:
- Nome do cliente: Não informado
- Motivo da ligação: {context}
- Se perguntarem "como a pessoa se chama?", diga: "O cliente não informou o nome"
- Se perguntarem "qual o assunto?", diga EXATAMENTE: "{context}"
- IMPORTANTE: Use as PALAVRAS EXATAS acima, não resuma nem interprete
- NUNCA invente informações!
"""
        
        # PRIORIDADE: Usar prompt do banco de dados se disponível
        if self.config.announcement_prompt:
            # Injetar variáveis dinâmicas no prompt customizado
            custom_prompt = self.config.announcement_prompt
            custom_prompt = custom_prompt.replace("{context}", context)
            custom_prompt = custom_prompt.replace("{caller_name}", caller_name or "Não informado")
            custom_prompt = custom_prompt.replace("{caller_info}", caller_info)
            logger.info("Using custom announcement prompt from database")
            return custom_prompt
        
        # FALLBACK: Prompt padrão hardcoded
        return f"""# Role & Objective
Você é a secretária virtual anunciando uma ligação para um atendente interno.
Seu objetivo é: informar quem está na linha e obter uma decisão clara (aceitar ou recusar).

CONTEXTO DA LIGAÇÃO: {context}
{caller_info}

# Personality & Tone
- Profissional e objetiva.
- Breve (cliente está aguardando em espera).
- 1 frase por resposta, máximo 2.

# Language
- Português do Brasil.
- Linguagem formal mas acessível.

# Instructions/Rules

## Regra Principal
- ANUNCIE e aguarde decisão EXPLÍCITA.
- NÃO faça perguntas sobre o cliente - você já tem as informações acima.
- Se o atendente perguntar algo, responda com as INFORMAÇÕES acima.
- NA DÚVIDA → PERGUNTE NOVAMENTE (nunca assuma aceitação ou recusa)

## Saudações Genuínas NÃO são Decisão
Estes são APENAS cumprimentos - NÃO é aceitação NEM recusa:
- "Alô", "Oi", "Olá", "Pois não", "Sim?", "Fala"
- "Bom dia", "Boa tarde", "Boa noite"
- "Tudo bem?", "Como vai?", "Beleza?", "Opa", "E aí"

ATENÇÃO - Expressões IRÔNICAS no Brasil (indicam impaciência/recusa educada):
- "Meu querido", "Minha querida", "Meu amigo" → SARCÁSTICO em atendimento profissional
- Quando ouvir isso, trate como POSSÍVEL RECUSA e PERGUNTE diretamente:
  "Entendi. Você pode atender essa ligação agora ou prefere que eu anote o recado?"

Após saudação ou resposta ambígua: REPITA seu anúncio e pergunte novamente.

## Quando Perguntar Algo
- "Quem é?" / "Como se chama?" → Diga EXATAMENTE o nome das INFORMAÇÕES acima
- "Qual o assunto?" / "Sobre o que?" → Diga EXATAMENTE o motivo das INFORMAÇÕES acima
  (use as PALAVRAS EXATAS do cliente, não resuma nem interprete)
- Após responder: "Pode atendê-lo agora?"

# Tools

## accept_transfer()
Usar SOMENTE com confirmação EXPLÍCITA e INEQUÍVOCA:
- "Pode passar" / "Pode conectar" / "Manda" / "Ok, pode"
- "Sim, pode passar" / "Sim, manda" / "Tá bom, pode passar"
- "Pode transferir" / "Coloca na linha" / "Pode colocar"

NÃO ACEITAR:
- "Sim" sozinho após saudação
- Expressões irônicas ("meu querido", "meu amigo") → PERGUNTE de novo
- Perguntas ("quem é?", "qual o assunto?")

## reject_transfer(reason)
Usar SOMENTE com recusa EXPLÍCITA:
- "Não posso agora" / "Estou ocupado" / "Liga depois"
- "Não quero" / "Agora não" / "Não dá" / "Depois"
- "Não tenho como" / "Não vai dar" / "Recuso"

NÃO REJEITAR:
- Saudações genuínas ("Alô", "Oi", "Bom dia")
- Perguntas ("Quem é?", "Qual assunto?")
- Qualquer resposta que NÃO seja recusa explícita

# Conversation Flow

## 1) Anúncio Inicial
"Olá, tenho {caller_name or 'um cliente'} aguardando sobre {context}. Pode atendê-lo?"

## 2) Se Saudação ou Cumprimento
Atendente: "Alô?" / "Oi" / "Bom dia" / "Boa tarde" / "Boa noite" / "Quem?" / "Pronto!"
Você: "Tenho {caller_name or 'um cliente'} na linha sobre {context}. Pode atender agora?"
→ NÃO chame nenhuma função, apenas repita o anúncio.

## 3) Se Pergunta
Atendente: "Quem é?"
Você: "É {caller_name or 'o cliente'}. Pode atendê-lo?"
→ NÃO chame nenhuma função, apenas responda e pergunte.

## 4) Se Aceitar EXPLICITAMENTE
Atendente: "Pode passar" / "Manda" / "Pode colocar"
→ Chame accept_transfer() IMEDIATAMENTE

## 5) Se Recusar EXPLICITAMENTE
Atendente: "Não posso agora" / "Estou ocupado"
→ Chame reject_transfer(reason) IMEDIATAMENTE

## 6) Se Resposta Ambígua
Atendente: resposta que não é claramente aceite nem recusa
→ NÃO chame nenhuma função. Pergunte: "Então pode atender a ligação agora?"

# REGRAS CRÍTICAS

1. NUNCA invente informações - use APENAS o que está nas INFORMAÇÕES acima
2. NUNCA interprete saudação/cumprimento como aceitação OU recusa
3. SEMPRE aguarde confirmação EXPLÍCITA e INEQUÍVOCA antes de accept_transfer
4. SEMPRE aguarde recusa EXPLÍCITA antes de reject_transfer
5. NA DÚVIDA → PERGUNTE NOVAMENTE (não assuma decisão)
6. Seja BREVE - o cliente está esperando
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
        
        # Emitir evento TRANSFER_ACCEPTED
        await self._emit_event(
            VoiceEventType.TRANSFER_ACCEPTED,
            b_leg_uuid=self.b_leg_uuid,
            conference_name=self.conference_name,
        )
        
        try:
            # =========================================================================
            # FLUXO CORRETO após aceitação:
            # 
            # Estado atual:
            # - A-leg está na conferência (mutado, SEM hangup_after_conference)
            # - B-leg está em &park() (fora da conferência)
            # 
            # Passos:
            # 1. Parar uuid_audio_stream do B-leg
            # 2. Setar hangup_after_conference=true no A-leg (APENAS agora que aceitou!)
            # 3. Mover B-leg para conferência com flags {moderator|endconf}
            # 4. Desmutar A-leg na conferência
            # 
            # IMPORTANTE: hangup_after_conference é setado AQUI, não em _move_a_leg_to_conference,
            # para que se o atendente desligar/recusar ANTES de aceitar, o cliente
            # continue na linha e possa deixar recado.
            # 
            # Ref: Context7 /signalwire/freeswitch-docs - conference, endconf
            # =========================================================================
            
            profile = self.config.conference_profile
            
            # 1. Parar stream de áudio do OpenAI no B-leg
            if self.b_leg_uuid:
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop"),
                        timeout=3.0
                    )
                    logger.debug("B-leg audio stream stopped")
                    await asyncio.sleep(0.2)
                except (asyncio.TimeoutError, Exception) as e:
                    logger.debug(f"Could not stop B-leg stream: {e}")
            
            # 2. AGORA setar hangup_after_conference em AMBOS os legs
            # Só setamos aqui porque a transferência foi ACEITA.
            # Se o atendente desligasse ANTES de aceitar, o cliente ficaria na linha
            # para receber feedback e poder deixar recado.
            # 
            # IMPORTANTE: Setar em AMBOS os legs garante que:
            # - Se A-leg (cliente) desligar: conferência termina → B-leg desliga
            # - Se B-leg (atendente) desligar: conferência termina (endconf) → A-leg desliga
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_setvar {self.a_leg_uuid} hangup_after_conference true"),
                    timeout=2.0
                )
                logger.debug("_handle_accepted: hangup_after_conference=true set on A-leg")
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug(f"_handle_accepted: Could not set hangup_after_conference on A-leg: {e}")
            
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_setvar {self.b_leg_uuid} hangup_after_conference true"),
                    timeout=2.0
                )
                logger.debug("_handle_accepted: hangup_after_conference=true set on B-leg")
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug(f"_handle_accepted: Could not set hangup_after_conference on B-leg: {e}")
            
            # 3. Mover B-leg para conferência com flags corretas
            # moderator: pode controlar a conferência
            # endconf: quando B-leg sair, TODOS os membros são desconectados
            transfer_b_cmd = (
                f"uuid_transfer {self.b_leg_uuid} "
                f"'conference:{self.conference_name}@{profile}+flags{{moderator|endconf}}' inline"
            )
            logger.info(f"Moving B-leg to conference: {transfer_b_cmd}")
            
            try:
                result = await asyncio.wait_for(
                    self.esl.execute_api(transfer_b_cmd),
                    timeout=5.0
                )
                logger.info(f"B-leg transfer result: {result}")
                
                if "-ERR" in str(result):
                    logger.error(f"Failed to move B-leg to conference: {result}")
                    # Tentar continuar mesmo assim
                else:
                    # Aguardar B-leg entrar na conferência
                    await asyncio.sleep(0.5)
                    
            except asyncio.TimeoutError:
                logger.warning("B-leg transfer timeout, continuing anyway")
            
            # 4. Desmutar, tirar deaf e adicionar endconf na A-leg
            # NOTA: Os comandos unmute/undeaf requerem member_id (número), não UUID
            # IMPORTANTE: Adicionar 'endconf' ao A-leg garante que quando QUALQUER
            # membro sair, a conferência termine e ambos desliguem.
            # Ref: Context7 - conference <confname> unmute/undeaf <member_id>|all|last|non_moderator
            
            member_id = await self._get_conference_member_id(self.a_leg_uuid)
            
            if member_id:
                # Unmute: permitir que cliente FALE
                unmute_cmd = f"conference {self.conference_name} unmute {member_id}"
                # Undeaf: permitir que cliente OUÇA
                undeaf_cmd = f"conference {self.conference_name} undeaf {member_id}"
                
                logger.debug(f"Unmute command: {unmute_cmd}")
                logger.debug(f"Undeaf command: {undeaf_cmd}")
                
                try:
                    # Executar ambos comandos
                    unmute_result = await asyncio.wait_for(
                        self.esl.execute_api(unmute_cmd),
                        timeout=3.0
                    )
                    undeaf_result = await asyncio.wait_for(
                        self.esl.execute_api(undeaf_cmd),
                        timeout=3.0
                    )
                    
                    if "-ERR" in str(unmute_result):
                        logger.warning(f"Unmute may have failed: {unmute_result}")
                    else:
                        logger.info(f"A-leg unmuted (member_id={member_id})")
                    
                    if "-ERR" in str(undeaf_result):
                        logger.warning(f"Undeaf may have failed: {undeaf_result}")
                    else:
                        logger.info(f"A-leg undeaf (member_id={member_id})")
                        
                except asyncio.TimeoutError:
                    logger.warning("Unmute/undeaf command timeout")
            else:
                # Fallback: desmutar e tirar deaf de todos os não-moderadores
                logger.warning("Could not find A-leg member_id, unmuting/undeafing all non_moderator")
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"conference {self.conference_name} unmute non_moderator"),
                        timeout=3.0
                    )
                    await asyncio.wait_for(
                        self.esl.execute_api(f"conference {self.conference_name} undeaf non_moderator"),
                        timeout=3.0
                    )
                except asyncio.TimeoutError:
                    pass
            
            # 5. Pronto! Ambos estão na conferência
            logger.info("🎉 Transfer completed - both parties in conference")
            logger.info(f"   Conference: {self.conference_name}")
            logger.info(f"   A-leg (cliente): {self.a_leg_uuid} - unmuted+undeaf")
            logger.info(f"   B-leg (atendente): {self.b_leg_uuid} - moderator|endconf")
            
            # 6. Iniciar monitor de hangup pós-conferência
            # Isso garante que quando o A-leg desligar, o B-leg também seja desligado
            # (e vice-versa, embora o B-leg com endconf já faça isso automaticamente)
            asyncio.create_task(self._monitor_conference_hangup())
            
            # Emitir evento TRANSFER_COMPLETED (bridge feito com sucesso)
            await self._emit_event(
                VoiceEventType.TRANSFER_COMPLETED,
                b_leg_uuid=self.b_leg_uuid,
                conference_name=self.conference_name,
            )
            
            return ConferenceTransferResult(
                success=True,
                decision=TransferDecision.ACCEPTED,
                b_leg_uuid=self.b_leg_uuid,
                conference_name=self.conference_name
            )
            
        except Exception as e:
            logger.error(f"Failed to complete transfer: {e}")
            await self._emit_event(
                VoiceEventType.TRANSFER_FAILED,
                reason="bridge_failed",
                error=str(e),
            )
            return ConferenceTransferResult(
                success=False,
                decision=TransferDecision.ERROR,
                error=str(e)
            )
    
    async def _monitor_conference_hangup(self) -> None:
        """
        Monitora a conferência para detectar quando um membro sai e desligar o outro.
        
        Isso é necessário porque:
        - B-leg tem 'endconf', então quando B-leg sai, A-leg é desligado automaticamente
        - A-leg NÃO tem 'endconf', então quando A-leg sai, B-leg fica sozinho
        
        Este monitor verifica periodicamente os membros e desliga o sobrevivente.
        """
        logger.info(f"📞 [HANGUP_MONITOR] Starting post-conference monitor for {self.conference_name}")
        
        check_interval = 2.0  # Verificar a cada 2 segundos
        max_checks = 300  # Máximo 10 minutos (300 * 2s)
        
        for _ in range(max_checks):
            try:
                await asyncio.sleep(check_interval)
                
                # Verificar se a conferência ainda existe e quantos membros tem
                result = await asyncio.wait_for(
                    self.esl.execute_api(f"conference {self.conference_name} list count"),
                    timeout=3.0
                )
                
                result_str = str(result).strip()
                
                # Se conferência não existe mais ou tem 0 membros, parar
                if "-ERR" in result_str or "not found" in result_str.lower():
                    logger.info(f"📞 [HANGUP_MONITOR] Conference {self.conference_name} ended")
                    break
                
                # Parsear o número de membros
                # O output pode ser "1" ou "Conference xyz has 1 member" etc
                try:
                    # Tentar extrair número
                    import re
                    numbers = re.findall(r'\d+', result_str)
                    if numbers:
                        member_count = int(numbers[0])
                        
                        if member_count == 0:
                            logger.info(f"📞 [HANGUP_MONITOR] Conference empty, stopping monitor")
                            break
                        elif member_count == 1:
                            # Só 1 membro - alguém saiu, desligar o sobrevivente
                            logger.warning(f"📞 [HANGUP_MONITOR] Only 1 member left, kicking remaining")
                            
                            # Desligar todos os membros restantes
                            try:
                                await asyncio.wait_for(
                                    self.esl.execute_api(f"conference {self.conference_name} kick all"),
                                    timeout=3.0
                                )
                                logger.info(f"📞 [HANGUP_MONITOR] Kicked remaining member")
                            except Exception as e:
                                logger.debug(f"Could not kick remaining member: {e}")
                            break
                except (ValueError, IndexError):
                    pass
                    
            except asyncio.CancelledError:
                logger.debug(f"📞 [HANGUP_MONITOR] Cancelled")
                break
            except Exception as e:
                logger.debug(f"📞 [HANGUP_MONITOR] Check error: {e}")
                # Continuar monitorando
        
        logger.info(f"📞 [HANGUP_MONITOR] Monitor ended for {self.conference_name}")
    
    async def _get_conference_member_id(self, uuid: str) -> Optional[str]:
        """
        Obtém o member_id de um participante da conferência pelo UUID.
        
        O comando 'conference list' retorna linhas no formato:
        member_id;register_string;uuid;caller_id_name;caller_id_number;flags;...
        
        Ref: Context7 /signalwire/freeswitch-docs - conference list output
        
        Args:
            uuid: UUID do participante
            
        Returns:
            member_id (string numérica) ou None se não encontrado
        """
        try:
            result = await asyncio.wait_for(
                self.esl.execute_api(f"conference {self.conference_name} list"),
                timeout=3.0
            )
            
            if not result or "-ERR" in str(result):
                logger.debug(f"Conference list failed: {result}")
                return None
            
            # Parsear o output linha por linha
            # Formato: member_id;register;uuid;name;number;flags;...
            for line in str(result).strip().split('\n'):
                if not line or line.startswith('Conference'):
                    continue
                
                parts = line.split(';')
                if len(parts) >= 3:
                    member_id = parts[0].strip()
                    member_uuid = parts[2].strip()
                    
                    if member_uuid == uuid:
                        logger.debug(f"Found member_id={member_id} for uuid={uuid}")
                        return member_id
            
            logger.debug(f"UUID {uuid} not found in conference list")
            return None
            
        except asyncio.TimeoutError:
            logger.warning("Conference list timeout")
            return None
        except Exception as e:
            logger.debug(f"Error getting member_id: {e}")
            return None
    
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
        
        # Emitir evento apropriado
        if result_decision == TransferDecision.TIMEOUT:
            await self._emit_event(
                VoiceEventType.TRANSFER_TIMEOUT,
                reason=reason,
            )
        else:
            await self._emit_event(
                VoiceEventType.TRANSFER_REJECTED,
                reason=reason,
                decision=result_decision.value,
            )
        
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
        
        Remove da conferência e REINICIA stream de áudio.
        
        IMPORTANTE: O uuid_transfer fecha a conexão WebSocket do mod_audio_stream.
        Por isso, NÃO podemos usar 'resume' - precisamos usar 'start' novamente
        com a URL completa para o FreeSWITCH reconectar ao RealtimeServer.
        
        O RealtimeServer (server.py) já tem lógica para reutilizar a session
        existente quando o mesmo call_uuid reconecta, preservando:
        - Histórico da conversa
        - Nome do cliente extraído
        - Contexto do provider OpenAI
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
            
            # =================================================================
            # STEP 1: Kick A-leg da conferência
            # =================================================================
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"conference {self.conference_name} kick {self.a_leg_uuid}"),
                    timeout=2.0
                )
                logger.info("✅ A-leg removido da conferência")
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug(f"Could not kick A-leg from conference: {e}")
            
            # =================================================================
            # STEP 2: Delay para canal estabilizar após sair da conferência
            # =================================================================
            logger.info("⏳ Aguardando 200ms para canal estabilizar...")
            await asyncio.sleep(0.2)
            
            # =================================================================
            # STEP 3: REINICIAR uuid_audio_stream (não resume!)
            # 
            # IMPORTANTE: O uuid_transfer FECHA a conexão WebSocket.
            # 'resume' não funciona porque não há conexão para retomar.
            # 
            # Precisamos fazer 'start' novamente com a URL completa.
            # O RealtimeServer vai reutilizar a session existente (via session_manager).
            # =================================================================
            
            # Construir URL do WebSocket
            # Formato: ws://host:port/stream/{secretary_uuid}/{call_uuid}/{caller_id}
            ws_host = os.getenv("REALTIME_WS_HOST", "127.0.0.1")
            ws_port = os.getenv("REALTIME_WS_PORT", "8085")
            
            # Secretary UUID pode estar no self ou precisamos extrair da session
            secretary_uuid = getattr(self, 'secretary_uuid', None) or "unknown"
            
            ws_url = f"ws://{ws_host}:{ws_port}/stream/{secretary_uuid}/{self.a_leg_uuid}/{self.caller_id}"
            
            logger.info(f"🔄 Reiniciando audio stream: {ws_url}")
            
            # Primeiro garantir que qualquer stream antigo está parado
            try:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_audio_stream {self.a_leg_uuid} stop"),
                    timeout=2.0
                )
            except (asyncio.TimeoutError, Exception):
                pass  # Pode falhar se não tinha stream, ok
            
            await asyncio.sleep(0.1)  # Pequeno delay para cleanup
            
            # Iniciar novo stream
            # Formato: uuid_audio_stream <uuid> start <url> mono 8k
            start_cmd = f"uuid_audio_stream {self.a_leg_uuid} start {ws_url} mono 8k"
            logger.info(f"🔊 Executando: {start_cmd}")
            
            try:
                result = await asyncio.wait_for(
                    self.esl.execute_api(start_cmd),
                    timeout=5.0
                )
                
                result_str = str(result).strip() if result else ""
                
                if "+OK" in result_str or result_str == "":
                    logger.info(f"✅ Audio stream reiniciado com sucesso")
                elif "-ERR" in result_str:
                    logger.error(f"❌ Falha ao reiniciar audio stream: {result_str}")
                else:
                    logger.info(f"🔊 Audio stream resultado: {result_str}")
                
            except asyncio.TimeoutError:
                logger.error("❌ Timeout ao reiniciar audio stream")
            except Exception as e:
                logger.error(f"❌ Erro ao reiniciar audio stream: {e}")
            
            # =================================================================
            # STEP 4: Aguardar reconexão do WebSocket
            # O RealtimeServer vai aceitar a nova conexão e reutilizar a session.
            # Precisamos dar tempo para o FreeSWITCH conectar.
            # =================================================================
            logger.info("⏳ Aguardando 500ms para WebSocket reconectar...")
            await asyncio.sleep(0.5)
            
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
            # 1. Parar stream de áudio do B-leg (evita áudio residual) - timeout curto
            # NOTA: NÃO paramos o stream do A-leg aqui porque queremos fazer
            # RESUME em _return_a_leg_to_voiceai() para manter o contexto da conversa
            if self.b_leg_uuid:
                try:
                    await asyncio.wait_for(
                        self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop"),
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
