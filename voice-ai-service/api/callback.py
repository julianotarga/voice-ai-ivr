"""
Callback API - Click-to-Call and Callback Origination.

FASE 4: Click-to-Call via Proxy
Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/

MULTI-TENANT: All operations require domain_uuid.

Settings loaded from: v_voice_secretary_settings (FusionPBX database)

Endpoints:
- POST /api/callback/originate - Originar chamada de callback
- POST /api/callback/check-availability - Verificar disponibilidade do ramal
- GET /api/callback/status/{call_uuid} - Status de uma chamada
"""

import os
import logging
import time
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

# ESL Client para comunicação com FreeSWITCH
from realtime.handlers.esl_client import AsyncESLClient, get_esl_client, get_esl_for_domain

# Métricas Prometheus (FASE 6)
from realtime.utils.metrics import get_metrics

# Database service para buscar configurações do FusionPBX
from services.database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/callback", tags=["callback"])


# =============================================================================
# Enums e Dataclasses
# =============================================================================

class OriginateStatus(str, Enum):
    """Status de uma chamada originada."""
    INITIATED = "initiated"       # Chamada iniciada
    RINGING_AGENT = "ringing_agent"  # Tocando no atendente
    AGENT_ANSWERED = "agent_answered"  # Atendente atendeu
    RINGING_CLIENT = "ringing_client"  # Tocando no cliente
    CONNECTED = "connected"       # Chamada conectada (bridge)
    COMPLETED = "completed"       # Chamada encerrada com sucesso
    FAILED = "failed"             # Falhou
    AGENT_BUSY = "agent_busy"     # Atendente ocupado
    AGENT_NO_ANSWER = "agent_no_answer"  # Atendente não atendeu
    CLIENT_NO_ANSWER = "client_no_answer"  # Cliente não atendeu
    CANCELLED = "cancelled"       # Cancelada


class ExtensionStatus(str, Enum):
    """Status de um ramal."""
    AVAILABLE = "available"
    IN_CALL = "in_call"
    RINGING = "ringing"
    DND = "dnd"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


# =============================================================================
# Request/Response Models
# =============================================================================

class OriginateRequest(BaseModel):
    """Request para originar chamada de callback."""
    domain_uuid: str = Field(..., description="UUID do tenant", min_length=1)
    extension: str = Field(..., description="Ramal do atendente (ex: 1001)", pattern=r"^\d{2,6}$")
    client_number: str = Field(..., description="Número do cliente no formato E.164", pattern=r"^55\d{10,11}$")
    ticket_id: Optional[int] = Field(None, description="ID do ticket de callback", ge=1)
    callback_reason: Optional[str] = Field(None, description="Motivo do callback", max_length=500)
    caller_id_name: Optional[str] = Field("Callback", description="Nome do caller ID", max_length=50)
    call_timeout: int = Field(30, description="Timeout para atender (segundos)", ge=10, le=120)
    record: bool = Field(True, description="Gravar chamada")
    
    class Config:
        """Configuração do modelo Pydantic."""
        str_strip_whitespace = True  # Remove espaços em branco


class OriginateResponse(BaseModel):
    """Response de originate."""
    success: bool
    call_uuid: Optional[str] = None
    status: OriginateStatus = OriginateStatus.INITIATED
    message: str = ""
    error: Optional[str] = None


class CheckAvailabilityRequest(BaseModel):
    """Request para verificar disponibilidade."""
    domain_uuid: str = Field(..., description="UUID do tenant", min_length=1)
    extension: str = Field(..., description="Ramal a verificar", pattern=r"^\d{2,6}$")
    
    class Config:
        """Configuração do modelo Pydantic."""
        str_strip_whitespace = True


class CheckAvailabilityResponse(BaseModel):
    """Response de verificação de disponibilidade."""
    extension: str
    status: ExtensionStatus
    available: bool
    reason: Optional[str] = None


class CallStatusResponse(BaseModel):
    """Response de status de chamada."""
    call_uuid: str
    status: OriginateStatus
    duration_seconds: Optional[int] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    hangup_cause: Optional[str] = None


# =============================================================================
# Domain Settings Loader
# =============================================================================

# Cache de configurações por domínio com TTL
# Formato: {domain_uuid: (settings_dict, timestamp)}
_domain_settings_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutos - mesmo valor de transfer_cache_ttl_seconds

