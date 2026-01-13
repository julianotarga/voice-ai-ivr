"""
Azure OpenAI LLM Provider.

Uses Azure OpenAI Service for enterprise-grade GPT models.
"""

from typing import List, Optional

from openai import AsyncAzureOpenAI

from .base import BaseLLM, Message, ChatResult


class AzureOpenAILLM(BaseLLM):
    """
    Azure OpenAI LLM provider.
    
    Config:
        api_key: Azure OpenAI API key (required)
        endpoint: Azure OpenAI endpoint URL (required)
        deployment_name: Deployment name (required)
        api_version: API version (default: 2024-02-15-preview)
    """
    
    provider_name = "azure_openai"
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        self._client: Optional[AsyncAzureOpenAI] = None
        
        # Fallback para env vars Azure
        self.api_key = config.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY", "")
        self.endpoint = config.get("endpoint") or config.get("azure_endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        self.deployment_name = config.get("deployment_name") or config.get("deployment")
        self.api_version = config.get("api_version", "2024-02-15-preview")
    
    def _get_client(self) -> AsyncAzureOpenAI:
        """Get or create Azure OpenAI client."""
        if self._client is None:
            self._client = AsyncAzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.endpoint,
                api_version=self.api_version,
            )
        return self._client
    
    async def chat(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 500,
        system_prompt: Optional[str] = None,
    ) -> ChatResult:
        """
        Generate chat completion using Azure OpenAI.
        
        Args:
            messages: Conversation history
            temperature: Creativity (0.0 to 1.0)
            max_tokens: Maximum tokens in response
            system_prompt: Optional system prompt to prepend
            
        Returns:
            ChatResult with generated response
        """
        client = self._get_client()
        
        # Build messages list
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
        
        # Call Azure OpenAI API
        response = await client.chat.completions.create(
            model=self.deployment_name,
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
        """Check if Azure OpenAI is available."""
        if not self.api_key or not self.endpoint or not self.deployment_name:
            return False
        
        try:
            client = self._get_client()
            # Simple test
            return True
        except Exception:
            return False
