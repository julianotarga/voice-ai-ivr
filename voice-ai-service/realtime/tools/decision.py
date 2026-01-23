"""
Tools de Decisão de Transferência.

Implementa accept_transfer e reject_transfer usados durante
o anúncio da chamada para o atendente.
"""

from typing import Any, Dict, Optional
from .base import VoiceAITool, ToolCategory, ToolContext, ToolResult, ValidationResult
import logging

logger = logging.getLogger(__name__)


class AcceptTransferTool(VoiceAITool):
    """
    Tool para aceitar uma transferência de chamada.
    
    Usado durante o anúncio ao atendente quando ele
    confirma que pode atender a chamada.
    """
    
    name = "accept_transfer"
    description = (
        "Confirma que o atendente pode atender a chamada. "
        "Use quando o atendente disser: 'sim', 'pode', 'pode transferir', "
        "'manda', 'passa pra mim', 'tudo bem', 'ok', 'estou disponível'."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "confirmation_phrase": {
                "type": "string",
                "description": "Frase exata que o atendente disse para aceitar"
            }
        },
        "required": []
    }
    
    category = ToolCategory.DECISION
    requires_response = False  # Ação imediata, sem resposta da IA
    filler_phrases = []
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """
        Processa aceitação de transferência.
        
        Sinaliza ao TransferManager que pode prosseguir com o bridge.
        """
        confirmation = kwargs.get("confirmation_phrase", "confirmado")
        
        logger.info(
            "✅ [ACCEPT_TRANSFER] Atendente aceitou a chamada",
            extra={
                "call_uuid": context.call_uuid,
                "confirmation": confirmation
            }
        )
        
        # Sinalizar decisão via session/transfer_manager
        session = context._session
        if session and hasattr(session, '_transfer_decision_event'):
            from ..handlers.transfer_manager import TransferDecision
            session._transfer_decision = TransferDecision.ACCEPT
            session._transfer_decision_event.set()
            logger.info("✅ [ACCEPT_TRANSFER] Event sinalizado para TransferManager")
        
        return ToolResult.ok(
            data={
                "status": "accepted",
                "action": "bridge_client"
            },
            should_respond=False,
            side_effects=["transfer_accepted"]
        )


class RejectTransferTool(VoiceAITool):
    """
    Tool para rejeitar uma transferência de chamada.
    
    Usado durante o anúncio ao atendente quando ele
    indica que não pode atender a chamada.
    """
    
    name = "reject_transfer"
    description = (
        "Indica que o atendente NÃO pode atender a chamada. "
        "Use APENAS quando o atendente disser claramente: "
        "'não posso agora', 'estou ocupado', 'em reunião', 'mais tarde', 'não'. "
        "NÃO use para respostas ambíguas - peça confirmação."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Motivo da recusa (ex: 'em reunião', 'ocupado')"
            },
            "suggest_callback": {
                "type": "boolean",
                "description": "Se o atendente sugeriu retornar depois"
            }
        },
        "required": ["reason"]
    }
    
    category = ToolCategory.DECISION
    requires_response = False
    filler_phrases = []
    
    def validate(self, **kwargs) -> ValidationResult:
        """Valida que há um motivo de recusa."""
        reason = kwargs.get("reason", "").strip()
        
        # Motivos muito vagos devem ser rejeitados
        vague_reasons = {"", "não sei", "talvez", "hmm", "energia", "ok"}
        if reason.lower() in vague_reasons:
            return ValidationResult.fail(
                "Motivo de recusa muito vago - confirme com o atendente se pode ou não atender"
            )
        
        return ValidationResult.ok()
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """
        Processa rejeição de transferência.
        
        Sinaliza ao TransferManager para oferecer recado ao cliente.
        """
        reason = kwargs.get("reason", "ocupado")
        suggest_callback = kwargs.get("suggest_callback", False)
        
        logger.info(
            "❌ [REJECT_TRANSFER] Atendente recusou a chamada",
            extra={
                "call_uuid": context.call_uuid,
                "reason": reason,
                "suggest_callback": suggest_callback
            }
        )
        
        # Sinalizar decisão via session/transfer_manager
        session = context._session
        if session and hasattr(session, '_transfer_decision_event'):
            from ..handlers.transfer_manager import TransferDecision
            session._transfer_decision = TransferDecision.REJECT
            session._transfer_rejection_reason = reason
            session._transfer_decision_event.set()
            logger.info("❌ [REJECT_TRANSFER] Event sinalizado para TransferManager")
        
        return ToolResult.ok(
            data={
                "status": "rejected",
                "reason": reason,
                "action": "offer_message"
            },
            should_respond=False,
            side_effects=["transfer_rejected"]
        )
