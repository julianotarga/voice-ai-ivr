"""
TransferManager - Gerencia transferências de chamadas com monitoramento de eventos.

Referências:
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/proposal.md
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/tasks.md (1.3)
- https://github.com/amigniter/mod_audio_stream (v1.0.3+ para pause/resume)

DECISÃO TÉCNICA IMPORTANTE (da proposal.md):
Usar uuid_broadcast + originate + uuid_bridge (NÃO uuid_transfer).

Motivo: uuid_transfer encerra a sessão ESL imediatamente, impedindo
o monitoramento do resultado. Com originate + bridge, mantemos controle
total da chamada e podemos retomar se o destino não atender.

Fluxo de attended transfer (ATUALIZADO):
1. uuid_audio_stream PAUSE para parar captura de áudio ← CRÍTICO!
2. uuid_break para parar playback do bot
3. uuid_broadcast para tocar música de espera no A-leg
4. originate para criar B-leg (chamada para destino)
5. Monitorar eventos CHANNEL_ANSWER / CHANNEL_HANGUP no B-leg
6. Se atendeu: uuid_audio_stream STOP + uuid_bridge (cliente fala com humano)
7. Se não atendeu: uuid_break + uuid_audio_stream RESUME + retomar Voice AI

IMPORTANTE - Por que pausar o audio_stream:
O mod_audio_stream com 'mono' captura apenas o áudio do caller, mas o FreeSWITCH
pode misturar áudio internamente. Além disso, o streaming bidirecional pode
causar eco se o áudio de resposta (TTS) for capturado de volta. Pausar o stream
durante transferências evita completamente esses problemas.

Multi-tenant: domain_uuid obrigatório em todas as operações.
"""

import os
import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .esl_client import AsyncESLClient, ESLEvent, OriginateResult, get_esl_client
from .transfer_destination_loader import (
    TransferDestination,
    TransferDestinationLoader,
    get_destination_loader
)

logger = logging.getLogger(__name__)

# Configurações padrão (usadas se não houver config do banco)
DEFAULT_TRANSFER_TIMEOUT = int(os.getenv("TRANSFER_DEFAULT_TIMEOUT", "30"))
DEFAULT_TRANSFER_ANNOUNCE_ENABLED = os.getenv("TRANSFER_ANNOUNCE_ENABLED", "true").lower() == "true"
DEFAULT_TRANSFER_MUSIC_ON_HOLD = os.getenv("TRANSFER_MUSIC_ON_HOLD", "local_stream://moh")


class TransferStatus(Enum):
    """Status possíveis de uma transferência."""
    PENDING = "pending"        # Aguardando iniciar
    RINGING = "ringing"        # Destino tocando
    ANSWERED = "answered"      # Destino atendeu
    SUCCESS = "success"        # Bridge estabelecido com sucesso
    BUSY = "busy"              # Destino ocupado
    NO_ANSWER = "no_answer"    # Destino não atendeu (timeout)
    DND = "dnd"                # Do Not Disturb
    OFFLINE = "offline"        # Ramal não registrado
    REJECTED = "rejected"      # Chamada rejeitada manualmente
    UNAVAILABLE = "unavailable"  # Destino indisponível (outros motivos)
    FAILED = "failed"          # Falha técnica
    CANCELLED = "cancelled"    # Cancelado (cliente desligou)


# Mapeamento de hangup causes para TransferStatus
HANGUP_CAUSE_MAP: Dict[str, TransferStatus] = {
    # Sucesso
    "NORMAL_CLEARING": TransferStatus.SUCCESS,
    "NORMAL_UNSPECIFIED": TransferStatus.SUCCESS,
    
    # Ocupado
    "USER_BUSY": TransferStatus.BUSY,
    "NORMAL_CIRCUIT_CONGESTION": TransferStatus.BUSY,
    
    # Não atendeu
    "NO_ANSWER": TransferStatus.NO_ANSWER,
    "NO_USER_RESPONSE": TransferStatus.NO_ANSWER,
    "ORIGINATOR_CANCEL": TransferStatus.NO_ANSWER,
    "ALLOTTED_TIMEOUT": TransferStatus.NO_ANSWER,
    
    # Rejeitado
    "CALL_REJECTED": TransferStatus.REJECTED,
    "USER_CHALLENGE": TransferStatus.REJECTED,
    
    # Offline / Não registrado
    "SUBSCRIBER_ABSENT": TransferStatus.OFFLINE,
    "USER_NOT_REGISTERED": TransferStatus.OFFLINE,
    "UNALLOCATED_NUMBER": TransferStatus.OFFLINE,
    "NO_ROUTE_DESTINATION": TransferStatus.OFFLINE,
    
    # DND
    "DO_NOT_DISTURB": TransferStatus.DND,
    
    # Falha técnica
    "DESTINATION_OUT_OF_ORDER": TransferStatus.FAILED,
    "NETWORK_OUT_OF_ORDER": TransferStatus.FAILED,
    "TEMPORARY_FAILURE": TransferStatus.FAILED,
    "SWITCH_CONGESTION": TransferStatus.FAILED,
    "MEDIA_TIMEOUT": TransferStatus.FAILED,
    "GATEWAY_DOWN": TransferStatus.FAILED,
    "INVALID_GATEWAY": TransferStatus.FAILED,
    
    # Cancelado
    "LOSE_RACE": TransferStatus.CANCELLED,
    "PICKED_OFF": TransferStatus.CANCELLED,
    "MANAGER_REQUEST": TransferStatus.CANCELLED,
    
    # Indisponível (outros)
    "BEARERCAPABILITY_NOTAVAIL": TransferStatus.UNAVAILABLE,
    "FACILITY_NOT_SUBSCRIBED": TransferStatus.UNAVAILABLE,
    "INCOMING_CALL_BARRED": TransferStatus.UNAVAILABLE,
    "OUTGOING_CALL_BARRED": TransferStatus.UNAVAILABLE,
}


# Mensagens contextuais por status
# Cada mensagem deve ser clara e oferecer uma ação (deixar recado)
STATUS_MESSAGES: Dict[TransferStatus, str] = {
    TransferStatus.SUCCESS: "Conectando você agora.",
    TransferStatus.BUSY: "O ramal está ocupado em outra ligação. Quer deixar um recado?",
    TransferStatus.NO_ANSWER: "O ramal tocou mas ninguém atendeu. Quer deixar um recado?",
    TransferStatus.DND: "O ramal está em modo não perturbe. Quer deixar um recado?",
    TransferStatus.OFFLINE: "O ramal não está conectado no momento. Quer deixar um recado?",
    TransferStatus.REJECTED: "A chamada não foi aceita. Quer deixar um recado?",
    TransferStatus.UNAVAILABLE: "O destino não está disponível. Quer deixar um recado?",
    TransferStatus.FAILED: "Não foi possível completar a transferência. Posso ajudar de outra forma?",
    TransferStatus.CANCELLED: "A chamada foi cancelada.",
}

