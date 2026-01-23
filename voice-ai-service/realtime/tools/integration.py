"""
Integração do ToolRegistry com RealtimeSession.

Este módulo fornece funções de compatibilidade que permitem
usar o novo sistema de tools sem quebrar o código existente.

A migração é gradual:
1. Inicializar ToolRegistry no startup do servidor
2. Usar execute_with_fallback() no session.py
3. Gradualmente migrar tools inline para o registry
"""

from typing import Any, Dict, Optional, Callable, Awaitable, TYPE_CHECKING
from .registry import ToolRegistry
from .base import ToolContext, ToolResult
import logging

if TYPE_CHECKING:
    from ..session import RealtimeSession

logger = logging.getLogger(__name__)


async def execute_with_fallback(
    session: "RealtimeSession",
    function_name: str,
    function_args: Dict[str, Any],
    legacy_executor: Optional[Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None
) -> Dict[str, Any]:
    """
    Executa uma função usando o ToolRegistry com fallback para código legado.
    
    Esta função permite migração gradual:
    - Se o tool está no registry, usa o novo sistema
    - Se não está, usa o executor legado (código inline do session.py)
    
    Args:
        session: Sessão de voz ativa
        function_name: Nome da função/tool
        function_args: Argumentos da função
        legacy_executor: Função legada para fallback (ex: session._execute_function)
        
    Returns:
        Dict com resultado da execução (formato OpenAI)
    """
    # Garantir que tools estão registrados
    ToolRegistry.ensure_initialized()
    
    # Tentar usar o registry primeiro
    if ToolRegistry.has(function_name):
        context = ToolContext.from_session(session)
        result = await ToolRegistry.execute(function_name, context, **function_args)
        return result.to_dict()
    
    # Fallback para código legado
    if legacy_executor:
        logger.debug(f"Tool {function_name} não encontrado no registry - usando fallback legado")
        return await legacy_executor(function_name, function_args)
    
    # Nenhum executor disponível
    logger.warning(f"Tool {function_name} não encontrado e sem fallback")
    return {"status": "error", "error": f"Tool não encontrado: {function_name}"}


def get_openai_tools_with_defaults(custom_tools: list = None) -> list:
    """
    Retorna lista de tools para OpenAI com defaults garantidos.
    
    Combina:
    1. Tools do ToolRegistry
    2. Tools customizados passados
    
    Args:
        custom_tools: Lista de tools customizados (formato OpenAI)
        
    Returns:
        Lista completa de tools para session.update
    """
    ToolRegistry.ensure_initialized()
    
    # Começar com tools do registry
    tools = ToolRegistry.to_openai_format()
    tool_names = {t["name"] for t in tools}
    
    # Adicionar tools customizados que não existem no registry
    if custom_tools:
        for tool in custom_tools:
            if isinstance(tool, dict) and tool.get("name") not in tool_names:
                tools.append(tool)
                tool_names.add(tool.get("name"))
    
    return tools


def get_filler_for_function(function_name: str, legacy_fillers: Dict[str, list] = None) -> Optional[str]:
    """
    Obtém frase de espera para uma função.
    
    Args:
        function_name: Nome da função
        legacy_fillers: Dict de fillers legado para fallback
        
    Returns:
        Frase de filler ou None
    """
    # Tentar registry primeiro
    filler = ToolRegistry.get_filler(function_name)
    if filler:
        return filler
    
    # Fallback para fillers legados
    if legacy_fillers and function_name in legacy_fillers:
        fillers = legacy_fillers[function_name]
        if fillers:
            import random
            return random.choice(fillers)
    
    return None


def should_request_response(function_name: str) -> bool:
    """
    Verifica se uma função deve solicitar resposta da IA após execução.
    
    Args:
        function_name: Nome da função
        
    Returns:
        True se deve solicitar resposta, False caso contrário
    """
    tool = ToolRegistry.get(function_name)
    if tool:
        return tool.requires_response
    
    # Defaults para funções conhecidas
    skip_response = {"request_handoff", "end_call", "accept_transfer", "reject_transfer"}
    return function_name not in skip_response
