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
2. Cliente fica em silêncio (MOH removido por problemas de sincronização)
3. originate para criar B-leg (chamada para destino)
4. Monitorar eventos CHANNEL_ANSWER / CHANNEL_HANGUP no B-leg
5. Se atendeu: uuid_audio_stream STOP + uuid_bridge (cliente fala com humano)
6. Se não atendeu: uuid_audio_stream RESUME + retomar Voice AI

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
import random
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .esl_client import AsyncESLClient, ESLEvent, OriginateResult, get_esl_client, get_esl_for_domain
from .transfer_destination_loader import (
    TransferDestination,
    TransferDestinationLoader,
    get_destination_loader
)

logger = logging.getLogger(__name__)

# =========================================================================
# MENSAGENS DE TRANSIÇÃO NATURAIS
# Tornam as transferências mais humanas e informativas
# Ref: docs/PROJECT_EVOLUTION.md - Melhorias Conversacionais
# =========================================================================

# Mensagens quando vai transferir com sucesso
# NOTA: {destination} pode ser nome de pessoa ("João") ou departamento ("Vendas")
TRANSFER_ANNOUNCEMENTS = [
    "Perfeito! Vou te conectar com {destination}. Só um momento...",
    "Certo! Te transfiro agora para {destination}...",
    "Combinado! Te passo para {destination}. Um instante...",
    "Ok! Vou te conectar com {destination}...",
]

# Mensagens quando destino está offline/indisponível
# Funciona para pessoas ("João não está disponível") e departamentos ("Vendas não está disponível")
OFFLINE_MESSAGES = [
    "Infelizmente {destination} não está disponível no momento. "
    "Posso anotar um recado para retornarem assim que possível?",
    "{destination} não está online agora. "
    "Quer deixar uma mensagem para nossa equipe entrar em contato?",
]

# Mensagens quando destino não atende
NO_ANSWER_MESSAGES = [
    "{destination} não conseguiu atender agora. "
    "Quer deixar um recado para te retornarem?",
    "Liguei para {destination} mas não houve resposta. "
    "Posso anotar uma mensagem?",
]

# Mensagens quando destino está ocupado
BUSY_MESSAGES = [
    "{destination} está em outra ligação no momento. "
    "Quer aguardar ou prefere que eu anote um recado?",
    "{destination} está ocupado agora. "
    "Posso tentar novamente ou deixar uma mensagem?",
]

# Mensagens quando destino rejeitou a chamada ativamente
REJECTED_MESSAGES = [
    "{destination} não pôde atender no momento. "
    "Quer deixar um recado para retornarem?",
    "A chamada não foi atendida por {destination}. "
    "Posso anotar uma mensagem?",
]


def get_transfer_announcement(destination: str) -> str:
    """
    Retorna anúncio aleatório para transferência.
    
    Args:
        destination: Nome do destino (pessoa ou departamento)
    """
    msg = random.choice(TRANSFER_ANNOUNCEMENTS)
    return msg.format(destination=destination)


def get_offline_message(destination: str) -> str:
    """
    Retorna mensagem aleatória para destino offline.
    
    Args:
        destination: Nome do destino (pessoa ou departamento)
    """
    msg = random.choice(OFFLINE_MESSAGES)
    return msg.format(destination=destination)


def get_no_answer_message(destination: str) -> str:
    """
    Retorna mensagem aleatória para destino que não atendeu.
    
    Args:
        destination: Nome do destino (pessoa ou departamento)
    """
    msg = random.choice(NO_ANSWER_MESSAGES)
    return msg.format(destination=destination)


def get_busy_message(destination: str) -> str:
    """
    Retorna mensagem aleatória para destino ocupado.
    
    Args:
        destination: Nome do destino (pessoa ou departamento)
    """
    msg = random.choice(BUSY_MESSAGES)
    return msg.format(destination=destination)


def get_rejected_message(destination: str) -> str:
    """
    Retorna mensagem aleatória para chamada rejeitada.
    
    Usado quando o atendente clica em "reject" no softphone.
    
    Args:
        destination: Nome do destino (pessoa ou departamento)
    """
    msg = random.choice(REJECTED_MESSAGES)
    return msg.format(destination=destination)