# Mensagens detalhadas por status (para logs e debugging)
STATUS_DETAILED_MESSAGES: Dict[TransferStatus, str] = {
    TransferStatus.OFFLINE: "Ramal não registrado/desconectado - provavelmente desligado ou sem internet",
    TransferStatus.NO_ANSWER: "Tocou até timeout - ninguém atendeu ou ausente da sala",
    TransferStatus.BUSY: "Ramal em outra chamada - ocupado",
    TransferStatus.DND: "Do Not Disturb ativo - não aceita chamadas",
    TransferStatus.REJECTED: "Chamada rejeitada ativamente pelo destino",
}


@dataclass
class TransferResult:
    """Resultado de uma transferência."""
    status: TransferStatus
    destination: Optional[TransferDestination]
    hangup_cause: Optional[str] = None
    b_leg_uuid: Optional[str] = None
    duration_ms: int = 0
    retries: int = 0
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def success(self) -> bool:
        """Retorna True se transferência foi bem sucedida."""
        return self.status == TransferStatus.SUCCESS
    
    @property
    def message(self) -> str:
        """
        Retorna mensagem contextual para o status.
        
        A mensagem é personalizada com o nome do destino quando disponível,
        e inclui uma oferta de deixar recado quando apropriado.
        """
        name = self.destination.name if self.destination else "o atendente"
        
        # Mensagens personalizadas por status
        if self.status == TransferStatus.BUSY:
            return f"{name} está em outra ligação no momento. Quer deixar um recado para retornarem?"
        
        elif self.status == TransferStatus.NO_ANSWER:
            return f"O telefone de {name} tocou mas ninguém atendeu. Quer deixar um recado?"
        
        elif self.status == TransferStatus.DND:
            return f"{name} está com o ramal em modo não perturbe. Quer deixar um recado?"
        
        elif self.status == TransferStatus.OFFLINE:
            # Este é o caso de USER_NOT_REGISTERED - ramal desconectado
            return f"O ramal de {name} não está conectado no momento. Provavelmente está desligado ou fora do alcance. Quer deixar um recado?"
        
        elif self.status == TransferStatus.REJECTED:
            return f"A ligação para {name} não foi aceita. Quer deixar um recado?"
        
        elif self.status == TransferStatus.UNAVAILABLE:
            return f"{name} não está disponível no momento. Quer deixar um recado?"
        
        elif self.status == TransferStatus.SUCCESS:
            return f"Conectando você com {name} agora."
        
        elif self.status == TransferStatus.CANCELLED:
            return "A chamada foi cancelada."
        
        # Fallback para outros status
        return STATUS_MESSAGES.get(self.status, "Não foi possível completar a transferência. Posso ajudar de outra forma?")
    
    @property
    def should_offer_callback(self) -> bool:
        """
        Retorna True se deve oferecer callback/recado.
        
        Inclui falhas técnicas para garantir que o cliente
        sempre tenha uma alternativa quando a transferência falhar.
        """
        return self.status in [
            TransferStatus.BUSY,
            TransferStatus.NO_ANSWER,
            TransferStatus.DND,
            TransferStatus.OFFLINE,
            TransferStatus.REJECTED,
            TransferStatus.UNAVAILABLE,
            TransferStatus.FAILED,  # Falhas técnicas também devem oferecer alternativa
        ]


