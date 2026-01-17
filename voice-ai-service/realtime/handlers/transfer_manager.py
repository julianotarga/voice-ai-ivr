"""
TransferManager - Gerencia transferências de chamadas com monitoramento de eventos.

Referências:
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/proposal.md
- voice-ai-ivr/openspec/changes/intelligent-voice-handoff/tasks.md (1.3)

DECISÃO TÉCNICA IMPORTANTE (da proposal.md):
Usar uuid_broadcast + originate + uuid_bridge (NÃO uuid_transfer).

Motivo: uuid_transfer encerra a sessão ESL imediatamente, impedindo
o monitoramento do resultado. Com originate + bridge, mantemos controle
total da chamada e podemos retomar se o destino não atender.

Fluxo de attended transfer:
1. uuid_broadcast para tocar música de espera no A-leg
2. originate para criar B-leg (chamada para destino)
3. Monitorar eventos CHANNEL_ANSWER / CHANNEL_HANGUP no B-leg
4. Se atendeu: uuid_bridge para conectar A e B
5. Se não atendeu: uuid_break + retomar Voice AI

Multi-tenant: domain_uuid obrigatório em todas as operações.
"""

import os
import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .esl_client import AsyncESLClient, ESLEvent, get_esl_client
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
STATUS_MESSAGES: Dict[TransferStatus, str] = {
    TransferStatus.SUCCESS: "Conectando você agora.",
    TransferStatus.BUSY: "O ramal está ocupado no momento.",
    TransferStatus.NO_ANSWER: "Não está atendendo no momento.",
    TransferStatus.DND: "O ramal está em modo não perturbe.",
    TransferStatus.OFFLINE: "O ramal não está disponível no momento.",
    TransferStatus.REJECTED: "A chamada foi recusada.",
    TransferStatus.UNAVAILABLE: "O destino não está disponível no momento.",
    TransferStatus.FAILED: "Não foi possível completar a transferência.",
    TransferStatus.CANCELLED: "A chamada foi cancelada.",
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
        """Retorna mensagem contextual para o status."""
        base_msg = STATUS_MESSAGES.get(self.status, "Não foi possível completar a transferência.")
        
        if self.destination:
            name = self.destination.name
            if self.status == TransferStatus.BUSY:
                return f"O ramal de {name} está ocupado no momento."
            elif self.status == TransferStatus.NO_ANSWER:
                return f"{name} não está atendendo no momento."
            elif self.status == TransferStatus.DND:
                return f"O ramal de {name} está em modo não perturbe."
            elif self.status == TransferStatus.OFFLINE:
                return f"O ramal de {name} não está disponível no momento."
        
        return base_msg
    
    @property
    def should_offer_callback(self) -> bool:
        """Retorna True se deve oferecer callback/recado."""
        return self.status in [
            TransferStatus.BUSY,
            TransferStatus.NO_ANSWER,
            TransferStatus.DND,
            TransferStatus.OFFLINE,
            TransferStatus.REJECTED,
            TransferStatus.UNAVAILABLE,
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
        """
        self.domain_uuid = domain_uuid
        self.call_uuid = call_uuid
        self.caller_id = caller_id
        self.secretary_uuid = secretary_uuid
        
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
                
                b_leg_uuid = await self._esl.originate(
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
                
                if not b_leg_uuid:
                    await self._stop_moh()
                    return TransferResult(
                        status=TransferStatus.FAILED,
                        destination=destination,
                        error="Falha ao originar B-leg",
                        retries=retries
                    )
                
                self._b_leg_uuid = b_leg_uuid
                
                # 6. IMPORTANTE: api originate é SÍNCRONO!
                # Se retornou +OK, significa que o B-leg JÁ FOI ATENDIDO
                # Não precisamos monitorar CHANNEL_ANSWER - ir direto para bridge!
                # 
                # Ref: https://developer.signalwire.com/freeswitch/Originate
                # "api originate" bloqueia até o destino atender ou falhar
                
                logger.info(f"B-leg answered (originate success): {b_leg_uuid}")
                
                # 7. Parar MOH antes do bridge
                await self._stop_moh()
                
                # 8. Criar bridge IMEDIATAMENTE (B-leg já está atendida)
                bridge_success = await self._esl.uuid_bridge(
                    self.call_uuid,
                    b_leg_uuid
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
                        f"Transfer successful: bridge established",
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
        """Inicia música de espera no A-leg."""
        if not self._moh_active:
            success = await self._esl.uuid_broadcast(
                self.call_uuid,
                self._transfer_music_on_hold,
                leg="aleg"
            )
            if success:
                self._moh_active = True
                logger.debug(f"MOH started for {self.call_uuid}")
    
    async def _stop_moh(self) -> None:
        """Para música de espera."""
        if self._moh_active:
            await self._esl.uuid_break(self.call_uuid)
            self._moh_active = False
            logger.debug(f"MOH stopped for {self.call_uuid}")
    
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
    )
    
    # Pré-carregar destinos
    await manager.load_destinations()
    
    return manager