async def get_domain_settings(domain_uuid: str) -> Dict[str, Any]:
    """
    Carrega configurações do domínio do banco de dados FusionPBX.
    
    Lê da tabela v_voice_secretary_settings configurada via settings.php.
    
    Cache com TTL de 5 minutos para evitar consultas excessivas ao banco.
    
    Args:
        domain_uuid: UUID do domínio
        
    Returns:
        Dict com todas as configurações (com defaults aplicados)
    """
    current_time = time.time()
    
    # Verificar cache com TTL
    if domain_uuid in _domain_settings_cache:
        cached_settings, cached_at = _domain_settings_cache[domain_uuid]
        if current_time - cached_at < _CACHE_TTL_SECONDS:
            return cached_settings
        else:
            # Cache expirado
            logger.debug(f"Cache expired for domain {domain_uuid[:8]}...")
    
    try:
        settings = await db.get_domain_settings(UUID(domain_uuid))
        _domain_settings_cache[domain_uuid] = (settings, current_time)
        logger.info(f"Loaded domain settings for {domain_uuid[:8]}...", extra={
            "domain_uuid": domain_uuid,
            "esl_host": settings.get('esl_host'),
            "omniplay_api_url": settings.get('omniplay_api_url'),
            "cache_ttl": _CACHE_TTL_SECONDS,
        })
        return settings
    except Exception as e:
        logger.warning(f"Failed to load domain settings, using defaults: {e}")
        # Retornar defaults se falhar (sem cachear erro)
        return {
            'esl_host': '127.0.0.1',
            'esl_port': 8021,
            'esl_password': 'ClueCon',
            'transfer_default_timeout': 30,
            'callback_enabled': True,
            'omniplay_api_url': 'http://127.0.0.1:8080',
            'omniplay_api_timeout_ms': 10000,
        }


def clear_domain_settings_cache(domain_uuid: Optional[str] = None):
    """Limpa cache de configurações."""
    if domain_uuid:
        _domain_settings_cache.pop(domain_uuid, None)
        logger.info(f"Cache cleared for domain {domain_uuid[:8]}...")
    else:
        _domain_settings_cache.clear()
        logger.info("All domain settings cache cleared")


# =============================================================================
# ESL Commands
# =============================================================================

async def get_esl() -> AsyncESLClient:
    """
    Dependency para obter cliente ESL (singleton com configs de variáveis de ambiente).
    
    Para endpoints que precisam de configurações por domínio, usar get_esl_for_domain().
    """
    client = get_esl_client()
    
    if not client.is_connected:
        if not await client.connect():
            raise HTTPException(
                status_code=503,
                detail="Failed to connect to FreeSWITCH ESL"
            )
    
    return client


async def get_esl_with_domain_config(domain_uuid: str) -> AsyncESLClient:
    """
    Obtém cliente ESL configurado com as configurações do domínio.
    
    Lê configurações de v_voice_secretary_settings (esl_host, esl_port, esl_password).
    
    Args:
        domain_uuid: UUID do domínio
        
    Returns:
        AsyncESLClient configurado
        
    Raises:
        HTTPException 503 se falhar ao conectar
    """
    client = await get_esl_for_domain(domain_uuid)
    
    if not client.is_connected:
        if not await client.connect():
            raise HTTPException(
                status_code=503,
                detail="Failed to connect to FreeSWITCH ESL with domain settings"
            )
    
    return client


async def check_extension_registered(
    esl: AsyncESLClient,
    extension: str,
    domain_uuid: str
) -> bool:
    """Verifica se ramal está registrado."""
    try:
        # Buscar registrations do sofia
        result = await esl.execute_api(
            f"sofia status profile internal reg {extension}"
        )
        
        if result and "REGISTERED" in result.upper():
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"Error checking registration: {e}")
        return False


async def check_extension_in_call(
    esl: AsyncESLClient,
    extension: str
) -> bool:
    """Verifica se ramal está em chamada."""
    try:
        # Buscar canais ativos
        result = await esl.execute_api("show channels")
        
        if result and extension in result:
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"Error checking channels: {e}")
        return False


