"""
Groq LLM Provider.

Supports Llama 3.1 70B, Mixtral 8x7B, and other ultra-fast models.
Groq provides extremely low latency inference.
"""

from typing import List, Optional

from groq import AsyncGroq

from .base import BaseLLM, Message, ChatResult


class GroqLLM(BaseLLM):
    """
    Groq LLM provider for ultra-fast inference.
    
    Config:
        api_key: Groq API key (required)
        model: Model name (default: llama-3.1-70b-versatile)
    """
    
    provider_name = "groq"
    
    def __init__(self, config: dict):
        super().__init__(config)
        self._client: Optional[AsyncGroq] = None
    
    def _get_client(self) -> AsyncGroq:
        """Get or create Groq client."""
        if self._client is None:
            # Fallback para env var GROQ_API_KEY (auto-detectado pelo client se None)
            api_key = self.config.get("api_key") or None
            self._client = AsyncGroq(api_key=api_key)
        return self._client
    
    async def chat(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 500,
        system_prompt: Optional[str] = None,
    ) -> ChatResult:
        """
        Generate chat completion using Groq.
        
        Groq uses OpenAI-compatible API format.
        
        Args:
            messages: Conversation history
            temperature: Creativity (0.0 to 1.0)
            max_tokens: Maximum tokens in response
            system_prompt: Optional system prompt to prepend
            
        Returns:
            ChatResult with generated response
        """
        client = self._get_client()
        
        # Build messages list (OpenAI-compatible format)
        api_messages = []
        
        # Add system prompt if provided
        if system_prompt:
            api_messages.append({
                "role": "system",
                "content": system_prompt,
            })
        
        # Add conversation messages
        for msg in messages:
            api_messages.append({
                "role": msg.role,
                "content": msg.content,
            })
        
        # Get model from config or use default
        model = self.config.get("model", "llama-3.1-70b-versatile")
        
        # Call Groq API
        response = await client.chat.completions.create(
            model=model,
            messages=api_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        
        # Extract response
        choice = response.choices[0]
        response_text = choice.message.content or ""
        
        # Parse action from response
        action, extension, department = self.parse_action(response_text)
        
        # Calculate tokens used
        tokens_used = 0
        if response.usage:
            tokens_used = response.usage.total_tokens
        
        return ChatResult(
            text=response_text,
            action=action,
            transfer_extension=extension,
            transfer_department=department,
            tokens_used=tokens_used,
            finish_reason=choice.finish_reason,
        )
    
    async def is_available(self) -> bool:
        """Check if Groq is available."""
        if not self.config.get("api_key"):
            return False
        
        try:
            client = self._get_client()
            # Simple test - list models
            await client.models.list()
            return True
        except Exception:
            return False