class TransferManager:
    """
    Gerencia transferências de chamadas.
    
    Uso:
        manager = TransferManager(domain_uuid, call_uuid, caller_id)
        
        # Encontrar destino
        dest, error = await manager.find_and_validate_destination("Jeni")
        
        # Executar transferência
        result = await manager.execute_attended_transfer(dest, timeout=30)
        
        if result.success:
            # Bridge estabelecido
        else:
            # Retomar Voice AI
            await manager.stop_moh_and_resume()
    """
    
    def __init__(
        self,
        domain_uuid: str,
        call_uuid: str,
        caller_id: str,
        secretary_uuid: Optional[str] = None,
        esl_client: Optional[AsyncESLClient] = None,
        destination_loader: Optional[TransferDestinationLoader] = None,
        on_resume: Optional[Callable[[], Any]] = None,
        on_transfer_complete: Optional[Callable[[TransferResult], Any]] = None,
        domain_settings: Optional[Dict[str, Any]] = None,
        voice_id: Optional[str] = None,
        announcement_tts_provider: Optional[str] = None,
    ):
        """
        Args:
            domain_uuid: UUID do tenant
            call_uuid: UUID da chamada (A-leg)
            caller_id: Número do chamador
            secretary_uuid: UUID da secretária (opcional)
            esl_client: Cliente ESL (opcional, usa singleton se não fornecido)
            destination_loader: Loader de destinos (opcional, usa singleton)
            on_resume: Callback quando retomar Voice AI
            on_transfer_complete: Callback quando transferência completar
            domain_settings: Configurações do domínio (lidas de v_voice_secretary_settings)
            voice_id: ID da voz ElevenLabs para anúncios
            announcement_tts_provider: Provider TTS para anúncios ("elevenlabs" ou "openai")
        """
        self.domain_uuid = domain_uuid
        self.call_uuid = call_uuid
        self.caller_id = caller_id
        self.secretary_uuid = secretary_uuid
        self._voice_id = voice_id
        self._announcement_tts_provider = announcement_tts_provider or "elevenlabs"
        
        self._esl = esl_client or get_esl_client()
        self._loader = destination_loader or get_destination_loader()
        
        self._on_resume = on_resume
        self._on_transfer_complete = on_transfer_complete
        
        # Configurações do domínio (do banco de dados)
        self._domain_settings = domain_settings or {}
        
        # Configurações de transferência (priorizar do banco)
        self._transfer_default_timeout = self._domain_settings.get(
            'transfer_default_timeout', DEFAULT_TRANSFER_TIMEOUT
        )
        self._transfer_announce_enabled = self._domain_settings.get(
            'transfer_announce_enabled', DEFAULT_TRANSFER_ANNOUNCE_ENABLED
        )
        self._transfer_music_on_hold = self._domain_settings.get(
            'transfer_music_on_hold', DEFAULT_TRANSFER_MUSIC_ON_HOLD
        )
        
        # Estado da transferência atual
        self._current_transfer: Optional[TransferResult] = None
        self._b_leg_uuid: Optional[str] = None
        self._moh_active = False
        self._stream_paused = False
        self._caller_hungup = False
        
        # Cache de destinos
        self._destinations: Optional[List[TransferDestination]] = None
    
    async def load_destinations(self, force_refresh: bool = False) -> List[TransferDestination]:
        """Carrega destinos de transferência."""
        if self._destinations is None or force_refresh:
            self._destinations = await self._loader.load_destinations(
                domain_uuid=self.domain_uuid,
                secretary_uuid=self.secretary_uuid,
                force_refresh=force_refresh
            )
        return self._destinations
    
    async def find_and_validate_destination(
        self,
        user_text: str
    ) -> tuple[Optional[TransferDestination], Optional[str]]:
        """
        Encontra e valida destino baseado no texto do usuário.
        
        Args:
            user_text: Texto falado pelo usuário (ex: "Jeni", "financeiro", "qualquer atendente")
        
        Returns:
            Tuple (destination, error_message)
            - Se encontrou: (destination, None)
            - Se não encontrou: (None, error_message)
        """
        destinations = await self.load_destinations()
        
        if not destinations:
            return (None, "Não há destinos de transferência configurados.")
        
        # Verificar se é pedido genérico
        generic_keywords = ["qualquer", "alguém", "atendente", "disponível", "pessoa"]
        text_lower = user_text.lower()
        
        if any(kw in text_lower for kw in generic_keywords):
            # Retornar destino padrão (fila ou ring_group)
            dest = self._loader.get_default(destinations)
            if dest:
                # Verificar horário
                available, msg = self._loader.is_within_working_hours(dest)
                if not available:
                    return (None, msg)
                return (dest, None)
            return (None, "Não há atendentes disponíveis no momento.")
        
        # Buscar destino específico
        dest = self._loader.find_by_alias(user_text, destinations, min_score=0.5)
        
        if not dest:
            # Sugerir destinos disponíveis
            available_names = [d.name for d in destinations[:5]]
            suggestion = ", ".join(available_names)
            return (
                None,
                f"Não encontrei '{user_text}'. Você pode falar com: {suggestion}."
            )
        
        # Verificar horário comercial
        available, msg = self._loader.is_within_working_hours(dest)
        if not available:
            return (None, msg)
        
        return (dest, None)
    
    async def execute_attended_transfer(
        self,
        destination: TransferDestination,
        timeout: Optional[int] = None,
        retry_on_busy: bool = True
    ) -> TransferResult:
        """
        Executa transferência attended (assistida).
        
        Fluxo:
        1. Tocar música de espera no A-leg
        2. Originar B-leg para destino
        3. Monitorar eventos (ANSWER, HANGUP)
        4. Se atendeu: criar bridge entre A e B
        5. Se não atendeu: parar música e retornar status
        
        Args:
            destination: Destino da transferência
            timeout: Timeout em segundos (usa padrão do destino se não fornecido)
            retry_on_busy: Se True, tenta novamente se ocupado
        
        Returns:
            TransferResult com status da transferência
        """
        start_time = datetime.utcnow()
        timeout = timeout or destination.ring_timeout_seconds or self._transfer_default_timeout
        retries = 0
        max_retries = destination.max_retries if retry_on_busy else 1
        
        logger.info(
            f"Starting attended transfer",
            extra={
                "call_uuid": self.call_uuid,
                "destination": destination.name,
                "destination_number": destination.destination_number,
                "timeout": timeout
            }
        )
        
        while retries < max_retries:
            try:
                # 1. Garantir conexão ESL
                if not self._esl.is_connected:
                    connected = await self._esl.connect()
                    if not connected:
                        logger.error("Failed to connect to ESL for transfer")
                        return TransferResult(
                            status=TransferStatus.FAILED,
                            destination=destination,
                            error="Falha na conexão ESL",
                            retries=retries
                        )
                
                # 2. Verificar se A-leg ainda existe
                # NOTA: Em algumas configurações, uuid_exists pode falhar devido a
                # diferenças entre conexões ESL inbound/outbound. Logamos mas continuamos.
                a_leg_exists = await self._esl.uuid_exists(self.call_uuid)
                logger.debug(
                    f"A-leg check: uuid={self.call_uuid}, exists={a_leg_exists}"
                )
                
                if not a_leg_exists:
                    # Tentar uma vez mais após pequeno delay (race condition)
                    await asyncio.sleep(0.1)
                    a_leg_exists = await self._esl.uuid_exists(self.call_uuid)
                    logger.debug(f"A-leg recheck after 100ms: exists={a_leg_exists}")
                    
                    if not a_leg_exists:
                        # Verificar se chamador marcou como desconectado
                        if self._caller_hungup:
                            return TransferResult(
                                status=TransferStatus.CANCELLED,
                                destination=destination,
                                error="Cliente desligou",
                                retries=retries
                            )
                        # Caso contrário, continuar mesmo assim (pode ser falso negativo)
                        logger.warning(
                            f"uuid_exists returned false but proceeding anyway - may be ESL inbound/outbound mismatch"
                        )
                
                # 3. Tocar música de espera
                await self._start_moh()
                
                # 4. Subscrever eventos para monitorar B-leg
                await self._esl.subscribe_events([
                    "CHANNEL_ANSWER",
                    "CHANNEL_HANGUP",
                    "CHANNEL_PROGRESS",
                    "CHANNEL_PROGRESS_MEDIA"
                ])
                
                # 5. Originar B-leg
                dial_string = self._build_dial_string(destination)
                
                logger.info(f"Originating B-leg: {dial_string}")
                
                originate_result = await self._esl.originate(
                    dial_string=dial_string,
                    app="&park()",
                    timeout=timeout,
                    variables={
                        "ignore_early_media": "true",
                        "hangup_after_bridge": "true",  # Documentação oficial: desliga após bridge encerrar
                        "origination_caller_id_number": self.caller_id,
                        "origination_caller_id_name": "Secretaria Virtual"
                    }
                )
                
                if not originate_result.success:
                    await self._stop_moh()
                    
                    # Determinar status baseado no hangup_cause
                    status = self._hangup_cause_to_status(originate_result.hangup_cause)
                    
                    logger.info(
                        f"Originate failed with cause: {originate_result.hangup_cause}",
                        extra={
                            "destination": destination.name,
                            "hangup_cause": originate_result.hangup_cause,
                            "status": status.value,
                        }
                    )
                    
                    return TransferResult(
                        status=status,
                        destination=destination,
                        hangup_cause=originate_result.hangup_cause,
                        error=originate_result.error_message,
                        retries=retries
                    )
                
                # Verificar se UUID foi retornado (sanity check)
                if not originate_result.uuid:
                    await self._stop_moh()
                    logger.error("Originate succeeded but no UUID returned - this is unexpected")
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Originate retornou sucesso sem UUID",
                        retries=retries
                    )
                
                b_leg_uuid = originate_result.uuid
                self._b_leg_uuid = b_leg_uuid
                
                # 6. IMPORTANTE: api originate é SÍNCRONO!
                # Se retornou +OK, significa que o B-leg JÁ FOI ATENDIDO
                # Não precisamos monitorar CHANNEL_ANSWER - ir direto para bridge!
                # 
                # Ref: https://developer.signalwire.com/freeswitch/Originate
                # "api originate" bloqueia até o destino atender ou falhar
                
                logger.info(f"B-leg answered (originate success): {b_leg_uuid}")
                
                # 7. Parar MOH antes do bridge (NÃO resumir stream - cliente vai p/ bridge)
                await self._stop_moh(resume_stream=False)
                
                # 8. IMPORTANTE: Definir hangup_after_bridge no A-leg ANTES do bridge
                # Isso garante que quando o humano (B) desligar, o cliente (A) também desliga
                try:
                    await self._esl.execute_api(
                        f"uuid_setvar {self.call_uuid} hangup_after_bridge true"
                    )
                    logger.debug(f"Set hangup_after_bridge=true on A-leg: {self.call_uuid}")
                except Exception as e:
                    logger.warning(f"Failed to set hangup_after_bridge on A-leg: {e}")
                
                # 9. Criar bridge IMEDIATAMENTE (B-leg já está atendida)
                logger.info(
                    f"[DEBUG] About to uuid_bridge: A={self.call_uuid} <-> B={b_leg_uuid}"
                )
                
                bridge_success = await self._esl.uuid_bridge(
                    self.call_uuid,
                    b_leg_uuid
                )
                
                logger.info(
                    f"[DEBUG] uuid_bridge returned: success={bridge_success}"
                )
                
                if bridge_success:
                    duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    final_result = TransferResult(
                        status=TransferStatus.SUCCESS,
                        destination=destination,
                        b_leg_uuid=b_leg_uuid,
                        duration_ms=duration,
                        retries=retries
                    )
                    
                    logger.info(
                        f"[DEBUG] Transfer SUCCESS - bridge established, returning to session.py",
                        extra={
                            "call_uuid": self.call_uuid,
                            "b_leg_uuid": b_leg_uuid,
                            "destination": destination.name
                        }
                    )
                    
                    if self._on_transfer_complete:
                        await self._on_transfer_complete(final_result)
                    
                    return final_result
                else:
                    # Bridge falhou - parar MOH e matar B-leg
                    await self._stop_moh()
                    await self._esl.uuid_kill(b_leg_uuid)
                    
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Falha ao criar bridge",
                        retries=retries
                    )
            
            except asyncio.CancelledError:
                # Tarefa cancelada
                if self._b_leg_uuid:
                    await self._esl.uuid_kill(self._b_leg_uuid)
                await self._stop_moh()
                raise
                
            except Exception as e:
                logger.exception(f"Transfer error: {e}")
                await self._stop_moh()
                
                return TransferResult(
                    status=TransferStatus.FAILED,
                    destination=destination,
                    error=str(e),
                    retries=retries
                )
        
        # Excedeu retentativas
        await self._stop_moh()
        return TransferResult(
            status=TransferStatus.BUSY,
            destination=destination,
            error="Excedeu número máximo de tentativas",
            retries=retries
        )
    
    # =========================================================================
    # ANNOUNCED TRANSFER: Transferência com anúncio para o humano
    # Ref: voice-ai-ivr/openspec/changes/announced-transfer/
    # =========================================================================
    
    async def execute_announced_transfer(
        self,
        destination: TransferDestination,
        announcement: str,
        ring_timeout: int = 30,
        accept_timeout: float = 5.0,
    ) -> TransferResult:
        """
        Executa transferência COM ANÚNCIO para o humano.
        
        Fluxo:
        1. MOH no A-leg (cliente)
        2. Originate B-leg (humano)
        3. Quando humano atende, TTS do anúncio
        4. Aguardar resposta (modelo híbrido):
           - Timeout 5s = aceitar (bridge)
           - DTMF 2 = recusar
           - Hangup = recusar
        5. Se aceitar: bridge A↔B
        6. Se recusar: retornar ao cliente
        
        Args:
            destination: Destino da transferência
            announcement: Texto do anúncio (ex: "Tenho o João na linha sobre plano")
            ring_timeout: Timeout de ring em segundos
            accept_timeout: Tempo para aceitar automaticamente (segundos)
        
        Returns:
            TransferResult com status
        """
        start_time = datetime.utcnow()
        
        logger.info(
            f"Starting ANNOUNCED transfer",
            extra={
                "call_uuid": self.call_uuid,
                "destination": destination.name,
                "destination_number": destination.destination_number,
                "announcement": announcement[:50] + "..." if len(announcement) > 50 else announcement,
            }
        )
        
        try:
            # 1. Garantir conexão ESL
            if not self._esl.is_connected:
                connected = await self._esl.connect()
                if not connected:
                    logger.error("Failed to connect to ESL for announced transfer")
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Falha na conexão ESL",
                    )
            
            # 2. Verificar se A-leg ainda existe
            a_leg_exists = await self._esl.uuid_exists(self.call_uuid)
            if not a_leg_exists and self._caller_hungup:
                return TransferResult(
                    status=TransferStatus.CANCELLED,
                    destination=destination,
                    error="Cliente desligou",
                )
            
            # 3. Tocar música de espera no cliente
            await self._start_moh()
            
            # 4. Subscrever eventos DTMF para o B-leg
            await self._esl.subscribe_events([
                "CHANNEL_ANSWER",
                "CHANNEL_HANGUP",
                "DTMF"
            ])
            
            # 5. Originar B-leg
            dial_string = self._build_dial_string(destination)
            
            logger.info(f"Originating B-leg for announced transfer: {dial_string}")
            
            originate_result = await self._esl.originate(
                dial_string=dial_string,
                app="&park()",
                timeout=ring_timeout,
                variables={
                    "ignore_early_media": "true",
                    "hangup_after_bridge": "true",
                    "origination_caller_id_number": self.caller_id,
                    "origination_caller_id_name": "Secretaria_Virtual"
                }
            )
            
            if not originate_result.success:
                await self._stop_moh()
                
                # Determinar status baseado no hangup_cause
                status = self._hangup_cause_to_status(originate_result.hangup_cause)
                
                logger.info(
                    f"Announced transfer originate failed",
                    extra={
                        "destination": destination.name,
                        "hangup_cause": originate_result.hangup_cause,
                        "status": status.value,
                        "is_offline": originate_result.is_offline,
                        "is_busy": originate_result.is_busy,
                        "is_no_answer": originate_result.is_no_answer,
                    }
                )
                
                return TransferResult(
                    status=status,
                    destination=destination,
                    hangup_cause=originate_result.hangup_cause,
                    error=originate_result.error_message,
                )
            
            # Verificar se UUID foi retornado (sanity check)
            if not originate_result.uuid:
                await self._stop_moh()
                logger.error("Originate succeeded but no UUID returned - this is unexpected")
                return TransferResult(
                    status=TransferStatus.FAILED,
                    destination=destination,
                    error="Originate retornou sucesso sem UUID",
                )
            
            b_leg_uuid = originate_result.uuid
            self._b_leg_uuid = b_leg_uuid
            
            # 6. Parar MOH temporariamente para o anúncio
            # (humano já atendeu - originate síncrono retornou +OK)
            # NOTA: Mantemos o MOH no cliente, apenas falamos com o humano
            
            # Aguardar para garantir que eventos ESL do originate foram processados
            # Isso evita race condition no socket quando uuid_playback é chamado
            await asyncio.sleep(1.0)
            
            logger.info(f"B-leg answered, playing announcement: {b_leg_uuid}")
            
            # 7. Tocar anúncio para o humano via ElevenLabs TTS (mesma voz da IA)
            announcement_with_instructions = (
                f"{announcement}. "
                "Pressione 2 para recusar, ou aguarde para aceitar."
            )
            
            logger.info(
                f"Generating ElevenLabs announcement for B-leg: {b_leg_uuid}",
                extra={"announcement": announcement_with_instructions[:100]}
            )
            
            # Usar TTS configurado para gerar áudio com mesma voz da IA
            from .announcement_tts import get_announcement_tts
            
            tts_service = get_announcement_tts(provider=self._announcement_tts_provider)
            audio_path = await tts_service.generate_announcement(
                announcement_with_instructions,
                voice_id=self._voice_id  # Mesma voz configurada na secretária
            )
            
            if audio_path:
                logger.info(f"Playing ElevenLabs announcement: {audio_path}")
                
                # NOTA: O ESL client já usa _command_lock para serializar comandos,
                # então não precisamos fazer reconnect. Apenas aguardar um momento
                # para garantir que eventos do originate foram processados.
                await asyncio.sleep(0.2)
                
                await self._esl.uuid_playback(b_leg_uuid, audio_path)
                # Aguardar um pouco para o áudio começar a tocar
                await asyncio.sleep(0.5)
            else:
                # Fallback: mod_flite (voz robótica)
                logger.warning("ElevenLabs TTS failed, falling back to mod_flite")
                tts_success = await self._esl.uuid_say(b_leg_uuid, announcement_with_instructions)
                await asyncio.sleep(0.5)
                
                if not tts_success:
                    # Último fallback: arquivo de áudio genérico
                    logger.warning("mod_flite also failed, using generic audio file")
                    await self._esl.uuid_playback(
                        b_leg_uuid,
                        "/usr/share/freeswitch/sounds/en/us/callie/ivr/ivr-one_moment_please.wav"
                    )
                    await asyncio.sleep(1.0)
            
            # 8. Aguardar resposta (modelo híbrido)
            response = await self._esl.wait_for_reject_or_timeout(
                b_leg_uuid,
                timeout=accept_timeout
            )
            
            # 9. Processar resposta
            if response == "accept":
                # Timeout = aceitar → Bridge
                logger.info(f"Announced transfer: human accepted (timeout)")
                
                # NÃO resumir stream - cliente vai para bridge com humano
                await self._stop_moh(resume_stream=False)
                
                # Definir hangup_after_bridge no A-leg
                await self._esl.execute_api(
                    f"uuid_setvar {self.call_uuid} hangup_after_bridge true"
                )
                
                # Criar bridge
                bridge_success = await self._esl.uuid_bridge(
                    self.call_uuid,
                    b_leg_uuid
                )
                
                if bridge_success:
                    duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    
                    result = TransferResult(
                        status=TransferStatus.SUCCESS,
                        destination=destination,
                        b_leg_uuid=b_leg_uuid,
                        duration_ms=duration,
                    )
                    
                    logger.info(
                        f"Announced transfer SUCCESS: bridge established",
                        extra={
                            "call_uuid": self.call_uuid,
                            "b_leg_uuid": b_leg_uuid,
                            "destination": destination.name,
                        }
                    )
                    
                    if self._on_transfer_complete:
                        await self._on_transfer_complete(result)
                    
                    return result
                else:
                    # Bridge falhou
                    await self._esl.uuid_kill(b_leg_uuid)
                    await self._stop_moh()
                    
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Falha ao criar bridge",
                    )
            
            elif response == "reject":
                # DTMF 2 = humano recusou
                logger.info(f"Announced transfer: human REJECTED (DTMF 2)")
                
                await self._esl.uuid_kill(b_leg_uuid)
                await self._stop_moh()
                
                return TransferResult(
                    status=TransferStatus.REJECTED,
                    destination=destination,
                    error=f"Transferência recusada por {destination.name}",
                )
            
            else:  # "hangup"
                # Humano desligou
                logger.info(f"Announced transfer: human HANGUP")
                
                await self._stop_moh()
                
                return TransferResult(
                    status=TransferStatus.REJECTED,
                    destination=destination,
                    error=f"Humano desligou: {destination.name}",
                )
        
        except asyncio.CancelledError:
            # Tarefa cancelada (ex: cliente desligou)
            if self._b_leg_uuid:
                await self._esl.uuid_kill(self._b_leg_uuid)
            await self._stop_moh()
            raise
        
        except Exception as e:
            logger.exception(f"Announced transfer error: {e}")
            await self._stop_moh()
            
            return TransferResult(
                status=TransferStatus.FAILED,
                destination=destination,
                error=str(e),
            )
    
    # =========================================================================
    # ANNOUNCED TRANSFER REALTIME: Conversa por voz com o humano
    # Opção premium que usa OpenAI Realtime para anunciar ao humano
    # =========================================================================
    
    async def execute_announced_transfer_realtime(
        self,
        destination: TransferDestination,
        announcement: str,
        caller_context: str,
        realtime_prompt: Optional[str] = None,
        ring_timeout: int = 30,
        conversation_timeout: float = 15.0,
    ) -> TransferResult:
        """
        Executa transferência com conversa Realtime com o humano.
        
        Diferente do anúncio TTS, aqui o agente IA conversa por voz com
        o humano, permitindo respostas naturais como:
        - "Pode passar"
        - "Estou ocupado, pede pra ligar daqui 5 minutos"
        - "Do que se trata?"
        
        Fluxo:
        1. MOH no cliente (A-leg)
        2. Originate B-leg para humano
        3. Criar sessão OpenAI Realtime para B-leg
        4. Agente anuncia e conversa com humano
        5. Interpretar resposta (aceitar/recusar/instruções)
        6. Se aceitar: bridge A↔B
        7. Se recusar: retornar ao cliente com mensagem
        
        Args:
            destination: Destino da transferência
            announcement: Texto inicial do anúncio
            caller_context: Contexto do cliente (nome, motivo, resumo)
            realtime_prompt: Prompt para o agente ao falar com humano
            ring_timeout: Timeout de ring em segundos
            conversation_timeout: Tempo máximo de conversa com humano
        
        Returns:
            TransferResult com status e possível mensagem do humano
        """
        start_time = datetime.utcnow()
        
        logger.info(
            f"Starting REALTIME announced transfer",
            extra={
                "call_uuid": self.call_uuid,
                "destination": destination.name,
                "destination_number": destination.destination_number,
            }
        )
        
        try:
            # 1. Garantir conexão ESL
            if not self._esl.is_connected:
                connected = await self._esl.connect()
                if not connected:
                    logger.error("Failed to connect to ESL for realtime transfer")
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Falha na conexão ESL",
                    )
            
            # 2. Verificar se A-leg ainda existe
            a_leg_exists = await self._esl.uuid_exists(self.call_uuid)
            if not a_leg_exists and self._caller_hungup:
                return TransferResult(
                    status=TransferStatus.CANCELLED,
                    destination=destination,
                    error="Cliente desligou",
                )
            
            # 3. Tocar música de espera no cliente
            await self._start_moh()
            
            # 4. Originar B-leg
            dial_string = self._build_dial_string(destination)
            
            logger.info(f"Originating B-leg for realtime transfer: {dial_string}")
            
            originate_result = await self._esl.originate(
                dial_string=dial_string,
                app="&park()",
                timeout=ring_timeout,
                variables={
                    "ignore_early_media": "true",
                    "hangup_after_bridge": "true",
                    "origination_caller_id_number": self.caller_id,
                    "origination_caller_id_name": "Secretaria_Virtual"
                }
            )
            
            if not originate_result.success:
                await self._stop_moh()
                status = self._hangup_cause_to_status(originate_result.hangup_cause)
                
                logger.info(
                    f"Realtime transfer originate failed",
                    extra={
                        "destination": destination.name,
                        "hangup_cause": originate_result.hangup_cause,
                        "status": status.value,
                    }
                )
                
                return TransferResult(
                    status=status,
                    destination=destination,
                    hangup_cause=originate_result.hangup_cause,
                    error=originate_result.error_message,
                )
            
            if not originate_result.uuid:
                await self._stop_moh()
                return TransferResult(
                    status=TransferStatus.FAILED,
                    destination=destination,
                    error="Originate retornou sucesso sem UUID",
                )
            
            b_leg_uuid = originate_result.uuid
            self._b_leg_uuid = b_leg_uuid
            
            logger.info(f"B-leg answered, starting realtime conversation: {b_leg_uuid}")
            
            # 5. Iniciar sessão Realtime para o B-leg
            # Import aqui para evitar circular imports
            from .realtime_announcement import RealtimeAnnouncementSession
            
            # Construir prompt para o agente
            system_prompt = realtime_prompt or self._build_realtime_announcement_prompt(
                destination.name,
                caller_context
            )
            
            # Criar sessão Realtime para o B-leg
            realtime_session = RealtimeAnnouncementSession(
                b_leg_uuid=b_leg_uuid,
                esl_client=self._esl,
                system_prompt=system_prompt,
                initial_message=announcement,
            )
            
            # 6. Executar conversa e aguardar resultado
            conversation_result = await realtime_session.run(
                timeout=conversation_timeout
            )
            
            # 7. Processar resultado
            if conversation_result.accepted:
                # Humano aceitou - fazer bridge
                logger.info(f"Realtime transfer: human ACCEPTED")
                
                # NÃO resumir stream - cliente vai para bridge com humano
                await self._stop_moh(resume_stream=False)
                
                # Definir hangup_after_bridge no A-leg
                await self._esl.execute_api(
                    f"uuid_setvar {self.call_uuid} hangup_after_bridge true"
                )
                
                # Criar bridge
                bridge_success = await self._esl.uuid_bridge(
                    self.call_uuid,
                    b_leg_uuid
                )
                
                if bridge_success:
                    duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    
                    result = TransferResult(
                        status=TransferStatus.SUCCESS,
                        destination=destination,
                        b_leg_uuid=b_leg_uuid,
                        duration_ms=duration,
                    )
                    
                    if self._on_transfer_complete:
                        await self._on_transfer_complete(result)
                    
                    return result
                else:
                    await self._esl.uuid_kill(b_leg_uuid)
                    await self._stop_moh()
                    
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Falha ao criar bridge",
                    )
            
            elif conversation_result.rejected:
                # Humano recusou
                logger.info(
                    f"Realtime transfer: human REJECTED",
                    extra={"message": conversation_result.message}
                )
                
                await self._esl.uuid_kill(b_leg_uuid)
                await self._stop_moh()
                
                return TransferResult(
                    status=TransferStatus.REJECTED,
                    destination=destination,
                    error=conversation_result.message or f"Recusado por {destination.name}",
                )
            
            else:
                # Timeout ou erro
                logger.info(f"Realtime transfer: conversation ended without decision")
                
                await self._esl.uuid_kill(b_leg_uuid)
                await self._stop_moh()
                
                return TransferResult(
                    status=TransferStatus.NO_ANSWER,
                    destination=destination,
                    error="Humano não respondeu a tempo",
                )
        
        except asyncio.CancelledError:
            if self._b_leg_uuid:
                await self._esl.uuid_kill(self._b_leg_uuid)
            await self._stop_moh()
            raise
        
        except Exception as e:
            logger.exception(f"Realtime announced transfer error: {e}")
            await self._stop_moh()
            
            return TransferResult(
                status=TransferStatus.FAILED,
                destination=destination,
                error=str(e),
            )
    
    def _build_realtime_announcement_prompt(
        self,
        destination_name: str,
        caller_context: str
    ) -> str:
        """
        Constrói prompt de sistema para conversa com humano.
        
        Args:
            destination_name: Nome do destino (ex: "João - Vendas")
            caller_context: Contexto do cliente
        
        Returns:
            Prompt de sistema
        """
        return f"""Você é uma assistente virtual anunciando uma ligação para {destination_name}.

CONTEXTO DO CLIENTE:
{caller_context}

INSTRUÇÕES:
1. Anuncie brevemente quem está ligando e o motivo
2. Pergunte se pode transferir a ligação
3. Se o humano aceitar (ex: "pode passar", "ok", "sim"), responda "ACEITO" e encerre
4. Se o humano recusar (ex: "não posso", "estou ocupado"), pergunte se quer deixar recado
5. Se o humano der instruções (ex: "diz que ligo em 5 min"), anote e responda "RECUSADO: [instruções]"
6. Seja breve e objetivo - o cliente está aguardando em espera

IMPORTANTE:
- Sua última mensagem DEVE conter "ACEITO" ou "RECUSADO: [motivo]"
- Não prolongue a conversa desnecessariamente
"""
    
    async def _monitor_transfer_leg(
        self,
        b_leg_uuid: str,
        destination: TransferDestination,
        timeout: float
    ) -> TransferResult:
        """
        Monitora eventos do B-leg até conclusão.
        
        Args:
            b_leg_uuid: UUID do B-leg
            destination: Destino da transferência
            timeout: Timeout máximo
        
        Returns:
            TransferResult com status
        """
        start_time = asyncio.get_event_loop().time()
        answered = False
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            # Verificar se chamador desligou (flag setado pelo event relay)
            # IMPORTANTE: NÃO usar uuid_exists aqui pois pode retornar false
            # incorretamente após o originate (bug de conexão ESL inbound)
            if self._caller_hungup:
                logger.info("A-leg hangup detected during transfer (via event relay)")
                
                # Matar B-leg
                await self._esl.uuid_kill(b_leg_uuid)
                
                return TransferResult(
                    status=TransferStatus.CANCELLED,
                    destination=destination,
                    error="Cliente desligou durante a transferência"
                )
            
            # Aguardar próximo evento
            event = await self._esl.wait_for_event(
                event_names=["CHANNEL_ANSWER", "CHANNEL_HANGUP"],
                uuid=b_leg_uuid,
                timeout=2.0
            )
            
            if not event:
                continue
            
            if event.name == "CHANNEL_ANSWER":
                # Destino atendeu
                logger.info(f"B-leg answered: {b_leg_uuid}")
                answered = True
                
                return TransferResult(
                    status=TransferStatus.ANSWERED,
                    destination=destination,
                    b_leg_uuid=b_leg_uuid
                )
            
            elif event.name == "CHANNEL_HANGUP":
                # B-leg desligou - verificar causa
                hangup_cause = event.hangup_cause or "UNKNOWN"
                
                logger.info(
                    f"B-leg hangup: {hangup_cause}",
                    extra={
                        "b_leg_uuid": b_leg_uuid,
                        "destination": destination.name
                    }
                )
                
                status = HANGUP_CAUSE_MAP.get(hangup_cause, TransferStatus.FAILED)
                
                return TransferResult(
                    status=status,
                    destination=destination,
                    hangup_cause=hangup_cause,
                    b_leg_uuid=b_leg_uuid
                )
        
        # Timeout
        logger.info(f"Transfer timeout: {b_leg_uuid}")
        
        # Matar B-leg
        await self._esl.uuid_kill(b_leg_uuid)
        
        return TransferResult(
            status=TransferStatus.NO_ANSWER,
            destination=destination,
            hangup_cause="ALLOTTED_TIMEOUT",
            b_leg_uuid=b_leg_uuid
        )
    
    async def _start_moh(self) -> None:
        """
        Inicia música de espera no A-leg.
        
        IMPORTANTE: Pausa o audio_stream ANTES de iniciar MOH para evitar
        que o MOH seja capturado e enviado para o provider AI, causando
        respostas "picotadas" ou loops de feedback onde o agente responde
        a si mesmo.
        """
        if not self._moh_active:
            # 1. CRÍTICO: Pausar audio_stream ANTES de iniciar MOH
            # Isso evita que o MOH seja capturado e enviado para OpenAI
            self._stream_paused = False
            try:
                await self._esl.uuid_audio_stream(self.call_uuid, "pause")
                self._stream_paused = True
                logger.info(f"Audio stream paused before MOH for {self.call_uuid}")
            except Exception as e:
                logger.warning(f"Failed to pause audio stream before MOH: {e}")
            
            # 2. Interromper qualquer áudio em reprodução (uuid_break)
            # Isso para o playback do agente que pode estar em andamento
            try:
                await self._esl.uuid_break(self.call_uuid)
                logger.debug(f"Cleared playback before MOH for {self.call_uuid}")
            except Exception as e:
                logger.warning(f"Failed to clear playback before MOH: {e}")
            
            # 3. Delay para serialização de comandos ESL
            # Estamos simulando atendente humano - não precisa ser rápido
            # Delay de 0.5s é imperceptível para o usuário e garante estabilidade
            await asyncio.sleep(0.5)
            
            # 4. Iniciar MOH
            success = await self._esl.uuid_broadcast(
                self.call_uuid,
                self._transfer_music_on_hold,
                leg="aleg"
            )
            if success:
                self._moh_active = True
                logger.debug(f"MOH started for {self.call_uuid}")
            else:
                # MOH falhou - resumir stream imediatamente!
                logger.warning(f"MOH failed to start for {self.call_uuid}, resuming audio stream")
                if self._stream_paused:
                    try:
                        await self._esl.uuid_audio_stream(self.call_uuid, "resume")
                        self._stream_paused = False
                        logger.info(f"Audio stream resumed after MOH failure for {self.call_uuid}")
                    except Exception as e:
                        logger.error(f"Failed to resume audio stream after MOH failure: {e}")
    
    async def _stop_moh(self, resume_stream: bool = True) -> None:
        """
        Para música de espera e gerencia o audio_stream.
        
        Robusto: sempre executa o gerenciamento do stream, mesmo se MOH não iniciou.
        Isso garante que o cliente não fique preso em espera/silêncio.
        
        Args:
            resume_stream: Se True (default), resume o audio_stream após parar MOH.
                          Usar False quando a transferência foi bem-sucedida e o
                          cliente está em bridge com humano.
        
        IMPORTANTE: Resume o audio_stream SEMPRE que _stream_paused=True e
        resume_stream=True, mesmo que MOH não tenha iniciado corretamente.
        """
        # 1. Parar MOH se estava ativo
        if self._moh_active:
            try:
                await self._esl.uuid_break(self.call_uuid)
                logger.debug(f"MOH stopped for {self.call_uuid}")
            except Exception as e:
                logger.warning(f"Failed to stop MOH (uuid_break): {e}")
                # Tentar reconectar e parar novamente
                try:
                    if not self._esl.is_connected:
                        await self._esl.connect()
                    await self._esl.uuid_break(self.call_uuid)
                    logger.debug(f"MOH stopped for {self.call_uuid} (after reconnect)")
                except Exception as e2:
                    logger.error(f"Failed to stop MOH even after reconnect: {e2}")
            finally:
                self._moh_active = False
        
        # 2. Delay para garantir que FreeSWITCH processou
        # Atendente humano não precisa de latência ultra-baixa
        await asyncio.sleep(0.3)
        
        # 3. CRÍTICO: Gerenciar audio_stream baseado em _stream_paused
        # Isso garante que o stream é retomado mesmo se MOH falhou ao iniciar
        stream_paused = getattr(self, '_stream_paused', False)
        
        if resume_stream:
            # Transferência FALHOU - precisamos retomar o bot
            if stream_paused:
                try:
                    await self._esl.uuid_audio_stream(self.call_uuid, "resume")
                    self._stream_paused = False
                    logger.info(f"Audio stream resumed after failed transfer for {self.call_uuid}")
                except Exception as e:
                    logger.warning(f"Failed to resume audio stream: {e}")
        else:
            # Transferência bem-sucedida - cliente em bridge, parar stream
            try:
                await self._esl.uuid_audio_stream(self.call_uuid, "stop")
                self._stream_paused = False
                logger.info(f"Audio stream stopped (transfer success) for {self.call_uuid}")
            except Exception as e:
                logger.debug(f"Failed to stop audio stream (may be normal): {e}")
    
    async def stop_moh_and_resume(self) -> None:
        """Para música e sinaliza para retomar Voice AI."""
        await self._stop_moh()
        
        if self._on_resume:
            result = self._on_resume()
            if asyncio.iscoroutine(result):
                await result
    
    def _build_dial_string(self, dest: TransferDestination) -> str:
        """
        Constrói dial string para o destino.
        
        IMPORTANTE: Usar user/ para extensões internas!
        O FreeSWITCH resolve user/ext@domain para o IP real do softphone.
        
        Args:
            dest: Destino da transferência
        
        Returns:
            Dial string para originate
        """
        number = dest.destination_number
        context = dest.destination_context
        
        if dest.destination_type == "extension":
            # user/ resolve para o IP real do softphone via directory lookup
            # Exemplo: user/1001@domain → sofia/internal/1001@177.72.9.170:57203
            return f"user/{number}@{context}"
        
        elif dest.destination_type == "ring_group":
            # Ring groups usam group/
            return f"group/{number}@{context}"
        
        elif dest.destination_type == "queue":
            return f"fifo/{number}@{context}"
        
        elif dest.destination_type == "voicemail":
            return f"voicemail/{number}@{context}"
        
        elif dest.destination_type == "external":
            # Número externo - usar gateway padrão
            gateway = os.getenv("DEFAULT_GATEWAY", "default")
            return f"sofia/gateway/{gateway}/{number}"
        
        else:
            # Default: tratar como extensão via user/
            return f"user/{number}@{context}"
    
    def _hangup_cause_to_status(self, hangup_cause: Optional[str]) -> TransferStatus:
        """
        Converte hangup cause do FreeSWITCH para TransferStatus.
        
        Args:
            hangup_cause: Hangup cause do FreeSWITCH (ex: "USER_NOT_REGISTERED")
        
        Returns:
            TransferStatus correspondente
        """
        if not hangup_cause:
            return TransferStatus.FAILED
        
        # Usar mapeamento global
        status = HANGUP_CAUSE_MAP.get(hangup_cause.upper())
        if status:
            return status
        
        # Fallback para causas não mapeadas
        cause_upper = hangup_cause.upper()
        
        # Padrões de offline
        if any(x in cause_upper for x in ("NOT_REGISTERED", "ABSENT", "UNALLOCATED", "NO_ROUTE")):
            return TransferStatus.OFFLINE
        
        # Padrões de busy
        if any(x in cause_upper for x in ("BUSY", "CONGESTION")):
            return TransferStatus.BUSY
        
        # Padrões de no_answer
        if any(x in cause_upper for x in ("NO_ANSWER", "NO_USER", "TIMEOUT", "CANCEL")):
            return TransferStatus.NO_ANSWER
        
        # Padrões de rejected
        if any(x in cause_upper for x in ("REJECTED", "CHALLENGE", "BARRED")):
            return TransferStatus.REJECTED
        
        # Default
        return TransferStatus.FAILED
    
    async def cancel_transfer(self) -> bool:
        """
        Cancela transferência em andamento.
        
        Returns:
            True se cancelou com sucesso
        """
        if self._b_leg_uuid:
            await self._esl.uuid_kill(self._b_leg_uuid)
            self._b_leg_uuid = None
        
        await self._stop_moh()
        
        logger.info(f"Transfer cancelled for {self.call_uuid}")
        return True
    
    async def handle_caller_hangup(self) -> None:
        """
        Handler para quando cliente desliga durante transferência.
        
        Deve ser chamado quando detectar hangup do A-leg.
        """
        self._caller_hungup = True
        
        if self._b_leg_uuid:
            # Matar B-leg pendente
            await self._esl.uuid_kill(self._b_leg_uuid, "ORIGINATOR_CANCEL")
            self._b_leg_uuid = None
        
        logger.info(
            f"Caller hangup during transfer",
            extra={"call_uuid": self.call_uuid}
        )
    
    async def close(self) -> None:
        """Limpa recursos."""
        await self.cancel_transfer()


