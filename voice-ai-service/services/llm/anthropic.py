"""
Anthropic Claude LLM Provider.

Supports Claude 3.5 Sonnet, Claude 3 Opus, and other Anthropic models.
Uses the official Anthropic Python SDK with async support.
"""

from typing import List, Optional

from anthropic import AsyncAnthropic

from .base import BaseLLM, Message, ChatResult


class AnthropicLLM(BaseLLM):
    """
    Anthropic Claude LLM provider using the official SDK.
    
    Config:
        api_key: Anthropic API key (required)
        model: Model name (default: claude-sonnet-4-20250514)
        max_tokens: Default max tokens (default: 1024)
    """
    
    provider_name = "anthropic"
    
    def __init__(self, config: dict):
        super().__init__(config)
        self._client: Optional[AsyncAnthropic] = None
    
    def _get_client(self) -> AsyncAnthropic:
        """Get or create Anthropic client."""
        if self._client is None:
            # Fallback para env var ANTHROPIC_API_KEY (auto-detectado pelo client se None)
            api_key = self.config.get("api_key") or None
            self._client = AsyncAnthropic(api_key=api_key)
        return self._client
    
    async def chat(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 500,
        system_prompt: Optional[str] = None,
    ) -> ChatResult:
        """
        Generate chat completion using Anthropic Claude.
        
        Args:
            messages: Conversation history
            temperature: Creativity (0.0 to 1.0)
            max_tokens: Maximum tokens in response
            system_prompt: Optional system prompt
            
        Returns:
            ChatResult with generated response
        """
        client = self._get_client()
        
        # Build messages list (Claude uses different format)
        api_messages = []
        
        for msg in messages:
            # Skip system messages - they go in the system parameter
            if msg.role == "system":
                if not system_prompt:
                    system_prompt = msg.content
                continue
            
            api_messages.append({
                "role": msg.role,
                "content": msg.content,
            })
        
        # Get model from config or use default
        model = self.config.get("model", "claude-sonnet-4-20250514")
        
        # Call Anthropic API
        response = await client.messages.create(
            model=model,
            messages=api_messages,
            system=system_prompt or "",
            max_tokens=max_tokens,
            temperature=temperature,
        )
        
        # Extract response text
        response_text = ""
        for block in response.content:
            if hasattr(block, 'text'):
                response_text += block.text
        
        # Parse action from response
        action, extension, department = self.parse_action(response_text)
        
        # Calculate tokens used
        tokens_used = 0
        if response.usage:
            tokens_used = response.usage.input_tokens + response.usage.output_tokens
        
        return ChatResult(
            text=response_text,
            action=action,
            transfer_extension=extension,
            transfer_department=department,
            tokens_used=tokens_used,
            finish_reason=response.stop_reason,
        )
    
    async def is_available(self) -> bool:
        """Check if Anthropic is available."""
        if not self.config.get("api_key"):
            return False
        
        try:
            # We can't easily test without making a request
            # Just verify we can create a client
            self._get_client()
            return True
        except Exception:
            return False
