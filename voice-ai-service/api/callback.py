"""
Callback API - Click-to-Call and Callback Origination.

FASE 4: Click-to-Call via Proxy
Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/

MULTI-TENANT: All operations require domain_uuid.

Endpoints:
- POST /api/callback/originate - Originar chamada de callback
- POST /api/callback/check-availability - Verificar disponibilidade do ramal
- GET /api/callback/status/{call_uuid} - Status de uma chamada
"""

import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

# ESL Client para comunicação com FreeSWITCH
from realtime.handlers.esl_client import AsyncESLClient, get_esl_client

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
    domain_uuid: str = Field(..., description="UUID do tenant")
    extension: str = Field(..., description="Ramal do atendente (ex: 1001)")
    client_number: str = Field(..., description="Número do cliente no formato E.164")
    ticket_id: Optional[int] = Field(None, description="ID do ticket de callback")
    callback_reason: Optional[str] = Field(None, description="Motivo do callback")
    caller_id_name: Optional[str] = Field("Callback", description="Nome do caller ID")
    call_timeout: int = Field(30, description="Timeout para atender (segundos)")
    record: bool = Field(True, description="Gravar chamada")


class OriginateResponse(BaseModel):
    """Response de originate."""
    success: bool
    call_uuid: Optional[str] = None
    status: OriginateStatus = OriginateStatus.INITIATED
    message: str = ""
    error: Optional[str] = None


class CheckAvailabilityRequest(BaseModel):
    """Request para verificar disponibilidade."""
    domain_uuid: str
    extension: str


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
# ESL Commands
# =============================================================================

async def get_esl() -> AsyncESLClient:
    """Dependency para obter cliente ESL."""
    client = get_esl_client()
    
    if not client.is_connected:
        if not await client.connect():
            raise HTTPException(
                status_code=503,
                detail="Failed to connect to FreeSWITCH ESL"
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
    
    Nota: Esta verificação é feita no banco de dados FusionPBX.
    Aqui fazemos uma simplificação - em produção, consultar o banco.
    """
    # TODO: Consultar banco de dados FusionPBX
    # SELECT do_not_disturb FROM v_extensions WHERE extension = $1 AND domain_uuid = $2
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
        esl = await get_esl()
        
        # 1. Verificar registro
        is_registered = await check_extension_registered(
            esl, request.extension, request.domain_uuid
        )
        
        if not is_registered:
            return CheckAvailabilityResponse(
                extension=request.extension,
                status=ExtensionStatus.OFFLINE,
                available=False,
                reason="Ramal não registrado"
            )
        
        # 2. Verificar se está em chamada
        in_call = await check_extension_in_call(esl, request.extension)
        
        if in_call:
            return CheckAvailabilityResponse(
                extension=request.extension,
                status=ExtensionStatus.IN_CALL,
                available=False,
                reason="Em chamada ativa"
            )
        
        # 3. Verificar DND
        is_dnd = await check_extension_dnd(request.extension, request.domain_uuid)
        
        if is_dnd:
            return CheckAvailabilityResponse(
                extension=request.extension,
                status=ExtensionStatus.DND,
                available=False,
                reason="Modo não perturbe ativado"
            )
        
        # Disponível!
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
    """
    if not request.domain_uuid:
        raise HTTPException(status_code=400, detail="domain_uuid is required")
    
    logger.info(
        "Originating callback",
        extra={
            "domain_uuid": request.domain_uuid,
            "extension": request.extension,
            "client_number": request.client_number,
            "ticket_id": request.ticket_id,
        }
    )
    
    try:
        esl = await get_esl()
        
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
        
        # Variáveis de canal
        channel_vars = [
            f"origination_caller_id_number={request.client_number}",
            f"origination_caller_id_name={request.caller_id_name}",
            f"domain_uuid={request.domain_uuid}",
            f"call_direction=outbound",
            f"call_timeout={request.call_timeout}",
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


# Health check
@router.get("/health")
async def callback_health():
    """Health check para o serviço de callback."""
    return {"status": "ok", "service": "callback"}