# Factory function para criar TransferManager
async def create_transfer_manager(
    domain_uuid: str,
    call_uuid: str,
    caller_id: str,
    secretary_uuid: Optional[str] = None,
    on_resume: Optional[Callable[[], Any]] = None,
    on_transfer_complete: Optional[Callable[[TransferResult], Any]] = None,
    domain_settings: Optional[Dict[str, Any]] = None,
    voice_id: Optional[str] = None,
    announcement_tts_provider: Optional[str] = None,
) -> TransferManager:
    """
    Cria e inicializa TransferManager.
    
    Esta função garante que ESL e loader estão conectados.
    Carrega domain_settings do banco de dados se não fornecido.
    
    Args:
        domain_uuid: UUID do tenant
        call_uuid: UUID da chamada
        caller_id: Número do chamador
        secretary_uuid: UUID da secretária (opcional)
        on_resume: Callback quando retomar Voice AI
        on_transfer_complete: Callback quando transferência completar
        domain_settings: Configurações do domínio (opcional, carrega do banco se None)
        voice_id: ID da voz ElevenLabs para anúncios de transferência
        announcement_tts_provider: Provider TTS para anúncios ("elevenlabs" ou "openai")
    """
    # Carregar configurações do banco se não fornecidas
    if domain_settings is None:
        try:
            from services.database import db
            from uuid import UUID
            domain_settings = await db.get_domain_settings(UUID(domain_uuid))
        except Exception as e:
            logger.warning(f"Failed to load domain settings: {e}")
            domain_settings = {}
    
    manager = TransferManager(
        domain_uuid=domain_uuid,
        call_uuid=call_uuid,
        caller_id=caller_id,
        secretary_uuid=secretary_uuid,
        on_resume=on_resume,
        on_transfer_complete=on_transfer_complete,
        domain_settings=domain_settings,
        voice_id=voice_id,
        announcement_tts_provider=announcement_tts_provider,
    )
    
    # Pré-carregar destinos
    await manager.load_destinations()
    
    return manager
