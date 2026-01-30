"""
Tool Registry - Registro centralizado de Voice AI Tools.

O ToolRegistry é um singleton que gerencia todos os tools disponíveis
para sessões de Voice AI. Ele permite:
- Registrar novos tools
- Obter tools por nome
- Listar todos os tools
- Exportar para formato OpenAI
"""

from typing import Dict, List, Optional, Set, TYPE_CHECKING
from .base import VoiceAITool, ToolCategory, ToolContext, ToolResult, ValidationResult
import logging

if TYPE_CHECKING:
    from ..session import RealtimeSession

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Registro centralizado de Voice AI Tools (Singleton).
    
    Uso:
        # Registrar um tool
        ToolRegistry.register(MyTool())
        
        # Obter um tool
        tool = ToolRegistry.get("my_tool")
        
        # Executar um tool
        result = await ToolRegistry.execute("my_tool", context, **kwargs)
        
        # Listar para OpenAI
        tools = ToolRegistry.to_openai_format()
    """
    
    _instance: Optional["ToolRegistry"] = None
    # Inicializar diretamente na classe para que @classmethod funcione
    _tools: Dict[str, VoiceAITool] = {}
    _initialized: bool = False
    
    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def register(cls, tool: VoiceAITool) -> None:
        """
        Registra um tool no registry.
        
        Args:
            tool: Instância de VoiceAITool a registrar
            
        Raises:
            ValueError: Se um tool com mesmo nome já existe
        """
        if tool.name in cls._tools:
            logger.warning(f"Tool '{tool.name}' já registrado - sobrescrevendo")
        
        cls._tools[tool.name] = tool
        logger.info(f"✅ Tool registrado: {tool.name} ({tool.category.value})")
    
    @classmethod
    def unregister(cls, name: str) -> bool:
        """
        Remove um tool do registry.
        
        Args:
            name: Nome do tool a remover
            
        Returns:
            True se removeu, False se não existia
        """
        if name in cls._tools:
            del cls._tools[name]
            logger.info(f"❌ Tool removido: {name}")
            return True
        return False
    
    @classmethod
    def get(cls, name: str) -> Optional[VoiceAITool]:
        """
        Obtém um tool pelo nome.
        
        Args:
            name: Nome do tool
            
        Returns:
            Instância do tool ou None se não existe
        """
        return cls._tools.get(name)
    
    @classmethod
    def has(cls, name: str) -> bool:
        """Verifica se um tool está registrado."""
        return name in cls._tools
    
    @classmethod
    def list_all(cls) -> List[VoiceAITool]:
        """Retorna lista de todos os tools registrados."""
        return list(cls._tools.values())
    
    @classmethod
    def list_names(cls) -> List[str]:
        """Retorna lista de nomes de todos os tools."""
        return list(cls._tools.keys())
    
    @classmethod
    def list_by_category(cls, category: ToolCategory) -> List[VoiceAITool]:
        """Retorna tools de uma categoria específica."""
        return [t for t in cls._tools.values() if t.category == category]
    
    @classmethod
    def to_openai_format(cls, filter_names: Optional[Set[str]] = None) -> List[Dict]:
        """
        Exporta tools para formato OpenAI Realtime API.
        
        Args:
            filter_names: Se fornecido, inclui apenas estes tools
            
        Returns:
            Lista de dicts no formato OpenAI function definition
        """
        result = []
        for tool in cls._tools.values():
            if filter_names is None or tool.name in filter_names:
                result.append(tool.to_openai_format())
        return result
    
    @classmethod
    async def execute(
        cls, 
        name: str, 
        context: ToolContext, 
        **kwargs
    ) -> ToolResult:
        """
        Executa um tool pelo nome.
        
        Este método:
        1. Localiza o tool
        2. Valida os parâmetros
        3. Executa o tool
        4. Retorna o resultado
        
        Args:
            name: Nome do tool
            context: Contexto da chamada
            **kwargs: Parâmetros para o tool
            
        Returns:
            ToolResult com o resultado da execução
        """
        tool = cls.get(name)
        
        if tool is None:
            logger.warning(f"Tool não encontrado: {name}")
            return ToolResult.fail(f"Tool não encontrado: {name}")
        
        # Validar parâmetros
        validation = tool.validate(**kwargs)
        if not validation.valid:
            logger.warning(f"Validação falhou para {name}: {validation.error}")
            return ToolResult.fail(
                f"Parâmetros inválidos: {validation.error}",
                should_respond=True
            )
        
        # Executar
        try:
            logger.info(f"🔧 Executando tool: {name}", extra={
                "call_uuid": context.call_uuid,
                "tool_name": name,
                "args": kwargs
            })
            
            result = await tool.execute(context, **kwargs)
            
            # Herdar configuração de resposta do tool se não foi explicitamente setado
            # O ToolResult.ok() default é should_respond=True, então verificamos
            # se o tool deve sobrescrever esse default
            if not tool.requires_response and result.should_respond:
                result.should_respond = False
            
            logger.info(f"🔧 Tool {name} concluído: success={result.success}", extra={
                "call_uuid": context.call_uuid,
                "tool_name": name,
                "success": result.success,
                "side_effects": result.side_effects
            })
            
            return result
            
        except Exception as e:
            logger.exception(f"Erro ao executar tool {name}: {e}")
            return ToolResult.fail(f"Erro interno: {str(e)}")
    
    @classmethod
    def get_filler(cls, name: str) -> Optional[str]:
        """
        Obtém frase de espera para um tool.
        
        Args:
            name: Nome do tool
            
        Returns:
            Frase de filler ou None
        """
        tool = cls.get(name)
        if tool:
            return tool.get_filler()
        return None
    
    @classmethod
    def clear(cls) -> None:
        """
        Remove todos os tools registrados.
        
        Use apenas para testes!
        """
        cls._tools.clear()
        cls._initialized = False
        logger.warning("⚠️ ToolRegistry limpo - todos os tools removidos")
    
    @classmethod
    def initialize_default_tools(cls) -> None:
        """
        Registra os tools padrão do sistema.
        
        Chamado automaticamente na inicialização do servidor.
        """
        if cls._initialized:
            logger.debug("ToolRegistry já inicializado")
            return
        
        # Importar e registrar tools padrão
        try:
            from .transfer import RequestHandoffTool
            from .message import TakeMessageTool
            from .decision import AcceptTransferTool, RejectTransferTool
            from .call_control import EndCallTool, GetBusinessInfoTool
            from .callback import (
                AcceptCallbackTool,
                ProvideCallbackNumberTool,
                ConfirmCallbackNumberTool,
                ScheduleCallbackTool,
            )
            
            cls.register(RequestHandoffTool())
            cls.register(TakeMessageTool())
            cls.register(AcceptTransferTool())
            cls.register(RejectTransferTool())
            cls.register(EndCallTool())
            cls.register(GetBusinessInfoTool())
            
            # Tools de Callback (retorno de ligação)
            cls.register(AcceptCallbackTool())
            cls.register(ProvideCallbackNumberTool())
            cls.register(ConfirmCallbackNumberTool())
            cls.register(ScheduleCallbackTool())
            
            cls._initialized = True
            logger.info(f"✅ ToolRegistry inicializado com {len(cls._tools)} tools")
            
        except ImportError as e:
            logger.warning(f"Alguns tools não puderam ser importados: {e}")
            # Não falha - permite uso parcial
    
    @classmethod
    def ensure_initialized(cls) -> None:
        """Garante que os tools padrão estejam registrados."""
        if not cls._initialized:
            cls.initialize_default_tools()
