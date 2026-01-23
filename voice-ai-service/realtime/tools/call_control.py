"""
Tools de Controle de Chamada.

Implementa end_call e outros controles de chamada.
"""

from typing import Any, Dict, Optional
from .base import VoiceAITool, ToolCategory, ToolContext, ToolResult, ValidationResult
import logging

logger = logging.getLogger(__name__)


class EndCallTool(VoiceAITool):
    """
    Tool para encerrar a chamada de forma graciosa.
    
    Usado quando:
    - Cliente indica que não precisa de mais nada
    - Conversa chegou ao fim natural
    - Cliente quer desligar
    """
    
    name = "end_call"
    description = (
        "Encerra a chamada de forma educada. "
        "Use quando o cliente: "
        "1) Disser 'obrigado, é só isso' ou similar "
        "2) Indicar que não precisa de mais ajuda "
        "3) Pedir para desligar "
        "SEMPRE agradeça antes de encerrar."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "farewell_reason": {
                "type": "string",
                "description": "Motivo/contexto do encerramento (ex: 'cliente satisfeito', 'recado anotado')"
            }
        },
        "required": []
    }
    
    category = ToolCategory.CALL_CONTROL
    requires_response = False
    filler_phrases = []
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """
        Agenda encerramento gracioso da chamada.
        """
        farewell_reason = kwargs.get("farewell_reason", "encerramento normal")
        
        logger.info(
            "📞 [END_CALL] Encerrando chamada",
            extra={
                "call_uuid": context.call_uuid,
                "reason": farewell_reason
            }
        )
        
        # Agendar encerramento via session
        session = context._session
        if session:
            session._ending_call = True
            import asyncio
            asyncio.create_task(session._delayed_stop(2.0, "function_end"))
        
        return ToolResult.ok(
            data={
                "status": "ending",
                "reason": farewell_reason
            },
            should_respond=False,
            side_effects=["call_ending"]
        )


class GetBusinessInfoTool(VoiceAITool):
    """
    Tool para obter informações do negócio.
    
    Usado para responder perguntas sobre a empresa.
    """
    
    name = "get_business_info"
    description = (
        "Obtém informações sobre a empresa. "
        "Use para responder perguntas sobre: serviços, horários, localização, contato."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": ["servicos", "horarios", "localizacao", "contato", "geral"],
                "description": "Tópico da informação desejada"
            }
        },
        "required": ["topic"]
    }
    
    category = ToolCategory.INFO
    requires_response = True
    filler_phrases = ["Um momento...", "Deixa eu verificar..."]
    
    # Informações padrão - podem ser customizadas por empresa
    DEFAULT_INFO = {
        "servicos": "Oferecemos soluções de telefonia fixa, móvel, internet fibra óptica e integração WhatsApp Business.",
        "horarios": "Nosso horário de atendimento é de segunda a sexta, das 8h às 18h.",
        "localizacao": "Estamos localizados em São Paulo. Para endereço completo, consulte nosso site.",
        "contato": "Nosso WhatsApp é o mesmo número desta ligação. Email: contato@empresa.com.br",
        "geral": "Somos uma empresa de tecnologia focada em soluções de comunicação."
    }
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """
        Retorna informações sobre a empresa.
        """
        topic = kwargs.get("topic", "geral")
        
        logger.info(
            "📋 [GET_BUSINESS_INFO] Buscando info",
            extra={
                "call_uuid": context.call_uuid,
                "topic": topic
            }
        )
        
        # TODO: Buscar info customizada por empresa do banco
        # Por enquanto usa defaults
        info = self.DEFAULT_INFO.get(topic, self.DEFAULT_INFO["geral"])
        
        return ToolResult.ok(
            data={
                "status": "success",
                "info": info,
                "topic": topic
            },
            should_respond=True
        )
