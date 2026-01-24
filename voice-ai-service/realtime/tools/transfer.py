"""
Tool de Transferência de Chamada.

Implementa o request_handoff que transfere chamadas para atendentes.
"""

from typing import Any, Dict, List, Optional
from .base import VoiceAITool, ToolCategory, ToolContext, ToolResult, ValidationResult
import logging

logger = logging.getLogger(__name__)


class RequestHandoffTool(VoiceAITool):
    """
    Tool para solicitar transferência de chamada para um atendente.
    
    Este tool:
    1. Valida que o nome do cliente foi coletado
    2. Prepara os dados para transferência
    3. Retorna instrução para a IA falar enquanto transfere
    
    A transferência efetiva é feita pelo TransferManager,
    que é acionado após este tool retornar.
    """
    
    name = "request_handoff"
    description = (
        "Transfere a chamada para atendente. "
        "REGRAS: "
        "1) NOME do cliente é OBRIGATÓRIO (pergunte se não souber). "
        "2) MOTIVO: pode ser inferido do contexto da conversa OU perguntado. "
        "   Ex: cliente perguntou sobre planos e quer contratar → motivo = 'interesse em contratação'. "
        "Antes de transferir, confirme: '[NOME], vou transferir para [DESTINO] para [MOTIVO]. Um momento.'"
    )
    
    parameters = {
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "description": "Para quem/onde transferir (ex: 'suporte', 'vendas', 'Jeni')"
            },
            "reason": {
                "type": "string",
                "description": "Motivo da ligação - use as palavras do cliente OU infira do contexto (ex: 'interesse em contratação', 'dúvida sobre fatura')"
            },
            "caller_name": {
                "type": "string",
                "description": "Nome do cliente (OBRIGATÓRIO - pergunte se não souber)"
            }
        },
        "required": ["destination", "caller_name"]
    }
    
    category = ToolCategory.TRANSFER
    requires_response = False  # Não gerar resposta - já enviamos instrução
    filler_phrases = []  # Sem filler - fala personalizada
    
    # Nomes inválidos que indicam que a IA não perguntou
    INVALID_NAMES = {
        "não informado", "desconhecido", "cliente", "usuario", "usuário",
        "pessoa", "caller", "ligante", "chamador", "não sei", "nao sei",
        "unknown", "anônimo", "anonimo"
    }
    
    def validate(self, **kwargs) -> ValidationResult:
        """Valida parâmetros, especialmente o nome do cliente."""
        # Validação padrão primeiro
        base_validation = super().validate(**kwargs)
        if not base_validation.valid:
            return base_validation
        
        # Validar nome do cliente
        caller_name = kwargs.get("caller_name", "").strip().lower()
        if not caller_name:
            return ValidationResult.fail("Nome do cliente é obrigatório")
        
        if caller_name in self.INVALID_NAMES:
            return ValidationResult.fail(
                f"Nome '{kwargs.get('caller_name')}' não é válido - pergunte o nome do cliente"
            )
        
        # Nome muito curto (1 letra)
        if len(caller_name) < 2:
            return ValidationResult.fail("Nome do cliente muito curto")
        
        return ValidationResult.ok()
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """
        Processa solicitação de transferência.
        
        Nota: Este tool NÃO executa a transferência diretamente.
        Ele prepara os dados e retorna uma instrução para a IA.
        O TransferManager é acionado pelo session.py após este retorno.
        """
        destination = kwargs.get("destination", "qualquer atendente")
        reason = kwargs.get("reason", "solicitação do cliente")
        caller_name = kwargs.get("caller_name", "")
        
        logger.info(
            "🔄 [HANDOFF] request_handoff tool executado",
            extra={
                "call_uuid": context.call_uuid,
                "destination": destination,
                "reason": reason,
                "caller_name": caller_name
            }
        )
        
        # Verificar se já há transferência em andamento (via session)
        session = context._session
        if session:
            if getattr(session, '_transfer_in_progress', False):
                logger.warning("🔄 [HANDOFF] Transferência já em progresso - ignorando")
                return ToolResult.ok(
                    data={"status": "already_in_progress"},
                    should_respond=False
                )
            
            if getattr(session, '_handoff_pending', False):
                logger.warning("🔄 [HANDOFF] Handoff já pendente - ignorando")
                return ToolResult.ok(
                    data={"status": "already_pending"},
                    should_respond=False
                )
            
            # Armazenar nome na sessão para uso pelo TransferManager
            session._caller_name_from_handoff = caller_name
        
        # Construir fala de transição
        spoken_destination = self._format_destination(destination)
        spoken_message = f"Um momento {caller_name}, vou transferir para {spoken_destination}."
        
        return ToolResult.ok(
            data={
                "status": "initiating_transfer",
                "destination": destination,
                "reason": reason,
                "caller_name": caller_name,
                "spoken_message": spoken_message
            },
            should_respond=False,
            instruction=f"[SISTEMA] Diga APENAS: '{spoken_message}' - nada mais.",
            side_effects=["transfer_initiated"]
        )
    
    def _format_destination(self, destination: str) -> str:
        """
        Formata destino para fala natural.
        
        Ex: "suporte_tecnico" -> "suporte técnico"
        """
        destination = destination.lower().strip()
        
        # Mapeamento de destinos comuns
        mappings = {
            "suporte": "o suporte",
            "suporte_tecnico": "o suporte técnico",
            "vendas": "vendas",
            "financeiro": "o financeiro",
            "comercial": "o comercial",
            "atendimento": "o atendimento",
        }
        
        if destination in mappings:
            return mappings[destination]
        
        # Se parece ser um nome próprio (começa com maiúscula ou é curto)
        if len(destination) < 15 and not destination.startswith("setor"):
            return destination.title()  # "jeni" -> "Jeni"
        
        return destination
