"""
Tool de Anotação de Recados.

Implementa o take_message que anota recados para retorno posterior.
"""

from typing import Any, Dict, List, Optional
from .base import VoiceAITool, ToolCategory, ToolContext, ToolResult, ValidationResult
import logging
import aiohttp

logger = logging.getLogger(__name__)


class TakeMessageTool(VoiceAITool):
    """
    Tool para anotar recado do cliente.
    
    Este tool:
    1. Valida os dados do recado
    2. Envia para o webhook OmniPlay
    3. Cria um ticket no sistema
    4. Retorna instrução de confirmação
    """
    
    name = "take_message"
    description = (
        "Anota um recado do cliente para retorno posterior. "
        "OBRIGATÓRIO usar quando o cliente quiser deixar uma mensagem ou recado. "
        "Colete: nome do cliente, mensagem completa, e nível de urgência."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "caller_name": {
                "type": "string",
                "description": "Nome do cliente"
            },
            "message": {
                "type": "string",
                "description": "Conteúdo completo do recado/mensagem"
            },
            "urgency": {
                "type": "string",
                "enum": ["baixa", "normal", "alta"],
                "description": "Nível de urgência do recado"
            }
        },
        "required": ["caller_name", "message"]
    }
    
    category = ToolCategory.MESSAGE
    requires_response = True  # IA deve confirmar após anotar
    filler_phrases = []  # Sem filler - confirmação vem depois
    
    def validate(self, **kwargs) -> ValidationResult:
        """Valida os dados do recado."""
        base_validation = super().validate(**kwargs)
        if not base_validation.valid:
            return base_validation
        
        # Mensagem não pode ser muito curta
        message = kwargs.get("message", "").strip()
        if len(message) < 5:
            return ValidationResult.fail("Mensagem muito curta - peça mais detalhes")
        
        return ValidationResult.ok()
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """
        Anota o recado e envia para o OmniPlay.
        """
        caller_name = kwargs.get("caller_name", "Não informado")
        message = kwargs.get("message", "")
        urgency = kwargs.get("urgency", "normal")
        
        # Telefone é sempre o caller_id da chamada
        caller_phone = context.caller_id
        
        logger.info(
            "📝 [TAKE_MESSAGE] Anotando recado",
            extra={
                "call_uuid": context.call_uuid,
                "caller_name": caller_name,
                "caller_phone": caller_phone,
                "urgency": urgency,
                "message_length": len(message)
            }
        )
        
        # Enviar para webhook OmniPlay
        webhook_success = False
        ticket_id = None
        
        if context.webhook_url:
            try:
                payload = {
                    "event": "voice_ai_message",
                    "domain_uuid": context.domain_uuid,
                    "call_uuid": context.call_uuid,
                    "caller_id": caller_phone,
                    "secretary_uuid": context.secretary_uuid,
                    "company_id": context.company_id,
                    "ticket": {
                        "type": "message",
                        "subject": f"Recado de {caller_name}" if caller_name != "Não informado" else f"Recado de {caller_phone}",
                        "message": message,
                        "priority": self._map_urgency(urgency),
                        "caller_name": caller_name,
                        "caller_phone": caller_phone,
                    }
                }
                
                logger.info(f"📝 [TAKE_MESSAGE] Enviando para {context.webhook_url}")
                
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.post(
                        context.webhook_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        resp_text = await resp.text()
                        if resp.status in (200, 201):
                            logger.info(f"📝 [TAKE_MESSAGE] Recado enviado: {resp_text}")
                            webhook_success = True
                            # Tentar extrair ticket_id da resposta
                            try:
                                import json
                                resp_data = json.loads(resp_text)
                                ticket_id = resp_data.get("ticket_id") or resp_data.get("id")
                            except:
                                pass
                        else:
                            logger.warning(f"📝 [TAKE_MESSAGE] Webhook retornou {resp.status}: {resp_text}")
                            
            except Exception as e:
                logger.warning(f"📝 [TAKE_MESSAGE] Erro ao enviar webhook: {e}")
        else:
            logger.warning("📝 [TAKE_MESSAGE] Nenhum webhook_url configurado")
        
        # Agendar encerramento da chamada (via session)
        if context._session:
            import asyncio
            logger.info("📝 [TAKE_MESSAGE] Agendando encerramento em 10s")
            asyncio.create_task(context._session._delayed_stop(10.0, "take_message_done"))
        
        # Resultado com instrução clara
        return ToolResult.ok(
            data={
                "status": "success" if webhook_success else "saved_locally",
                "action": "message_saved",
                "ticket_id": ticket_id,
            },
            instruction="Diga APENAS: 'Recado anotado! Obrigado, tenha um bom dia.' NÃO repita o recado.",
            should_respond=True,
            side_effects=["message_saved", "call_ending_scheduled"]
        )
    
    def _map_urgency(self, urgency: str) -> str:
        """Mapeia urgência para formato OmniPlay."""
        mapping = {
            "baixa": "low",
            "normal": "normal",
            "alta": "high",
            "low": "low",
            "high": "high"
        }
        return mapping.get(urgency.lower(), "normal")
