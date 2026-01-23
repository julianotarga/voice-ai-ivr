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
        "Encerra a chamada. "
        "REGRA CRÍTICA: Você DEVE FALAR uma despedida educada ANTES de chamar esta função! "
        "Exemplos de despedida: 'Obrigada por ligar, até logo!', 'Foi um prazer ajudar, até logo!' "
        "NUNCA chame end_call sem ANTES ter falado a despedida em voz alta. "
        "A despedida deve ser a ÚLTIMA coisa que você fala antes de chamar esta função."
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
    Os dados são configurados no FusionPBX (Informações da Empresa).
    """
    
    name = "get_business_info"
    description = (
        "Obtém informações sobre a empresa. "
        "SEMPRE use esta função para responder perguntas sobre: "
        "serviços oferecidos, preços/valores/planos, promoções/descontos, "
        "horários de atendimento, localização/endereço, formas de contato, "
        "ou informações gerais sobre a empresa. "
        "NÃO invente informações - use sempre esta função para obter dados corretos."
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": ["servicos", "precos", "promocoes", "horarios", "localizacao", "contato", "sobre", "geral"],
                "description": "Tópico: servicos, precos (valores/planos), promocoes (descontos), horarios, localizacao, contato, sobre (a empresa), geral"
            }
        },
        "required": ["topic"]
    }
    
    category = ToolCategory.INFO
    requires_response = True
    filler_phrases = ["Um momento...", "Deixa eu verificar..."]
    
    # Informações padrão - sobrescritas pelos dados do banco de dados
    DEFAULT_INFO = {
        "servicos": "Consulte nosso site para informações sobre serviços.",
        "precos": "Os preços variam conforme o serviço. Posso anotar sua dúvida para retorno.",
        "promocoes": "Consulte nosso site ou fale com um atendente para saber sobre promoções.",
        "horarios": "Entre em contato para verificar nossos horários de atendimento.",
        "localizacao": "Consulte nosso site para informações de localização.",
        "contato": "Ligue para este número ou acesse nosso site.",
        "sobre": "Somos uma empresa focada em soluções de qualidade.",
        "geral": "Posso anotar sua dúvida para que um atendente retorne com mais detalhes."
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
