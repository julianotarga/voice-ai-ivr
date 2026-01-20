"""
Transferência anunciada usando mod_conference do FreeSWITCH.

Substitui a abordagem de &park() que apresentava problemas de áudio.
Usa conferência temporária para conectar A-leg (cliente) e B-leg (atendente).

Ref: voice-ai-ivr/docs/announced-transfer-conference.md
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
            # STEP 0: Verificar e garantir conexão ESL
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
            
            # ============================================================
            # STEP 1: Verificar A-leg ainda existe
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 1: Verificando se A-leg existe...")
            try:
                a_exists = await asyncio.wait_for(
                    self.esl.uuid_exists(self.a_leg_uuid),
                    timeout=5.0
                )
                logger.info(f"{elapsed()} STEP 1: uuid_exists returned: {a_exists}")
            except asyncio.TimeoutError:
                logger.warning(f"{elapsed()} STEP 1: ⚠️ uuid_exists TIMEOUT, assuming A-leg exists")
                a_exists = True
            except Exception as e:
                logger.warning(f"{elapsed()} STEP 1: ⚠️ uuid_exists error: {e}, assuming A-leg exists")
                a_exists = True
            
            if not a_exists:
                logger.warning(f"{elapsed()} STEP 1: ❌ A-leg no longer exists")
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
            
            try:
                is_registered, contact, check_ok = await asyncio.wait_for(
                    self.esl.check_extension_registered(destination, self.domain),
                    timeout=5.0
                )
                logger.info(f"{elapsed()} STEP 2: Ramal registrado: {is_registered}, contact: {contact}")
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
                # NÃO colocar cliente em espera - retornar erro imediatamente
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
            try:
                a_exists = await asyncio.wait_for(
                    self.esl.uuid_exists(self.a_leg_uuid),
                    timeout=5.0
                )
                logger.info(f"{elapsed()} STEP 3: uuid_exists returned: {a_exists}")
            except asyncio.TimeoutError:
                logger.warning(f"{elapsed()} STEP 3: ⚠️ TIMEOUT checking A-leg")
                a_exists = True
            except Exception as e:
                logger.warning(f"{elapsed()} STEP 3: ⚠️ Error checking A-leg: {e}")
                a_exists = True
            
            if not a_exists:
                logger.warning(f"{elapsed()} STEP 3: ❌ Cliente desligou durante espera")
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
            originate_success = await self._originate_b_leg(destination)
            
            if not originate_success:
                logger.warning(f"{elapsed()} STEP 4: ❌ Ramal não atendeu")
                # Tirar cliente da espera e dar feedback
                await self._cleanup_and_return(reason="Ramal não atendeu")
                return ConferenceTransferResult(
                    success=False,
                    decision=TransferDecision.REJECTED,
                    conference_name=self.conference_name,
                    error="Ramal não atendeu. Você pode deixar um recado.",
                    duration_ms=int((time.time() - start_time) * 1000)
                )
            logger.info(f"{elapsed()} STEP 4: ✅ Ramal atendeu: {self.b_leg_uuid}")
            
            # Aguardar B-leg estabilizar
            logger.info(f"{elapsed()} STEP 4: Aguardando estabilização (1.5s)...")
            await asyncio.sleep(1.5)
            logger.info(f"{elapsed()} STEP 4: ✅ Ramal estável")
            
            # ============================================================
            # STEP 5: Anunciar para o atendente
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 5: Anunciando cliente para o atendente...")
            decision = await self._announce_to_b_leg(announcement, context)
            logger.info(f"{elapsed()} STEP 5: ✅ Decisão do atendente: {decision.value}")
            
            # ============================================================
            # STEP 6: Processar decisão do atendente
            # ============================================================
            logger.info(f"{elapsed()} 📍 STEP 6: Processando decisão...")
            result = await self._process_decision(decision, context)
            result.duration_ms = int((time.time() - start_time) * 1000)
            logger.info(f"{elapsed()} STEP 6: ✅ Resultado: success={result.success}, decision={result.decision.value}")
            
            return result
            
        except asyncio.CancelledError:
            logger.info("Transfer cancelled")
            await self._cleanup_on_error()
            raise
            
        except Exception as e:
            logger.error(f"Transfer failed: {e}", exc_info=True)
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
    
    async def _originate_b_leg(self, destination: str) -> bool:
        """
        Origina B-leg (atendente) direto para conferência.
        
        B-leg entra como moderador - pode falar e ouvir normalmente.
        
        Args:
            destination: Extensão destino (ex: "1001")
            
        Returns:
            bool: True se originate teve sucesso
        """
        logger.info(f"_originate_b_leg: START - destination={destination}@{self.domain}")
        
        # Gerar UUID para B-leg (local até confirmar que existe)
        candidate_uuid = str(uuid4())
        logger.info(f"_originate_b_leg: Generated candidate UUID: {candidate_uuid}")
        
        profile = self.config.conference_profile
        timeout = self.config.originate_timeout
        
        # Construir dial string
        # Format: {vars}user/destination@domain
        dial_string = (
            f"{{origination_uuid={candidate_uuid},"
            f"origination_caller_id_number={self.caller_id},"
            f"origination_caller_id_name=Secretaria_Virtual,"
            f"originate_timeout={timeout},"
            f"ignore_early_media=true}}"
            f"user/{destination}@{self.domain}"
        )
        
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
        
        # Mensagem inicial
        initial_message = (
            f"{announcement}. "
            f"Se você pode atender agora, diga 'aceito' ou 'pode conectar'. "
            f"Se não pode atender, diga 'não posso' ou 'recuso'."
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
        return f"""Você é uma assistente virtual anunciando uma ligação para um atendente humano.

CONTEXTO: {context}

INSTRUÇÕES:
1. Anuncie brevemente quem está ligando e o motivo
2. Pergunte se o atendente pode atender agora
3. IMPORTANTE: Detecte a resposta do atendente:
   - Se aceitar (dizer "sim", "aceito", "pode conectar", "pode passar"): chame accept_transfer()
   - Se recusar (dizer "não", "não posso", "ocupado", "recuso"): chame reject_transfer()
4. Seja educado, profissional e BREVE - o cliente está aguardando

REGRAS:
- Máximo 2-3 frases no anúncio
- Aguarde a resposta do atendente
- Não insista se recusarem
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
        
        IMPORTANTE: Parar stream de áudio do B-leg ANTES de unmute
        para evitar eco/feedback do OpenAI.
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
            logger.info("🎉 Transfer completed - both parties can talk")
            
            # Definir hangup_after_bridge em ambos (fire and forget com timeout)
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
            
            return ConferenceTransferResult(
                success=True,
                decision=TransferDecision.ACCEPTED,
                b_leg_uuid=self.b_leg_uuid,
                conference_name=self.conference_name
            )
            
        except Exception as e:
            logger.error(f"Failed to unmute A-leg: {e}")
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
