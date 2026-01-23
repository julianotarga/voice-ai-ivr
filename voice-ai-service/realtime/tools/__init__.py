"""
Voice AI Tools - Sistema plugável de ferramentas.

Este módulo fornece um sistema de registry para tools que podem ser
chamados pelo OpenAI Realtime API durante uma sessão de voz.

Inspirado em:
- Asterisk-AI-Voice-Agent (https://github.com/hkjarral/Asterisk-AI-Voice-Agent)
- sip-to-ai (https://github.com/aicc2025/sip-to-ai)

Exemplo de uso:
    from realtime.tools import ToolRegistry, VoiceAITool, ToolResult
    
    class MyCustomTool(VoiceAITool):
        name = "my_tool"
        description = "Faz algo customizado"
        parameters = {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "Primeiro parâmetro"}
            },
            "required": ["param1"]
        }
        
        async def execute(self, context, **kwargs):
            result = do_something(kwargs["param1"])
            return ToolResult(success=True, data={"result": result})
    
    # Registrar o tool
    ToolRegistry.register(MyCustomTool())
    
    # Obter tools para OpenAI
    tools = ToolRegistry.to_openai_format()
"""

from .base import (
    VoiceAITool,
    ToolResult,
    ToolContext,
    ValidationResult,
    ToolCategory,
)
from .registry import ToolRegistry
from .integration import (
    execute_with_fallback,
    get_openai_tools_with_defaults,
    get_filler_for_function,
    should_request_response,
)

__all__ = [
    # Base classes
    "VoiceAITool",
    "ToolResult",
    "ToolContext",
    "ValidationResult",
    "ToolCategory",
    # Registry
    "ToolRegistry",
    # Integration helpers
    "execute_with_fallback",
    "get_openai_tools_with_defaults",
    "get_filler_for_function",
    "should_request_response",
]
