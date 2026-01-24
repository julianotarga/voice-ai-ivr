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
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable, Any, Tuple
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


class OriginateResult(Enum):
    """Resultado do originate do B-leg."""
    ANSWERED = "answered"           # Atendente atendeu
    REJECTED = "rejected"           # Atendente rejeitou (clicou em recusar)
    TIMEOUT = "timeout"             # Timeout esperando atender
    A_LEG_HANGUP = "a_leg_hangup"   # A-leg desligou durante o originate
    B_LEG_HANGUP = "b_leg_hangup"   # B-leg recebeu hangup event
    ERROR = "error"                 # Erro no originate


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
    
    # Warmup do anúncio (ms) - opcional (usa valor do banco quando disponível)
    announcement_warmup_ms: Optional[int] = None
    
    # Comportamento
    accept_on_timeout: bool = False
    
    # Prompts customizados
    announcement_prompt: Optional[str] = None
    courtesy_message: Optional[str] = None
    
    # Beep de conexão - toca em ambas as pernas quando o bridge é estabelecido
    # Indica ao cliente e atendente que estão conectados
    bridge_beep_enabled: bool = True
    # Formato: tone_stream://%(duração_ms,silêncio_ms,frequência_hz)
    # Exemplos:
    #   - "tone_stream://%(100,0,800)" - Beep curto 800Hz
    #   - "tone_stream://%(150,50,600);%(150,0,800)" - Duplo beep ascendente
    #   - "/usr/share/freeswitch/sounds/..." - Arquivo de áudio
    bridge_beep_tone: str = "tone_stream://%(100,50,600);%(150,0,800)"  # Duplo beep agradável


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
        self._transfer_active: bool = False  # Flag para desativar handlers após stop
        
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
                # Criar VoiceEvent com dados
                event = VoiceEvent(
                    type=event_type,
                    call_uuid=self.a_leg_uuid,
                    data={
                        "state": self._state.value,
                        **kwargs
                    },
                    source="bridge_transfer"
                )
                await self.event_bus.emit(event)
            except Exception as e:
                logger.debug(f"Could not emit event {event_type}: {e}")
    
    # =========================================================================
    # MONITOR DE HANGUP
    # =========================================================================
    
    async def _start_hangup_monitor(self) -> None:
        """
        Inicia monitoramento de eventos CHANNEL_HANGUP para A-leg e B-leg.
        
        Usa ESL event subscription (como ConferenceTransferManager).
        Quando detecta hangup, seta o asyncio.Event correspondente.
        """
        self._transfer_active = True
        self._a_leg_hangup_event.clear()
        self._b_leg_hangup_event.clear()
        
        # Handler para eventos de hangup
        async def on_hangup(event):
            if not self._transfer_active:
                return
            
            # Extrair UUID e causa do evento ESL
            uuid = event.uuid if hasattr(event, 'uuid') else (
                event.headers.get('Unique-ID', '') if hasattr(event, 'headers') else ''
            )
            hangup_cause = (
                event.headers.get('Hangup-Cause', 'UNKNOWN') 
                if hasattr(event, 'headers') else 'UNKNOWN'
            )
            
            if uuid == self.a_leg_uuid:
                logger.warning(f"{self._elapsed()} 🔴 A-leg HANGUP: {hangup_cause}")
                self._a_leg_hangup_event.set()
                # CLEANUP: Se A-leg desliga, matar B-leg imediatamente
                if self._b_leg_uuid and self._state not in (TransferState.BRIDGED, TransferState.COMPLETED):
                    logger.info(f"{self._elapsed()} 🧹 Auto-cleanup: matando B-leg após A-leg hangup")
                    await self._kill_b_leg_safe()
                    
            elif uuid == self._b_leg_uuid:
                logger.info(f"{self._elapsed()} 🔴 B-leg HANGUP: {hangup_cause}")
                self._b_leg_hangup_event.set()
        
        # Registrar handler no ESL client (como ConferenceTransferManager)
        if hasattr(self.esl, 'register_event_handler'):
            try:
                self._hangup_handler_id = await self.esl.register_event_handler(
                    event_name="CHANNEL_HANGUP",
                    callback=on_hangup,
                    uuid_filter=None  # Monitorar todos, filtrar no callback
                )
                logger.debug(f"[HANGUP_MONITOR] ESL handler registrado: {self._hangup_handler_id}")
            except Exception as e:
                logger.debug(f"[HANGUP_MONITOR] Falha ao registrar ESL handler: {e}")
                self._hangup_handler_id = None
        else:
            logger.debug("[HANGUP_MONITOR] ESL não suporta event handlers, usando polling")
            self._hangup_handler_id = None
    
    async def _stop_hangup_monitor(self) -> None:
        """Para o monitoramento de hangup."""
        self._transfer_active = False
        
        # Remover handler se registrado
        if self._hangup_handler_id and hasattr(self.esl, 'unregister_event_handler'):
            try:
                await self.esl.unregister_event_handler(self._hangup_handler_id)
                logger.debug(f"[HANGUP_MONITOR] ESL handler removido: {self._hangup_handler_id}")
            except Exception as e:
                logger.debug(f"[HANGUP_MONITOR] Erro removendo handler: {e}")
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
    
    async def _verify_channel_answered(self, uuid: str, name: str = "channel") -> bool:
        """
        Verifica se um canal está ANSWERED.
        
        IMPORTANTE: uuid_audio_stream só funciona em canal ANSWERED!
        Um canal em RINGING ainda não pode receber audio stream.
        
        PROBLEMA IDENTIFICADO:
        uuid_getvar retorna _undef_ para canais criados via bgapi originate
        porque o ESL pode não ter acesso às variáveis do canal outbound.
        
        SOLUÇÃO:
        Usar 'show channels' que retorna o Channel-State real:
        - CS_CONSUME_MEDIA = RINGING (ainda não atendeu)
        - CS_EXECUTE = ANSWERED e executando dialplan/app
        - CS_PARK = ANSWERED e em park
        - CS_EXCHANGE_MEDIA = ANSWERED e trocando mídia
        """
        try:
            # =====================================================================
            # MÉTODO: Usar 'show channels' e parsear o estado
            # Este método funciona mesmo para canais criados via bgapi
            # =====================================================================
            response = await asyncio.wait_for(
                self.esl.execute_api(f"show channels like {uuid}"),
                timeout=self.config.esl_command_timeout
            )
            
            output = str(response).strip() if response else ""
            
            # Log para debug
            if len(output) > 200:
                logger.debug(f"{self._elapsed()} {name} show channels: {output[:200]}...")
            else:
                logger.debug(f"{self._elapsed()} {name} show channels: {output}")
            
            # Verificar se canal não existe
            if "0 total" in output or not uuid in output:
                logger.debug(f"{self._elapsed()} {name} não existe (0 total)")
                return False
            
            # Parsear o estado do canal
            # Formato CSV: uuid,direction,created,created_epoch,name,state,...,callstate,...
            # Header: uuid,direction,created,...
            # Data:   f9dd849c-...,inbound,...,CS_EXECUTE,...,ACTIVE,...
            
            lines = output.split('\n')
            header = None
            state = None
            callstate = None
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Pular linha de header (começa com "uuid,")
                if line.startswith('uuid,'):
                    header = line.split(',')
                    continue
                
                # Pular linha de total (ex: "1 total.")
                if 'total' in line.lower():
                    continue
                
                # Verificar se esta linha contém o UUID
                if uuid in line:
                    parts = line.split(',')
                    
                    # Se temos header, usar índices
                    if header:
                        try:
                            state_idx = header.index('state')
                            if len(parts) > state_idx:
                                state = parts[state_idx].strip()
                        except (ValueError, IndexError):
                            pass
                        
                        try:
                            callstate_idx = header.index('callstate')
                            if len(parts) > callstate_idx:
                                callstate = parts[callstate_idx].strip()
                        except (ValueError, IndexError):
                            pass
                    
                    # Fallback: buscar padrões conhecidos
                    if not state:
                        for part in parts:
                            part_clean = part.strip()
                            if part_clean.startswith('CS_'):
                                state = part_clean
                                break
                    
                    if not callstate:
                        for part in parts:
                            part_clean = part.strip()
                            if part_clean in ('RINGING', 'EARLY', 'ACTIVE', 'HANGUP', 'DOWN'):
                                callstate = part_clean
                                break
                    
                    break  # Encontrou o canal, sair do loop
            
            # Se não encontrou dados
            if state is None and callstate is None:
                logger.debug(f"{self._elapsed()} {name} não encontrado ou sem estado")
                return False
            
            logger.debug(f"{self._elapsed()} {name} parsed: state={state}, callstate={callstate}")
            
            # Estados que indicam ANSWERED
            answered_states = ['CS_EXECUTE', 'CS_PARK', 'CS_EXCHANGE_MEDIA', 'CS_SOFT_EXECUTE']
            if state in answered_states:
                logger.info(f"{self._elapsed()} ✅ {name} está ANSWERED (state={state})")
                return True
            
            # Callstate ACTIVE também indica answered
            if callstate == 'ACTIVE':
                logger.info(f"{self._elapsed()} ✅ {name} está ANSWERED (callstate=ACTIVE)")
                return True
            
            # Estados que indicam RINGING
            ringing_states = ['CS_CONSUME_MEDIA', 'CS_ROUTING', 'CS_INIT', 'CS_NEW']
            if state in ringing_states:
                logger.debug(f"{self._elapsed()} ⏳ {name} ainda não answered (state={state})")
                return False
            
            # Callstate RINGING ou EARLY
            if callstate in ('RINGING', 'EARLY', 'DOWN'):
                logger.debug(f"{self._elapsed()} ⏳ {name} ainda não answered (callstate={callstate})")
                return False
            
            # Estados que indicam problema
            if state in ('CS_HANGUP', 'CS_REPORTING', 'CS_DESTROY'):
                logger.debug(f"{self._elapsed()} 🔴 {name} está terminando (state={state})")
                return False
            
            # Estado desconhecido
            logger.warning(f"{self._elapsed()} ⚠️ {name} estado desconhecido: state={state}, callstate={callstate}")
            return False
            
        except asyncio.TimeoutError:
            logger.debug(f"{self._elapsed()} {name} state check timeout")
            return False
        except Exception as e:
            logger.warning(f"{self._elapsed()} Could not verify {name} state: {e}")
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
    ) -> Tuple[Optional[str], OriginateResult]:
        """
        Origina B-leg (atendente) para park.
        
        PROTEÇÃO: Verifica hangup do A-leg durante o polling.
        CLEANUP: Mata B-leg se A-leg desligar.
        
        Returns:
            Tuple[uuid ou None, OriginateResult]
            - (uuid, ANSWERED): Atendente atendeu
            - (None, REJECTED): Atendente rejeitou rapidamente
            - (None, TIMEOUT): Timeout aguardando atender
            - (None, A_LEG_HANGUP): Cliente desligou
            - (None, B_LEG_HANGUP): B-leg recebeu hangup
            - (None, ERROR): Erro no originate
        """
        self._log_state_change(TransferState.B_LEG_RINGING, f"destination={destination}")
        
        # Gerar UUID para o B-leg
        b_leg_uuid = str(uuid4())
        self._b_leg_uuid = b_leg_uuid
        
        # Determinar dial string
        # PRIORIDADE: Usar contact direto se disponível (evita lookup que pode causar loop)
        if direct_contact:
            # Limpar contact SIP
            # Formatos possíveis:
            #   "sip:1000@177.72.14.10:59339"
            #   "<sip:1000@177.72.14.10:59339>"
            #   "sip:1000@177.72.14.10:59339;transport=UDP"
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
            dial_string = f"sofia/internal/{contact_clean}"
            logger.info(f"{self._elapsed()} ✅ Usando DIRECT contact: {dial_string}")
            
            # Variáveis do originate (direct contact)
            originate_vars = (
                f"origination_uuid={b_leg_uuid},"
                f"origination_caller_id_number={self.caller_id},"
                f"origination_caller_id_name=Secretaria_Virtual,"
                f"originate_timeout={self.config.originate_timeout},"
                f"ignore_early_media=true,"
                f"hangup_after_bridge=true"
            )
        else:
            # Fallback: user lookup (usa domain para resolver)
            dial_string = f"sofia/internal/{destination}@{self.domain}"
            logger.info(f"{self._elapsed()} 📞 Usando user lookup: {dial_string}")
            
            # Variáveis do originate (com sip_invite_params para evitar loop)
            originate_vars = (
                f"origination_uuid={b_leg_uuid},"
                f"origination_caller_id_number={self.caller_id},"
                f"origination_caller_id_name=Secretaria_Virtual,"
                f"originate_timeout={self.config.originate_timeout},"
                f"ignore_early_media=true,"
                f"hangup_after_bridge=true,"
                f"sip_invite_params=user={destination}"
            )
        
        # Usar &park() - forma mais simples e direta
        # O canal será automaticamente answered quando atender e ficará em park aguardando comandos
        cmd = f"bgapi originate {{{originate_vars}}}{dial_string} &park()"
        
        logger.info(f"{self._elapsed()} 📞 Dial: {dial_string}")
        
        success, result = await self._esl_command(cmd, timeout=5.0, description="ORIGINATE_B")
        if not success and "Job-UUID" not in result:
            logger.error(f"{self._elapsed()} ❌ Originate falhou")
            return None, OriginateResult.ERROR
        
        # Polling para aguardar atendimento
        # IMPORTANTE: Precisamos esperar o canal estar ANSWERED, não apenas existir!
        # Um canal em RINGING já existe mas ainda não pode receber uuid_audio_stream.
        logger.info(f"{self._elapsed()} ⏳ Aguardando B-leg atender (max {self.config.originate_timeout}s)...")
        max_attempts = int(self.config.originate_timeout / self.config.originate_poll_interval)
        
        channel_exists = False  # Flag para saber se já detectamos que o canal existe
        channel_existed_at_least_once = False  # Flag para detectar rejeição rápida
        consecutive_missing = 0  # Contador de polling consecutivo sem canal
        REJECTION_THRESHOLD = 3  # Após N polls sem canal (após ter existido), considera rejeitado
        
        for attempt in range(max_attempts):
            # CLEANUP: Verificar hangup do A-leg
            if self._a_leg_hangup_event.is_set():
                logger.warning(f"{self._elapsed()} 🔴 A-leg desligou durante originate")
                await self._kill_b_leg_safe()
                return None, OriginateResult.A_LEG_HANGUP
            
            # Verificar hangup do B-leg (rejeitou antes de atender)
            if self._b_leg_hangup_event.is_set():
                logger.warning(f"{self._elapsed()} 🔴 B-leg rejeitou/não atendeu (hangup event)")
                return None, OriginateResult.B_LEG_HANGUP
            
            # Verificar se canal existe
            current_exists = await self._verify_channel_exists(b_leg_uuid, "B-leg")
            
            if current_exists:
                # Canal existe
                consecutive_missing = 0  # Reset contador
                
                if not channel_existed_at_least_once:
                    channel_existed_at_least_once = True
                    channel_exists = True
                    logger.info(f"{self._elapsed()} 📞 B-leg {b_leg_uuid[:8]}... está TOCANDO (channel criado)")
                
                # Verificar se está ANSWERED
                if await self._verify_channel_answered(b_leg_uuid, "B-leg"):
                    logger.info(f"{self._elapsed()} ✅ B-leg {b_leg_uuid[:8]}... ATENDEU! (channel answered)")
                    self._log_state_change(TransferState.B_LEG_ANSWERED)
                    # Pequeno delay para estabilização após answer
                    await asyncio.sleep(0.2)
                    return b_leg_uuid, OriginateResult.ANSWERED
            else:
                # Canal não existe
                channel_exists = False
                consecutive_missing += 1
                
                # DETECÇÃO DE REJEIÇÃO RÁPIDA:
                # Se o canal existiu antes (estava tocando) mas agora não existe mais,
                # isso significa que o atendente rejeitou a chamada.
                if channel_existed_at_least_once and consecutive_missing >= REJECTION_THRESHOLD:
                    logger.warning(
                        f"{self._elapsed()} 🚫 B-leg REJEITADO - canal desapareceu após "
                        f"{consecutive_missing} polls ({consecutive_missing * self.config.originate_poll_interval:.1f}s)"
                    )
                    # Não precisa chamar kill porque o canal já não existe
                    return None, OriginateResult.REJECTED
            
            await asyncio.sleep(self.config.originate_poll_interval)
        
        logger.warning(f"{self._elapsed()} ⏰ Timeout aguardando B-leg")
        await self._kill_b_leg_safe()
        return None, OriginateResult.TIMEOUT
    
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
        
        # Construir system_prompt para o anúncio
        # Usa o template do config ou um padrão mais completo
        base_prompt = self.config.announcement_prompt or self._build_default_announcement_prompt(
            caller_name, announcement
        )
        
        # IMPORTANTE: Adicionar instrução OBRIGATÓRIA para perguntar
        # Mesmo que o prompt do banco não tenha essa instrução, garantimos aqui
        mandatory_instruction = """

# REGRA OBRIGATÓRIA
Ao anunciar a ligação, você DEVE SEMPRE terminar perguntando: "Você pode atender agora?"
NUNCA omita essa pergunta. É essencial para obter uma resposta clara do atendente."""
        
        system_prompt = base_prompt + mandatory_instruction
        
        # IMPORTANTE: Adicionar pergunta ao final do anúncio
        # Sem isso, o atendente não sabe que precisa responder
        initial_message = f"{announcement}. Você pode atender agora?"
        
        session = ConferenceAnnouncementSession(
            esl_client=self.esl,
            b_leg_uuid=self._b_leg_uuid,
            system_prompt=system_prompt,
            initial_message=initial_message,
            voice=self.config.openai_voice,
            model=self.config.openai_model,
            a_leg_hangup_event=self._a_leg_hangup_event,
            warmup_ms=self.config.announcement_warmup_ms,
        )
        
        try:
            result = await session.run(timeout=self.config.announcement_timeout)
            
            # CLEANUP: Verificar se A-leg desligou durante anúncio
            if self._a_leg_hangup_event.is_set():
                logger.info(f"{self._elapsed()} A-leg desligou durante anúncio")
                return TransferDecision.HANGUP
            
            # Processar resultado
            if result.accepted:
                return TransferDecision.ACCEPTED
            elif result.rejected:
                return TransferDecision.REJECTED
            else:
                return TransferDecision.TIMEOUT
                
        except Exception as e:
            logger.error(f"{self._elapsed()} ❌ Erro no anúncio: {e}")
            return TransferDecision.ERROR
    
    def _build_default_announcement_prompt(
        self,
        caller_name: Optional[str],
        context: str
    ) -> str:
        """
        Constrói o system prompt padrão para o anúncio ao atendente.
        
        Este prompt instrui a IA a:
        1. Anunciar a chamada e PERGUNTAR se pode atender
        2. Aguardar resposta clara do atendente
        3. Chamar accept_transfer ou reject_transfer conforme a resposta
        """
        caller_display = caller_name or "um cliente"
        
        return f"""Você é uma secretária virtual fazendo uma transferência de chamada.

# INFORMAÇÕES DA CHAMADA
- Cliente: {caller_display}
- Contexto: {context}

# SEU OBJETIVO
Anunciar a chamada para o atendente e obter uma resposta CLARA: aceita ou recusa.

# IMPORTANTE - SEMPRE PERGUNTE
Após anunciar, você DEVE perguntar: "Você pode atender agora?"
Se a resposta for ambígua, pergunte novamente: "Então, posso transferir a ligação?"

# COMO INTERPRETAR RESPOSTAS

## ACEITAÇÃO (chame accept_transfer)
- "Pode transferir", "Pode passar", "Manda", "Passa pra mim"
- "Sim", "Claro", "Pode", "Tudo bem", "Ok"
- "Estou disponível", "Pode conectar"

## RECUSA (chame reject_transfer com motivo)
- "Não posso agora", "Estou ocupado", "Estou em reunião"
- "Depois", "Mais tarde", "Não", "Agora não dá"
- "Liga depois", "Anota recado"

## AMBÍGUO (NÃO chame função, pergunte novamente)
- "Oi", "Alô", "Bom dia", "Boa tarde" → Repita: "Tenho {caller_display} na linha. Pode atender?"
- "Quem é?" → Responda: "É {caller_display}. Pode atendê-lo?"
- "Hmm", "Ah", "Sei" → Pergunte: "Então posso transferir a ligação?"
- Silêncio por 3+ segundos → Pergunte: "Você está aí? Pode atender a chamada?"

# REGRAS CRÍTICAS
1. NUNCA assuma aceitação sem resposta explícita
2. NUNCA assuma recusa sem resposta explícita  
3. Se em dúvida, PERGUNTE novamente
4. Seja breve e direta nas respostas
5. Use tom profissional mas amigável"""
    
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
                
                # 5. Tocar beep de conexão (se habilitado)
                if self.config.bridge_beep_enabled:
                    await self._play_bridge_beep()
                
                return True
            else:
                logger.error(f"{self._elapsed()} ❌ Bridge falhou: {result}")
                return False
                
        except Exception as e:
            logger.error(f"{self._elapsed()} ❌ Erro ao criar bridge: {e}")
            return False
    
    async def _play_bridge_beep(self) -> None:
        """
        Toca beep de conexão em ambas as pernas após o bridge.
        
        Usa uuid_broadcast com 'both' para tocar simultaneamente em ambos os canais.
        Isso indica ao cliente e atendente que estão conectados.
        """
        try:
            tone = self.config.bridge_beep_tone
            
            # Tocar em AMBAS as pernas usando 'both'
            # - 'aleg': apenas cliente ouve
            # - 'bleg': apenas atendente ouve
            # - 'both': ambos ouvem (o que queremos)
            success, result = await self._esl_command(
                f"uuid_broadcast {self.a_leg_uuid} {tone} both",
                timeout=1.0,
                description="BEEP_BOTH"
            )
            
            if success:
                logger.info(f"{self._elapsed()} 🔔 Beep de conexão tocado (ambas as pernas)")
            else:
                logger.warning(f"{self._elapsed()} ⚠️ Beep falhou: {result}")
                
        except Exception as e:
            # Não falhar o bridge por causa do beep
            logger.warning(f"{self._elapsed()} ⚠️ Erro ao tocar beep: {e}")
    
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
            
            # STEP 2.5: Verificar se ramal está registrado
            logger.info(f"{self._elapsed()} 📍 STEP 2.5: Verificando registro de {destination}...")
            
            # Obter direct_contact se não fornecido
            checked_contact = direct_contact
            if not checked_contact and hasattr(self.esl, 'check_extension_registered'):
                try:
                    is_registered, contact, check_ok = await asyncio.wait_for(
                        self.esl.check_extension_registered(destination, self.domain),
                        timeout=5.0
                    )
                    logger.info(f"{self._elapsed()} Ramal registrado: {is_registered}, contact: {contact}")
                    
                    if check_ok and not is_registered:
                        logger.warning(f"{self._elapsed()} ❌ Ramal {destination} não está registrado/online")
                        await self._rollback("Ramal offline")
                        await self._emit_event(
                            VoiceEventType.TRANSFER_REJECTED,
                            reason="destination_offline",
                            destination=destination
                        )
                        return BridgeTransferResult(
                            success=False,
                            decision=TransferDecision.ERROR,
                            error="Ramal não está disponível",
                            final_state=self._state
                        )
                    
                    # Guardar contact direto para usar no originate
                    if is_registered and contact:
                        checked_contact = contact
                        logger.info(f"{self._elapsed()} 📍 Direct contact: {checked_contact}")
                        
                except asyncio.TimeoutError:
                    logger.warning(f"{self._elapsed()} ⚠️ Timeout verificando ramal, continuando...")
                except Exception as e:
                    logger.warning(f"{self._elapsed()} ⚠️ Erro verificando ramal: {e}, continuando...")
            
            # STEP 3: Originar B-leg
            logger.info(f"{self._elapsed()} 📍 STEP 3: Originando B-leg...")
            b_leg_uuid, originate_result = await self._originate_b_leg(destination, checked_contact)
            
            if originate_result != OriginateResult.ANSWERED:
                # B-leg não atendeu - ROLLBACK
                # Determinar mensagem e decisão baseado no resultado
                if originate_result == OriginateResult.REJECTED:
                    error_msg = "Ramal rejeitou a chamada"
                    decision = TransferDecision.REJECTED
                    event_reason = "rejected_by_attendant"
                    logger.warning(f"{self._elapsed()} 🚫 B-leg REJEITOU - ROLLBACK")
                elif originate_result == OriginateResult.A_LEG_HANGUP:
                    error_msg = "Cliente desligou"
                    decision = TransferDecision.HANGUP
                    event_reason = "caller_hangup"
                    logger.warning(f"{self._elapsed()} 🔴 A-leg desligou durante originate - ROLLBACK")
                elif originate_result == OriginateResult.B_LEG_HANGUP:
                    error_msg = "Ramal desligou/rejeitou"
                    decision = TransferDecision.REJECTED
                    event_reason = "b_leg_hangup"
                    logger.warning(f"{self._elapsed()} 🔴 B-leg hangup - ROLLBACK")
                elif originate_result == OriginateResult.ERROR:
                    error_msg = "Erro ao originar chamada"
                    decision = TransferDecision.ERROR
                    event_reason = "originate_error"
                    logger.error(f"{self._elapsed()} ❌ Originate erro - ROLLBACK")
                else:  # TIMEOUT
                    error_msg = "Ramal não atendeu"
                    decision = TransferDecision.TIMEOUT
                    event_reason = "no_answer"
                    logger.warning(f"{self._elapsed()} ⏰ B-leg timeout - ROLLBACK")
                
                await self._rollback(f"originate={originate_result.value}")
                await self._emit_event(VoiceEventType.TRANSFER_TIMEOUT, reason=event_reason)
                return BridgeTransferResult(
                    success=False,
                    decision=decision,
                    error=error_msg,
                    final_state=self._state
                )
            
            await self._emit_event(VoiceEventType.TRANSFER_ANSWERED)
            
            # Estabilização após atendimento
            # 500ms é um compromisso entre latência e estabilidade do canal de áudio
            # Muito agressivo (< 300ms) pode causar problemas de sincronização
            await asyncio.sleep(0.5)
            
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