# Configurações padrão (usadas se não houver config do banco)
DEFAULT_TRANSFER_TIMEOUT = int(os.getenv("TRANSFER_DEFAULT_TIMEOUT", "30"))
DEFAULT_TRANSFER_ANNOUNCE_ENABLED = os.getenv("TRANSFER_ANNOUNCE_ENABLED", "true").lower() == "true"
# NOTA: MOH removido - cliente fica em silêncio durante transferência


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
        # NOTA: MOH removido - cliente fica em silêncio durante transferência
        
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
        logger.info(
            f"🔍 [FIND_DESTINATION] Buscando destino para: '{user_text}'",
            extra={"call_uuid": self.call_uuid, "domain_uuid": self.domain_uuid}
        )
        
        destinations = await self.load_destinations()
        
        if not destinations:
            logger.warning(
                f"🔍 [FIND_DESTINATION] ERRO: Nenhum destino configurado para este tenant!",
                extra={"call_uuid": self.call_uuid, "domain_uuid": self.domain_uuid}
            )
            return (None, "Não há destinos de transferência configurados.")
        
        # Log dos destinos disponíveis para este tenant
        available_names = [d.name for d in destinations]
        logger.info(
            f"🔍 [FIND_DESTINATION] Destinos disponíveis para este tenant: {available_names}",
            extra={
                "call_uuid": self.call_uuid,
                "domain_uuid": self.domain_uuid,
                "destinations_count": len(destinations),
            }
        )
        
        # Verificar se é pedido genérico
        generic_keywords = ["qualquer", "alguém", "atendente", "disponível", "pessoa"]
        text_lower = user_text.lower()
        
        if any(kw in text_lower for kw in generic_keywords):
            logger.info(
                f"🔍 [FIND_DESTINATION] Pedido genérico detectado: '{user_text}'",
                extra={"call_uuid": self.call_uuid}
            )
            # Retornar destino padrão (fila ou ring_group)
            dest = self._loader.get_default(destinations)
            if dest:
                logger.info(
                    f"🔍 [FIND_DESTINATION] Usando destino padrão: {dest.name}",
                    extra={"call_uuid": self.call_uuid}
                )
                # Verificar horário
                available, msg = self._loader.is_within_working_hours(dest)
                if not available:
                    logger.warning(
                        f"🔍 [FIND_DESTINATION] Destino padrão fora do horário: {msg}",
                        extra={"call_uuid": self.call_uuid}
                    )
                    return (None, msg)
                return (dest, None)
            logger.warning(
                "🔍 [FIND_DESTINATION] Nenhum destino padrão configurado",
                extra={"call_uuid": self.call_uuid}
            )
            return (None, "Não há atendentes disponíveis no momento.")
        
        # Buscar destino específico
        dest = self._loader.find_by_alias(user_text, destinations, min_score=0.5)
        
        if not dest:
            # Sugerir destinos disponíveis
            suggestion = ", ".join(available_names[:5])
            logger.warning(
                f"🔍 [FIND_DESTINATION] Destino '{user_text}' NÃO ENCONTRADO. "
                f"Destinos disponíveis: {available_names}",
                extra={"call_uuid": self.call_uuid, "domain_uuid": self.domain_uuid}
            )
            return (
                None,
                f"Não encontrei '{user_text}'. Você pode falar com: {suggestion}."
            )
        
        logger.info(
            f"🔍 [FIND_DESTINATION] ✅ Destino encontrado: '{dest.name}' "
            f"(número={dest.destination_number}, tipo={dest.destination_type})",
            extra={"call_uuid": self.call_uuid}
        )
        
        # Verificar horário comercial
        available, msg = self._loader.is_within_working_hours(dest)
        if not available:
            logger.warning(
                f"🔍 [FIND_DESTINATION] Destino '{dest.name}' FORA DO HORÁRIO: {msg}",
                extra={"call_uuid": self.call_uuid}
            )
            return (None, msg)
        
        logger.info(
            f"🔍 [FIND_DESTINATION] ✅ Destino validado e disponível: {dest.name}",
            extra={"call_uuid": self.call_uuid}
        )
        
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
                
                # 1.5 VERIFICAÇÃO PRÉVIA DE PRESENÇA (para extensões)
                # Evita esperar timeout de originate quando ramal está offline
                if destination.destination_type == "extension":
                    is_registered, contact, check_successful = await self._esl.check_extension_registered(
                        destination.destination_number,
                        destination.destination_context
                    )
                    
                    # Só retornar OFFLINE se a verificação foi bem-sucedida E o ramal não está registrado
                    # Se check_successful = False (timeout/erro), tentar originate mesmo assim
                    if check_successful and not is_registered:
                        logger.info(
                            f"Extension {destination.destination_number} is NOT registered - skipping originate",
                            extra={
                                "destination": destination.name,
                                "extension": destination.destination_number,
                                "domain": destination.destination_context,
                            }
                        )
                        
                        return TransferResult(
                            status=TransferStatus.OFFLINE,
                            destination=destination,
                            hangup_cause="USER_NOT_REGISTERED",
                            error=f"Ramal {destination.destination_number} não está conectado",
                            retries=retries
                        )
                    elif not check_successful:
                        logger.info(
                            f"Extension {destination.destination_number} check failed - proceeding with originate",
                            extra={
                                "destination": destination.name,
                                "extension": destination.destination_number,
                            }
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
                
                # 7. Tirar do silêncio antes do bridge (NÃO resumir stream - cliente vai p/ bridge)
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
                    # Bridge falhou - tirar da espera e matar B-leg
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
        1. Silêncio no A-leg (cliente fica em espera)
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
            
            # 1.5 VERIFICAÇÃO PRÉVIA DE PRESENÇA (para extensões)
            # Evita esperar timeout de originate quando ramal está offline
            if destination.destination_type == "extension":
                is_registered, contact, check_successful = await self._esl.check_extension_registered(
                    destination.destination_number,
                    destination.destination_context
                )
                
                # Só retornar OFFLINE se a verificação foi bem-sucedida E o ramal não está registrado
                if check_successful and not is_registered:
                    logger.info(
                        f"Extension {destination.destination_number} is NOT registered - skipping announced transfer",
                        extra={
                            "destination": destination.name,
                            "extension": destination.destination_number,
                            "domain": destination.destination_context,
                        }
                    )
                    
                    return TransferResult(
                        status=TransferStatus.OFFLINE,
                        destination=destination,
                        hangup_cause="USER_NOT_REGISTERED",
                        error=f"Ramal {destination.destination_number} não está conectado",
                    )
                elif not check_successful:
                    logger.info(f"Extension {destination.destination_number} check failed - proceeding with announced transfer")
            
            # 2. Verificar se A-leg ainda existe
            a_leg_exists = await self._esl.uuid_exists(self.call_uuid)
            if not a_leg_exists and self._caller_hungup:
                return TransferResult(
                    status=TransferStatus.CANCELLED,
                    destination=destination,
                    error="Cliente desligou",
                )
            
            # 3. Colocar cliente em espera (modo silêncio)
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
            
            # 6. Cliente permanece em silêncio enquanto falamos com o humano
            # (humano já atendeu - originate síncrono retornou +OK)
            
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
    # ANNOUNCED TRANSFER REALTIME: Transferência com anúncio via conferência
    # Usa mod_conference nativo do FreeSWITCH - robusto e simples
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
        Executa transferência com anúncio usando conferência nativa do FreeSWITCH.
        
        Esta é a implementação ROBUSTA que usa mod_conference para:
        - Manter A-leg (cliente) em espera (modo silêncio)
        - Originar B-leg (atendente) para conferência
        - Tocar anúncio TTS para o atendente
        - Aguardar confirmação via DTMF ou timeout
        - Conectar ambos se aceito
        
        Fluxo:
        1. Criar conferência temporária única
        2. Mover A-leg para conferência (mutado, em silêncio)
        3. Originar B-leg para conferência
        4. Tocar anúncio TTS para B-leg
        5. Aguardar DTMF 1 (aceita) ou 2 (recusa) ou timeout
        6. Se aceitar: desmuta A-leg, ambos conversam
        7. Se recusar: kicka B-leg, retorna A-leg ao Voice AI
        
        Args:
            destination: Destino da transferência
            announcement: Texto do anúncio para o atendente
            caller_context: Contexto do cliente (não usado nesta versão)
            realtime_prompt: Não usado (mantido para compatibilidade)
            ring_timeout: Timeout de ring em segundos
            conversation_timeout: Tempo para aguardar resposta do atendente
        
        Returns:
            TransferResult com status da transferência
        """
        start_time = datetime.utcnow()
        b_leg_uuid: Optional[str] = None
        
        logger.info(
            f"Starting announced transfer with TTS",
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
                    logger.error("Failed to connect to ESL for conference transfer")
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Falha na conexão ESL",
                    )
            
            # 2. Verificação prévia de presença (para extensões)
            if destination.destination_type == "extension":
                is_registered, contact, check_successful = await self._esl.check_extension_registered(
                    destination.destination_number,
                    destination.destination_context
                )
                
                if check_successful and not is_registered:
                    logger.info(
                        f"Extension {destination.destination_number} is NOT registered",
                        extra={"destination": destination.name}
                    )
                    return TransferResult(
                        status=TransferStatus.OFFLINE,
                        destination=destination,
                        hangup_cause="USER_NOT_REGISTERED",
                        error=f"Ramal {destination.destination_number} não está conectado",
                    )
            
            # 3. Verificar se A-leg ainda existe (timeout curto)
            try:
                a_leg_exists = await asyncio.wait_for(
                    self._esl.uuid_exists(self.call_uuid),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                logger.warning("uuid_exists timeout checking A-leg, assuming exists")
                a_leg_exists = True
            
            if not a_leg_exists:
                return TransferResult(
                    status=TransferStatus.CANCELLED,
                    destination=destination,
                    error="Cliente desligou",
                )
            
            # 4. Parar stream de áudio do Voice AI (A-leg vai para conferência)
            try:
                await asyncio.wait_for(
                    self._esl.execute_api(f"uuid_audio_stream {self.call_uuid} stop"),
                    timeout=3.0
                )
            except (asyncio.TimeoutError, Exception):
                pass  # Ignorar se não estava ativo ou timeout
            
            # 5. Cliente fica em silêncio durante a transferência
            # NOTA: MOH removido - audio_stream já foi pausado
            logger.info(f"Putting A-leg on hold (silent): {self.call_uuid}")
            
            # 6. Originar B-leg para o atendente
            dial_string = self._build_dial_string(destination)
            
            logger.info(f"Originating B-leg to attendant: {dial_string}")
            
            # B-leg vai para park() - receberá o anúncio e depois fazemos bridge
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
                # Falha no originate - retornar A-leg ao Voice AI
                logger.info(f"Originate failed: {originate_result.hangup_cause}")
                await self._cleanup_transfer(self.call_uuid, None)
                
                return TransferResult(
                    status=self._hangup_cause_to_status(originate_result.hangup_cause),
                    destination=destination,
                    hangup_cause=originate_result.hangup_cause,
                    error=originate_result.error_message,
                )
            
            b_leg_uuid = originate_result.uuid
            self._b_leg_uuid = b_leg_uuid
            
            logger.info(f"B-leg answered: {b_leg_uuid}")
            
            # 7. Tocar anúncio TTS para o atendente (B-leg)
            announcement_played = await self._play_announcement_to_bleg(
                b_leg_uuid,
                announcement,
                destination.name
            )
            
            if not announcement_played:
                logger.warning("Failed to play announcement, proceeding anyway")
            
            # 8. Aguardar resposta do atendente
            # DTMF 1 = aceita, DTMF 2 = recusa, timeout = aceita
            logger.info(f"Waiting for attendant response (timeout: {conversation_timeout}s)")
            
            response = await self._wait_for_attendant_response(
                b_leg_uuid,
                timeout=conversation_timeout
            )
            
            # 9. Processar resposta
            if response == "accept":
                # Atendente aceitou - fazer bridge A-leg <-> B-leg
                logger.info("Transfer ACCEPTED by attendant")
                
                # NOTA: Cliente já estava em silêncio (sem MOH), não precisa de uuid_break
                
                # Definir hangup_after_bridge
                await self._esl.execute_api(f"uuid_setvar {self.call_uuid} hangup_after_bridge true")
                await self._esl.execute_api(f"uuid_setvar {b_leg_uuid} hangup_after_bridge true")
                
                # Fazer bridge entre A-leg e B-leg
                bridge_success = await self._esl.uuid_bridge(self.call_uuid, b_leg_uuid)
                
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
                    # Bridge falhou - cleanup
                    logger.error("Bridge failed")
                    await self._cleanup_transfer(self.call_uuid, b_leg_uuid)
                    
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Falha ao conectar ligação",
                    )
            
            elif response == "reject":
                # Atendente recusou
                logger.info("Transfer REJECTED by attendant")
                
                await self._cleanup_transfer(self.call_uuid, b_leg_uuid)
                
                return TransferResult(
                    status=TransferStatus.REJECTED,
                    destination=destination,
                    error=f"Transferência recusada por {destination.name}",
                )
            
            else:  # "hangup"
                # Atendente desligou
                logger.info(f"Transfer failed: attendant {response}")
                
                await self._cleanup_transfer(self.call_uuid, b_leg_uuid)
                
                return TransferResult(
                    status=TransferStatus.NO_ANSWER,
                    destination=destination,
                    error="Atendente desligou",
                )
        
        except asyncio.CancelledError:
            await self._cleanup_transfer(self.call_uuid, b_leg_uuid)
            raise
        
        except Exception as e:
            logger.exception(f"Announced transfer error: {e}")
            await self._cleanup_transfer(self.call_uuid, b_leg_uuid)
            
            return TransferResult(
                status=TransferStatus.FAILED,
                destination=destination,
                error=str(e),
            )
    
    async def _play_announcement_to_bleg(
        self,
        b_leg_uuid: str,
        announcement: str,
        destination_name: str
    ) -> bool:
        """
        Toca anúncio TTS para o atendente (B-leg).
        
        Fluxo:
        1. Tenta ElevenLabs/OpenAI TTS (voz natural)
        2. Fallback: mod_flite (voz robótica mas funciona)
        3. Último recurso: beep
        
        Args:
            b_leg_uuid: UUID do B-leg (atendente)
            announcement: Texto do anúncio
            destination_name: Nome do destino (para log)
        
        Returns:
            True se conseguiu tocar, False se falhou
        """
        # Adicionar instruções DTMF ao anúncio
        full_announcement = (
            f"{announcement}. "
            f"Pressione 1 para atender ou 2 para recusar."
        )
        
        # 1. Tentar gerar TTS via ElevenLabs/OpenAI
        try:
            from .announcement_tts import get_announcement_tts
            
            tts = get_announcement_tts()
            audio_file = await tts.generate_announcement(full_announcement)
            
            if audio_file:
                # Verificar se B-leg ainda existe (timeout curto de 3s para não bloquear)
                try:
                    b_exists = await asyncio.wait_for(
                        self._esl.uuid_exists(b_leg_uuid),
                        timeout=3.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("uuid_exists timeout, assuming B-leg exists")
                    b_exists = True  # Assumir que existe e tentar tocar
                
                if not b_exists:
                    logger.warning("B-leg gone before playing announcement")
                    return False
                
                # Tocar arquivo para o atendente (timeout curto)
                try:
                    result = await asyncio.wait_for(
                        self._esl.execute_api(f"uuid_broadcast {b_leg_uuid} {audio_file} aleg"),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("uuid_broadcast timeout")
                    result = ""
                
                if "+OK" in (result or ""):
                    logger.info(
                        f"TTS announcement playing",
                        extra={
                            "destination": destination_name,
                            "audio_file": audio_file,
                            "text_length": len(full_announcement),
                        }
                    )
                    
                    # Aguardar duração estimada do áudio (~2.5 palavras/segundo)
                    duration = len(full_announcement.split()) / 2.5
                    await asyncio.sleep(max(2.0, duration))
                    
                    return True
                else:
                    logger.warning(f"uuid_broadcast failed: {result}")
                    
        except Exception as e:
            logger.warning(f"TTS generation failed: {e}")
        
        # 2. Fallback: mod_flite (voz robótica)
        try:
            # Timeout curto para não bloquear
            try:
                b_exists = await asyncio.wait_for(
                    self._esl.uuid_exists(b_leg_uuid),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                b_exists = True  # Assumir que existe
            
            if not b_exists:
                return False
            
            success = await self._esl.uuid_say(b_leg_uuid, full_announcement)
            if success:
                logger.info(f"mod_flite announcement played for {destination_name}")
                await asyncio.sleep(3.0)
                return True
                
        except Exception as e:
            logger.warning(f"mod_flite fallback failed: {e}")
        
        # 3. Último recurso: beep de notificação
        try:
            await self._esl.execute_api(
                f"uuid_broadcast {b_leg_uuid} tone_stream://%(300,200,440);%(100,100,0);%(300,200,440) aleg"
            )
            logger.info(f"Fallback beep played for {destination_name}")
            await asyncio.sleep(1.0)
            return True
        except Exception:
            pass
        
        return False
    
    async def _wait_for_attendant_response(
        self,
        b_leg_uuid: str,
        timeout: float = 15.0
    ) -> str:
        """
        Aguarda resposta do atendente.
        
        Monitora:
        - DTMF 1 = aceita
        - DTMF 2 = recusa
        - B-leg hangup = rejeitado
        - Timeout = aceita automaticamente (atendente ouviu anúncio e não recusou)
        
        Args:
            b_leg_uuid: UUID do B-leg (atendente)
            timeout: Tempo máximo de espera
        
        Returns:
            "accept", "reject", ou "hangup"
        """
        logger.info(
            f"Waiting for attendant response",
            extra={
                "b_leg_uuid": b_leg_uuid,
                "timeout": timeout,
            }
        )
        
        # Subscrever eventos DTMF para o B-leg
        try:
            await self._esl.subscribe_events(["DTMF", "CHANNEL_HANGUP"], b_leg_uuid)
            logger.debug(f"Subscribed to DTMF events for {b_leg_uuid}")
        except Exception as e:
            logger.warning(f"Failed to subscribe events: {e}")
        
        start = asyncio.get_event_loop().time()
        
        # Limpar fila de DTMFs anterior para este UUID
        if hasattr(self._esl, '_dtmf_queue') and b_leg_uuid in self._esl._dtmf_queue:
            self._esl._dtmf_queue[b_leg_uuid].clear()
        
        while (asyncio.get_event_loop().time() - start) < timeout:
            # 1. Verificar se B-leg ainda existe
            b_leg_exists = await self._esl.uuid_exists(b_leg_uuid)
            if not b_leg_exists:
                logger.info("B-leg hangup detected")
                return "hangup"
            
            # 2. Verificar DTMFs na fila
            if hasattr(self._esl, '_get_dtmf_from_queue'):
                dtmf = self._esl._get_dtmf_from_queue(b_leg_uuid)
                
                if dtmf == "1":
                    logger.info("DTMF 1 received - transfer ACCEPTED")
                    return "accept"
                
                if dtmf == "2":
                    logger.info("DTMF 2 received - transfer REJECTED")
                    return "reject"
            
            # 3. Verificar se A-leg ainda existe (cliente desligou?)
            a_leg_exists = await self._esl.uuid_exists(self.call_uuid)
            if not a_leg_exists:
                logger.info("A-leg hangup detected - cancelling transfer")
                return "hangup"
            
            await asyncio.sleep(0.2)
        
        # Timeout - assumir aceitação
        # (atendente ouviu o anúncio e não desligou = aceita)
        logger.info("Response timeout - assuming acceptance")
        return "accept"
    
    async def _cleanup_transfer(
        self,
        a_leg_uuid: str,
        b_leg_uuid: Optional[str]
    ) -> None:
        """
        Limpa transferência e retorna A-leg ao Voice AI.
        
        Fluxo:
        1. Desliga B-leg (se existir)
        2. Para playback no A-leg
        3. Retorna A-leg ao Voice AI (reinicia stream de áudio)
        
        Args:
            a_leg_uuid: UUID do A-leg (cliente)
            b_leg_uuid: UUID do B-leg (atendente, se existir)
        """
        logger.info("Cleaning up transfer")
        
        try:
            # 1. Desligar B-leg se existir
            if b_leg_uuid:
                try:
                    b_exists = await self._esl.uuid_exists(b_leg_uuid)
                    if b_exists:
                        await self._esl.execute_api(f"uuid_kill {b_leg_uuid}")
                        logger.debug(f"B-leg {b_leg_uuid} killed")
                except Exception as e:
                    logger.debug(f"Failed to kill B-leg: {e}")
            
            # 2. Verificar se A-leg ainda existe
            a_exists = await self._esl.uuid_exists(a_leg_uuid)
            if not a_exists:
                logger.info("A-leg no longer exists during cleanup")
                return
            
            # 3. NOTA: MOH removido - cliente já estava em silêncio
            # Apenas um pequeno delay para estabilização
            await asyncio.sleep(0.2)
            
            # 4. Retornar A-leg ao Voice AI
            if self._on_resume:
                logger.info("Resuming Voice AI session for A-leg")
                try:
                    await self._on_resume()
                except Exception as e:
                    logger.error(f"Failed to resume Voice AI: {e}")
                    # Fallback: park
                    try:
                        await self._esl.execute_api(f"uuid_park {a_leg_uuid}")
                        logger.warning("A-leg parked as fallback")
                    except Exception:
                        pass
            else:
                logger.warning("No resume callback - parking A-leg")
                try:
                    await self._esl.execute_api(f"uuid_park {a_leg_uuid}")
                except Exception as e:
                    logger.error(f"Failed to park A-leg: {e}")
            
            logger.info("Transfer cleanup complete")
            
        except Exception as e:
            logger.error(f"Transfer cleanup error: {e}")
    
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
        Coloca cliente em espera (modo silêncio).
        
        IMPORTANTE: Pausa o audio_stream para evitar que ruídos sejam
        capturados e enviados para o provider AI durante a transferência.
        
        NOTA: MOH (Music on Hold) foi removido por problemas de sincronização.
        Cliente fica em silêncio durante a transferência.
        """
        if not self._moh_active:
            # 1. CRÍTICO: Pausar audio_stream
            # Isso evita que ruídos sejam capturados e enviados para OpenAI
            self._stream_paused = False
            try:
                await self._esl.uuid_audio_stream(self.call_uuid, "pause")
                self._stream_paused = True
                logger.info(f"Audio stream paused for transfer on {self.call_uuid}")
            except Exception as e:
                logger.warning(f"Failed to pause audio stream: {e}")
            
            # 2. Delay curto para estabilização
            await asyncio.sleep(0.2)
            
            # 3. Marcar como ativo (modo silêncio - sem playback)
            self._moh_active = True
            logger.info(f"Hold ativado (modo silêncio) for {self.call_uuid}")
    
    async def _stop_moh(self, resume_stream: bool = True) -> None:
        """
        Tira cliente da espera e gerencia o audio_stream.
        
        Robusto: sempre executa o gerenciamento do stream.
        Isso garante que o cliente não fique preso em silêncio.
        
        Args:
            resume_stream: Se True (default), resume o audio_stream.
                          Usar False quando a transferência foi bem-sucedida e o
                          cliente está em bridge com humano.
        
        NOTA: MOH foi removido - agora usamos modo silêncio.
        Não precisa mais de uuid_break para parar música.
        """
        # 1. Desativar flag de hold
        self._moh_active = False
        
        # 2. Delay curto para estabilização
        await asyncio.sleep(0.2)
        
        # 3. CRÍTICO: Gerenciar audio_stream baseado em _stream_paused
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
        
        IMPORTANTE: Usar sofia/internal/ em vez de user/ para preservar domínio!
        O user/ pode fazer lookup incorreto e substituir domínio por IP.
        
        Args:
            dest: Destino da transferência
        
        Returns:
            Dial string para originate
        """
        number = dest.destination_number
        context = dest.destination_context
        
        # NOTA: Usar sofia/internal/ diretamente em vez de user/
        # user/ pode fazer lookup incorreto e substituir domínio por IP
        # Exemplo incorreto: user/1001@domain → sofia/internal/1001@177.72.14.10
        # Correto: sofia/internal/1001@domain preserva o domínio
        
        if dest.destination_type == "external":
            # Número externo - usar gateway padrão
            gateway = os.getenv("DEFAULT_GATEWAY", "default")
            return f"sofia/gateway/{gateway}/{number}"
        
        elif dest.destination_type == "voicemail":
            return f"voicemail/{number}@{context}"
        
        elif dest.destination_type == "fifo" or dest.destination_type == "queue":
            # FIFO queues
            return f"fifo/{number}@{context}"
        
        else:
            # extension, ring_group e outros: usar sofia/internal/ diretamente
            # Isso preserva o domínio correto e evita lookup incorreto
            return f"sofia/internal/{number}@{context}"
    
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
    
    # Obter ESL client específico do tenant (multi-tenant)
    # Se falhar, get_esl_for_domain retorna o singleton padrão
    esl_client = await get_esl_for_domain(domain_uuid)
    
    manager = TransferManager(
        domain_uuid=domain_uuid,
        call_uuid=call_uuid,
        caller_id=caller_id,
        secretary_uuid=secretary_uuid,
        esl_client=esl_client,
        on_resume=on_resume,
        on_transfer_complete=on_transfer_complete,
        domain_settings=domain_settings,
        voice_id=voice_id,
        announcement_tts_provider=announcement_tts_provider,
    )
    
    # Garantir que ESL está conectado ANTES de retornar
    # Isso é crucial para que conference mode funcione
    if not manager._esl.is_connected:
        logger.info("Connecting ESL client in create_transfer_manager...")
        connected = await manager._esl.connect()
        if connected:
            logger.info("ESL client connected successfully")
        else:
            logger.warning("Failed to connect ESL client, transfers may fail")
    
    # Pré-carregar destinos
    await manager.load_destinations()
    
    return manager