async def check_extension_dnd(
    extension: str,
    domain_uuid: str
) -> bool:
    """
    Verifica se ramal está em DND (Do Not Disturb).
    
    Consulta o banco de dados FusionPBX para verificar o status DND do ramal.
    
    Args:
        extension: Número do ramal (ex: "1001")
        domain_uuid: UUID do domínio
        
    Returns:
        True se o ramal está em DND, False caso contrário
    """
    try:
        from services.database import db
        pool = await db.get_pool()
        
        # Consultar tabela v_extensions do FusionPBX
        query = """
            SELECT do_not_disturb 
            FROM v_extensions 
            WHERE extension = $1 
              AND domain_uuid = $2::uuid
            LIMIT 1
        """
        row = await pool.fetchrow(query, extension, domain_uuid)
        
        if row and row.get('do_not_disturb'):
            dnd_value = str(row['do_not_disturb']).lower()
            return dnd_value in ('true', '1', 'yes', 'on')
        
        return False
        
    except Exception as e:
        logger.warning(f"Error checking DND for {extension}: {e}")
        # Em caso de erro, assume que não está em DND para não bloquear
        return False


# =============================================================================
# API Endpoints
# =============================================================================

@router.post("/check-availability", response_model=CheckAvailabilityResponse)
async def check_availability(request: CheckAvailabilityRequest):
    """
    Verifica disponibilidade de um ramal para callback.
    
    Checagens:
    1. Ramal registrado no FreeSWITCH
    2. Ramal não está em chamada
    3. Ramal não está em DND
    """
    if not request.domain_uuid:
        raise HTTPException(status_code=400, detail="domain_uuid is required")
    
    try:
        # Usar ESL configurado para o domínio (lê do banco de dados)
        esl = await get_esl_with_domain_config(request.domain_uuid)
        
        # 1. Verificar registro
        is_registered = await check_extension_registered(
            esl, request.extension, request.domain_uuid
        )
        
        metrics = get_metrics()
        
        if not is_registered:
            # Registrar métrica de falha
            metrics.record_extension_check(
                domain_uuid=request.domain_uuid,
                extension=request.extension,
                status="offline",
                available=False
            )
            return CheckAvailabilityResponse(
                extension=request.extension,
                status=ExtensionStatus.OFFLINE,
                available=False,
                reason="Ramal não registrado"
            )
        
        # 2. Verificar se está em chamada
        in_call = await check_extension_in_call(esl, request.extension)
        
        if in_call:
            # Registrar métrica de falha
            metrics.record_extension_check(
                domain_uuid=request.domain_uuid,
                extension=request.extension,
                status="in_call",
                available=False
            )
            return CheckAvailabilityResponse(
                extension=request.extension,
                status=ExtensionStatus.IN_CALL,
                available=False,
                reason="Em chamada ativa"
            )
        
        # 3. Verificar DND
        is_dnd = await check_extension_dnd(request.extension, request.domain_uuid)
        
        if is_dnd:
            # Registrar métrica de falha
            metrics.record_extension_check(
                domain_uuid=request.domain_uuid,
                extension=request.extension,
                status="dnd",
                available=False
            )
            return CheckAvailabilityResponse(
                extension=request.extension,
                status=ExtensionStatus.DND,
                available=False,
                reason="Modo não perturbe ativado"
            )
        
        # Disponível!
        # Registrar métrica de sucesso
        metrics.record_extension_check(
            domain_uuid=request.domain_uuid,
            extension=request.extension,
            status="available",
            available=True
        )
        
        return CheckAvailabilityResponse(
            extension=request.extension,
            status=ExtensionStatus.AVAILABLE,
            available=True,
            reason=None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error checking availability: {e}")
        return CheckAvailabilityResponse(
            extension=request.extension,
            status=ExtensionStatus.UNKNOWN,
            available=False,
            reason=str(e)
        )


@router.post("/originate", response_model=OriginateResponse)
async def originate_callback(request: OriginateRequest):
    """
    Origina uma chamada de callback.
    
    Fluxo:
    1. Verifica disponibilidade do atendente
    2. Liga para o atendente (A-leg)
    3. Quando atendente atende, liga para o cliente (B-leg)
    4. Faz bridge entre as duas pernas
    
    O atendente vê o caller ID do cliente.
    A chamada é gravada se record=true.
    
    Settings loaded from: v_voice_secretary_settings (FusionPBX)
    """
    if not request.domain_uuid:
        raise HTTPException(status_code=400, detail="domain_uuid is required")
    
    # Carregar configurações do domínio do banco de dados FusionPBX
    domain_settings = await get_domain_settings(request.domain_uuid)
    
    # Verificar se callback está habilitado
    if not domain_settings.get('callback_enabled', True):
        return OriginateResponse(
            success=False,
            status=OriginateStatus.FAILED,
            error="Callback desabilitado",
            message="O sistema de callback está desabilitado para este domínio."
        )
    
    logger.info(
        "Originating callback",
        extra={
            "domain_uuid": request.domain_uuid,
            "extension": request.extension,
            "client_number": request.client_number,
            "ticket_id": request.ticket_id,
            "transfer_timeout": domain_settings.get('transfer_default_timeout', 30),
        }
    )
    
    try:
        # Usar ESL configurado para o domínio (lê do banco de dados)
        esl = await get_esl_with_domain_config(request.domain_uuid)
        
        # 1. Double-check disponibilidade
        is_registered = await check_extension_registered(
            esl, request.extension, request.domain_uuid
        )
        
        if not is_registered:
            return OriginateResponse(
                success=False,
                status=OriginateStatus.AGENT_BUSY,
                error="Ramal não está registrado",
                message="O ramal não está online. Verifique se o softphone está conectado."
            )
        
        in_call = await check_extension_in_call(esl, request.extension)
        
        if in_call:
            return OriginateResponse(
                success=False,
                status=OriginateStatus.AGENT_BUSY,
                error="Ramal em chamada",
                message="O ramal está em chamada. Tente novamente em alguns segundos."
            )
        
        # 2. Construir comando originate
        # Formato: originate {vars}dial_string &bridge(destination)
        
        # Usar timeout do banco se não especificado na request
        call_timeout = request.call_timeout or domain_settings.get('transfer_default_timeout', 30)
        
        # Variáveis de canal
        channel_vars = [
            f"origination_caller_id_number={request.client_number}",
            f"origination_caller_id_name={request.caller_id_name}",
            f"domain_uuid={request.domain_uuid}",
            f"call_direction=outbound",
            f"call_timeout={call_timeout}",
        ]
        
        if request.ticket_id:
            channel_vars.append(f"ticket_id={request.ticket_id}")
        
        if request.callback_reason:
            # Escapar caracteres especiais
            reason = request.callback_reason.replace(",", " ")[:100]
            channel_vars.append(f"callback_reason={reason}")
        
        if request.record:
            channel_vars.append("record_session=true")
        
        vars_str = ",".join(channel_vars)
        
        # Dial string para o atendente
        agent_dial = f"user/{request.extension}@{request.domain_uuid}"
        
        # Dial string para o cliente (via gateway default)
        # TODO: Configurar gateway correto baseado no domain
        client_dial = f"sofia/gateway/default/{request.client_number}"
        
        # Comando completo
        # Liga para o atendente primeiro, quando atender faz bridge com cliente
        originate_cmd = f"originate {{{vars_str}}}{agent_dial} &bridge({client_dial})"
        
        logger.debug(f"ESL originate command: {originate_cmd}")
        
        # 3. Executar originate em background
        result = await esl.execute_bgapi(originate_cmd)
        
        if result and "+OK" in result:
            # Extrair Job-UUID da resposta
            # Formato: +OK Job-UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
            call_uuid = None
            for line in result.split("\n"):
                if "Job-UUID:" in line:
                    call_uuid = line.split("Job-UUID:")[-1].strip()
                    break
            
            logger.info(
                "Callback originate initiated",
                extra={
                    "call_uuid": call_uuid,
                    "extension": request.extension,
                    "client_number": request.client_number,
                }
            )
            
            # Registrar métrica de click-to-call
            metrics = get_metrics()
            metrics.record_click_to_call(
                domain_uuid=request.domain_uuid,
                extension=request.extension,
                client_number=request.client_number,
                ticket_id=request.ticket_id,
                status="initiated"
            )
            
            # Iniciar monitoramento em background (se tiver ticket_id)
            if call_uuid and request.ticket_id:
                import asyncio
                asyncio.create_task(
                    monitor_callback_result(
                        call_uuid=call_uuid,
                        ticket_id=request.ticket_id,
                        domain_uuid=request.domain_uuid
                    )
                )
                logger.info(
                    "Started background monitoring for callback",
                    extra={
                        "call_uuid": call_uuid,
                        "ticket_id": request.ticket_id
                    }
                )
            
            return OriginateResponse(
                success=True,
                call_uuid=call_uuid,
                status=OriginateStatus.INITIATED,
                message="Ligação de callback iniciada. Aguarde..."
            )
        
        else:
            # Parse error
            error_msg = "Falha ao originar chamada"
            
            if result:
                if "USER_BUSY" in result:
                    error_msg = "Ramal ocupado"
                elif "NO_ANSWER" in result:
                    error_msg = "Ramal não atendeu"
                elif "SUBSCRIBER_ABSENT" in result:
                    error_msg = "Ramal offline"
                elif "CALL_REJECTED" in result:
                    error_msg = "Chamada rejeitada"
                else:
                    error_msg = result[:200]
            
            logger.error(f"Originate failed: {error_msg}")
            
            # Registrar métrica de falha
            metrics = get_metrics()
            metrics.record_click_to_call(
                domain_uuid=request.domain_uuid,
                extension=request.extension,
                client_number=request.client_number,
                ticket_id=request.ticket_id,
                status="failed"
            )
            
            return OriginateResponse(
                success=False,
                status=OriginateStatus.FAILED,
                error=error_msg,
                message="Não foi possível iniciar a chamada. Tente novamente."
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error originating callback: {e}")
        return OriginateResponse(
            success=False,
            status=OriginateStatus.FAILED,
            error=str(e),
            message="Erro interno ao originar chamada."
        )


@router.get("/status/{call_uuid}", response_model=CallStatusResponse)
async def get_call_status(call_uuid: str):
    """
    Retorna status de uma chamada.
    
    Consulta o FreeSWITCH para obter estado atual.
    """
    try:
        esl = await get_esl()
        
        # Verificar se a chamada está ativa
        result = await esl.execute_api(f"uuid_exists {call_uuid}")
        
        if result and "true" in result.lower():
            # Chamada ativa - obter detalhes
            # uuid_dump <uuid>
            dump = await esl.execute_api(f"uuid_dump {call_uuid}")
            
            # Parse básico do dump
            answered = "Answered" in dump if dump else False
            
            return CallStatusResponse(
                call_uuid=call_uuid,
                status=OriginateStatus.CONNECTED if answered else OriginateStatus.RINGING_AGENT,
                duration_seconds=None,  # TODO: Parse do dump
                answered_at=None,
                ended_at=None,
                hangup_cause=None
            )
        else:
            # Chamada não existe mais
            return CallStatusResponse(
                call_uuid=call_uuid,
                status=OriginateStatus.COMPLETED,
                duration_seconds=None,
                answered_at=None,
                ended_at=None,
                hangup_cause=None
            )
        
    except Exception as e:
        logger.exception(f"Error getting call status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cancel/{call_uuid}")
async def cancel_callback(call_uuid: str):
    """
    Cancela uma chamada de callback em andamento.
    """
    try:
        esl = await get_esl()
        
        # Verificar se a chamada existe
        exists = await esl.execute_api(f"uuid_exists {call_uuid}")
        
        if exists and "true" in exists.lower():
            # Desligar a chamada
            result = await esl.execute_api(f"uuid_kill {call_uuid} NORMAL_CLEARING")
            
            logger.info(f"Callback cancelled: {call_uuid}")
            
            return {"success": True, "message": "Chamada cancelada"}
        else:
            return {"success": False, "message": "Chamada não encontrada ou já encerrada"}
        
    except Exception as e:
        logger.exception(f"Error cancelling callback: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings/reload")
async def reload_settings(domain_uuid: str):
    """
    Recarrega as configurações do domínio do banco de dados.
    
    Deve ser chamado pelo FusionPBX após salvar configurações.
    """
    if not domain_uuid:
        raise HTTPException(status_code=400, detail="domain_uuid is required")
    
    # Limpar cache
    clear_domain_settings_cache(domain_uuid)
    
    # Recarregar configurações
    settings = await get_domain_settings(domain_uuid)
    
    logger.info(f"Settings reloaded for domain {domain_uuid[:8]}...")
    
    return {
        "success": True, 
        "message": "Configurações recarregadas",
        "settings": {
            "esl_host": settings.get('esl_host'),
            "callback_enabled": settings.get('callback_enabled'),
            "transfer_default_timeout": settings.get('transfer_default_timeout'),
        }
    }


@router.get("/settings/{domain_uuid}")
async def get_settings(domain_uuid: str):
    """
    Retorna as configurações atuais do domínio.
    
    Útil para debug e verificação.
    """
    if not domain_uuid:
        raise HTTPException(status_code=400, detail="domain_uuid is required")
    
    settings = await get_domain_settings(domain_uuid)
    
    # Não retornar senhas
    safe_settings = {k: v for k, v in settings.items() if 'password' not in k.lower() and 'key' not in k.lower()}
    
    return {"domain_uuid": domain_uuid, "settings": safe_settings}


# =============================================================================
# Background Monitoring - Monitorar resultado de chamadas
# =============================================================================

# Cache de chamadas em monitoramento
# Formato: {call_uuid: {"ticket_id": int, "domain_uuid": str, "started_at": datetime}}
_active_callbacks: Dict[str, Dict[str, Any]] = {}


async def notify_omniplay_callback_result(
    ticket_id: int,
    domain_uuid: str,
    status: str,
    duration_seconds: Optional[int] = None,
    hangup_cause: Optional[str] = None
) -> bool:
    """
    Notifica o OmniPlay sobre o resultado do callback.
    
    Args:
        ticket_id: ID do ticket no OmniPlay
        domain_uuid: UUID do domínio
        status: "completed" | "failed" | "no_answer"
        duration_seconds: Duração da chamada
        hangup_cause: Causa do desligamento
    
    Returns:
        True se notificação foi enviada com sucesso
    """
    try:
        domain_settings = await get_domain_settings(domain_uuid)
        omniplay_url = domain_settings.get('omniplay_api_url', 'http://127.0.0.1:8080')
        
        payload = {
            "ticketId": ticket_id,
            "status": status,
            "durationSeconds": duration_seconds,
            "hangupCause": hangup_cause,
            "completedAt": datetime.now().isoformat()
        }
        
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{omniplay_url}/api/callbacks/{ticket_id}/complete",
                json={"success": status == "completed", "duration": duration_seconds},
                headers={
                    "Content-Type": "application/json",
                    "X-Service-Name": "voice-ai-service"
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status in (200, 201):
                    logger.info(
                        "OmniPlay notified of callback result",
                        extra={
                            "ticket_id": ticket_id,
                            "status": status,
                            "duration": duration_seconds
                        }
                    )
                    return True
                else:
                    logger.error(
                        f"Failed to notify OmniPlay: {response.status}",
                        extra={"ticket_id": ticket_id}
                    )
                    return False
                    
    except Exception as e:
        logger.exception(f"Error notifying OmniPlay: {e}")
        return False


async def monitor_callback_result(
    call_uuid: str,
    ticket_id: int,
    domain_uuid: str,
    timeout_seconds: int = 3600
) -> None:
    """
    Monitora o resultado de um callback em background.
    
    Aguarda eventos de CHANNEL_BRIDGE (conectado) e CHANNEL_HANGUP (desligou).
    Notifica o OmniPlay quando a chamada terminar.
    
    Esta função deve ser chamada em asyncio.create_task() após originate.
    
    Args:
        call_uuid: UUID da chamada
        ticket_id: ID do ticket no OmniPlay
        domain_uuid: UUID do domínio
        timeout_seconds: Timeout máximo de monitoramento
    """
    logger.info(
        "Starting callback monitoring",
        extra={
            "call_uuid": call_uuid,
            "ticket_id": ticket_id,
            "timeout": timeout_seconds
        }
    )
    
    # Registrar callback ativo
    _active_callbacks[call_uuid] = {
        "ticket_id": ticket_id,
        "domain_uuid": domain_uuid,
        "started_at": datetime.now()
    }
    
    try:
        esl = await get_esl()
        
        # Aguardar eventos
        connected = False
        answered_at: Optional[datetime] = None
        hangup_cause: Optional[str] = None
        
        start_time = time.time()
        check_interval = 5  # Verificar a cada 5 segundos
        
        while time.time() - start_time < timeout_seconds:
            # Verificar se a chamada ainda existe
            exists_result = await esl.execute_api(f"uuid_exists {call_uuid}")
            
            if exists_result and "true" in exists_result.lower():
                # Chamada ainda ativa
                # Verificar se já está em bridge (conectada)
                dump_result = await esl.execute_api(f"uuid_dump {call_uuid} json")
                
                if dump_result and "Answered" in dump_result:
                    if not connected:
                        connected = True
                        answered_at = datetime.now()
                        logger.info(
                            "Callback connected",
                            extra={
                                "call_uuid": call_uuid,
                                "ticket_id": ticket_id
                            }
                        )
                
                # Aguardar um pouco antes de verificar novamente
                import asyncio
                await asyncio.sleep(check_interval)
                
            else:
                # Chamada terminou
                # Tentar obter hangup cause do último CDR
                # (Na prática, seria necessário consultar o CDR do FreeSWITCH)
                
                duration = None
                if answered_at:
                    duration = int((datetime.now() - answered_at).total_seconds())
                    status = "completed"
                else:
                    status = "failed"
                
                logger.info(
                    "Callback ended",
                    extra={
                        "call_uuid": call_uuid,
                        "ticket_id": ticket_id,
                        "status": status,
                        "duration": duration,
                        "was_connected": connected
                    }
                )
                
                # Registrar métrica
                metrics = get_metrics()
                metrics.record_callback_completed(
                    domain_uuid=domain_uuid,
                    ticket_id=ticket_id,
                    status=status,
                    duration=duration
                )
                
                # Notificar OmniPlay
                await notify_omniplay_callback_result(
                    ticket_id=ticket_id,
                    domain_uuid=domain_uuid,
                    status=status,
                    duration_seconds=duration,
                    hangup_cause=hangup_cause
                )
                
                break
        
        else:
            # Timeout
            logger.warning(
                "Callback monitoring timeout",
                extra={
                    "call_uuid": call_uuid,
                    "ticket_id": ticket_id,
                    "timeout": timeout_seconds
                }
            )
            
            # Notificar como incompleto
            await notify_omniplay_callback_result(
                ticket_id=ticket_id,
                domain_uuid=domain_uuid,
                status="timeout",
                hangup_cause="MONITORING_TIMEOUT"
            )
    
    except Exception as e:
        logger.exception(f"Error monitoring callback: {e}")
        
        # Notificar erro
        await notify_omniplay_callback_result(
            ticket_id=ticket_id,
            domain_uuid=domain_uuid,
            status="error",
            hangup_cause=str(e)
        )
    
    finally:
        # Remover do cache
        _active_callbacks.pop(call_uuid, None)


@router.post("/monitor")
async def start_monitoring(
    call_uuid: str,
    ticket_id: int,
    domain_uuid: str
):
    """
    Inicia monitoramento de uma chamada em background.
    
    Usado pelo OmniPlay para monitorar o resultado de um callback
    que já foi originado.
    """
    if not call_uuid or not ticket_id or not domain_uuid:
        raise HTTPException(
            status_code=400, 
            detail="call_uuid, ticket_id and domain_uuid required"
        )
    
    # Verificar se já está monitorando
    if call_uuid in _active_callbacks:
        return {
            "success": True, 
            "message": "Já está sendo monitorado",
            "already_monitoring": True
        }
    
    # Iniciar monitoramento em background
    import asyncio
    asyncio.create_task(
        monitor_callback_result(
            call_uuid=call_uuid,
            ticket_id=ticket_id,
            domain_uuid=domain_uuid
        )
    )
    
    return {
        "success": True,
        "message": "Monitoramento iniciado",
        "call_uuid": call_uuid
    }


@router.get("/active")
async def list_active_callbacks():
    """
    Lista callbacks ativos sendo monitorados.
    
    Útil para debug e monitoramento.
    """
    active_list = []
    for call_uuid, data in _active_callbacks.items():
        started_at = data.get("started_at")
        duration = None
        if started_at:
            duration = int((datetime.now() - started_at).total_seconds())
        
        active_list.append({
            "call_uuid": call_uuid,
            "ticket_id": data.get("ticket_id"),
            "domain_uuid": data.get("domain_uuid", "")[:8] + "...",
            "monitoring_duration_seconds": duration
        })
    
    return {
        "count": len(active_list),
        "callbacks": active_list
    }


# Health check
@router.get("/health")
async def callback_health():
    """Health check para o serviço de callback."""
    return {
        "status": "ok", 
        "service": "callback",
        "active_callbacks": len(_active_callbacks)
    }
