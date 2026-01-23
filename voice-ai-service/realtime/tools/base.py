"""
Classes base para Voice AI Tools.

Este módulo define as abstrações principais do sistema de tools:
- VoiceAITool: Classe base para todos os tools
- ToolResult: Resultado de execução de um tool
- ToolContext: Contexto disponível durante execução
- ValidationResult: Resultado de validação de parâmetros
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from enum import Enum
import logging

if TYPE_CHECKING:
    from ..session import RealtimeSession

logger = logging.getLogger(__name__)


class ToolCategory(Enum):
    """Categorias de tools para organização e filtragem."""
    TRANSFER = "transfer"      # Tools de transferência
    MESSAGE = "message"        # Tools de recado/mensagem
    DECISION = "decision"      # Tools de decisão (accept/reject)
    INFO = "info"              # Tools informativos
    CALL_CONTROL = "call"      # Controle de chamada (end_call, hold, etc)
    CUSTOM = "custom"          # Tools customizados


@dataclass
class ValidationResult:
    """Resultado de validação de parâmetros de um tool."""
    valid: bool
    error: Optional[str] = None
    
    @classmethod
    def ok(cls) -> "ValidationResult":
        """Retorna um resultado de validação bem-sucedido."""
        return cls(valid=True)
    
    @classmethod
    def fail(cls, error: str) -> "ValidationResult":
        """Retorna um resultado de validação com erro."""
        return cls(valid=False, error=error)


@dataclass
class ToolResult:
    """
    Resultado de execução de um tool.
    
    Attributes:
        success: Se a execução foi bem-sucedida
        data: Dados retornados pelo tool (vai pro OpenAI como function result)
        error: Mensagem de erro se falhou
        should_respond: Se a IA deve gerar resposta após o resultado
        instruction: Instrução explícita para a IA (ex: "Diga apenas: Recado anotado!")
        side_effects: Efeitos colaterais que ocorreram (para logging/RCA)
    """
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    should_respond: bool = True
    instruction: Optional[str] = None
    side_effects: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Converte para dict (formato OpenAI function result)."""
        if self.success:
            result = self.data or {"status": "success"}
            if self.instruction:
                result["instruction"] = self.instruction
            return result
        else:
            return {
                "status": "error",
                "error": self.error or "Unknown error"
            }
    
    @classmethod
    def ok(cls, data: Optional[Dict[str, Any]] = None, **kwargs) -> "ToolResult":
        """Cria um resultado de sucesso."""
        return cls(success=True, data=data, **kwargs)
    
    @classmethod
    def fail(cls, error: str, **kwargs) -> "ToolResult":
        """Cria um resultado de erro."""
        return cls(success=False, error=error, **kwargs)


@dataclass
class ToolContext:
    """
    Contexto disponível para um tool durante execução.
    
    Encapsula informações da sessão atual sem expor a classe inteira.
    Isso permite:
    - Testes isolados de tools
    - Menor acoplamento
    - Documentação clara do que está disponível
    """
    call_uuid: str
    caller_id: str
    caller_name: Optional[str] = None
    domain_uuid: Optional[str] = None
    secretary_uuid: Optional[str] = None
    company_id: Optional[int] = None
    webhook_url: Optional[str] = None
    
    # Referência à sessão para operações avançadas
    # Use com cuidado - preferir métodos explícitos
    _session: Optional["RealtimeSession"] = field(default=None, repr=False)
    
    # Metadados extras (ex: transcript acumulado)
    extras: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_session(cls, session: "RealtimeSession") -> "ToolContext":
        """Cria contexto a partir de uma sessão."""
        config = session.config
        return cls(
            call_uuid=session.call_uuid,
            caller_id=config.caller_id,
            caller_name=getattr(session, '_caller_name_from_handoff', None),
            domain_uuid=config.domain_uuid,
            secretary_uuid=config.secretary_uuid,
            company_id=config.omniplay_company_id,
            webhook_url=config.omniplay_webhook_url,
            _session=session,
            extras={
                "transcript": getattr(session, '_transcript', ''),
            }
        )


class VoiceAITool(ABC):
    """
    Classe base abstrata para todos os Voice AI Tools.
    
    Para criar um novo tool:
    1. Herde desta classe
    2. Defina name, description, parameters
    3. Implemente execute()
    4. Opcionalmente sobrescreva validate()
    
    Exemplo:
        class MyTool(VoiceAITool):
            name = "my_tool"
            description = "Descrição do tool"
            parameters = {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
            category = ToolCategory.CUSTOM
            
            async def execute(self, context, **kwargs):
                # Lógica aqui
                return ToolResult.ok({"resultado": "valor"})
    """
    
    # Atributos que devem ser definidos pelas subclasses
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    category: ToolCategory = ToolCategory.CUSTOM
    
    # Configurações de comportamento
    requires_response: bool = True  # Se a IA deve responder após execução
    filler_phrases: List[str] = []  # Frases de espera enquanto executa
    
    def __init__(self):
        """Valida que os atributos obrigatórios foram definidos."""
        if not self.name:
            raise ValueError(f"{self.__class__.__name__} deve definir 'name'")
        if not self.description:
            raise ValueError(f"{self.__class__.__name__} deve definir 'description'")
    
    def validate(self, **kwargs) -> ValidationResult:
        """
        Valida os parâmetros antes de executar.
        
        Implementação padrão valida apenas campos 'required'.
        Sobrescreva para validações mais complexas.
        
        Args:
            **kwargs: Parâmetros recebidos do OpenAI
            
        Returns:
            ValidationResult indicando se pode prosseguir
        """
        required = self.parameters.get("required", [])
        for field_name in required:
            if field_name not in kwargs or kwargs[field_name] is None:
                return ValidationResult.fail(f"Parâmetro obrigatório ausente: {field_name}")
            if isinstance(kwargs[field_name], str) and not kwargs[field_name].strip():
                return ValidationResult.fail(f"Parâmetro não pode ser vazio: {field_name}")
        return ValidationResult.ok()
    
    @abstractmethod
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """
        Executa a lógica principal do tool.
        
        Este método é chamado após validação bem-sucedida.
        
        Args:
            context: Contexto da chamada atual
            **kwargs: Parâmetros validados do OpenAI
            
        Returns:
            ToolResult com o resultado da execução
        """
        pass
    
    def to_openai_format(self) -> Dict[str, Any]:
        """
        Converte para formato esperado pelo OpenAI Realtime API.
        
        Returns:
            Dict no formato {"type": "function", "name": ..., ...}
        """
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }
    
    def get_filler(self) -> Optional[str]:
        """
        Retorna uma frase de espera aleatória.
        
        Returns:
            String com frase ou None se não houver fillers
        """
        if not self.filler_phrases:
            return None
        import random
        return random.choice(self.filler_phrases)
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name='{self.name}' category={self.category.value}>"
