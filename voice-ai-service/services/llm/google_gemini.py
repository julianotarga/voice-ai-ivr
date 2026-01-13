"""
Google Gemini LLM Provider.

Uses Google's Gemini models for chat completions.
"""

from typing import List, Optional

from .base import BaseLLM, Message, ChatResult


class GoogleGeminiLLM(BaseLLM):
    """
    Google Gemini LLM provider.
    
    Config:
        api_key: Google AI API key (required)
        model: Model name (default: gemini-1.5-flash)
    """
    
    provider_name = "google_gemini"
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env var GOOGLE_API_KEY
        self.api_key = config.get("api_key") or os.environ.get("GOOGLE_API_KEY", "")
        self.model = config.get("model", "gemini-1.5-flash")
        self._client = None
    
    def _get_client(self):
        """Get or create Gemini client."""
        if self._client is None:
            try:
                import google.generativeai as genai
            except ImportError:
                raise ImportError(
                    "google-generativeai not installed. "
                    "Install with: pip install google-generativeai"
                )
            
            genai.configure(api_key=self.api_key)
            self._client = genai.GenerativeModel(self.model)
        
        return self._client
    
    async def chat(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 500,
        system_prompt: Optional[str] = None,
    ) -> ChatResult:
        """
        Generate chat completion using Google Gemini.
        
        Args:
            messages: Conversation history
            temperature: Creativity (0.0 to 1.0)
            max_tokens: Maximum tokens in response
            system_prompt: Optional system prompt
            
        Returns:
            ChatResult with generated response
        """
        import asyncio
        
        model = self._get_client()
        
        # Build conversation for Gemini format
        history = []
        
        # Add system prompt as first user message if provided
        if system_prompt:
            history.append({
                "role": "user",
                "parts": [f"[System Instruction]\n{system_prompt}"],
            })
            history.append({
                "role": "model",
                "parts": ["Entendido. Vou seguir essas instruções."],
            })
        
        # Add conversation messages
        for msg in messages:
            role = "model" if msg.role == "assistant" else "user"
            history.append({
                "role": role,
                "parts": [msg.content],
            })
        
        # Create chat and get response
        loop = asyncio.get_event_loop()
        
        chat = model.start_chat(history=history[:-1] if history else [])
        
        # Get the last user message
        last_message = messages[-1].content if messages else ""
        
        response = await loop.run_in_executor(
            None,
            lambda: chat.send_message(
                last_message,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                },
            ),
        )
        
        response_text = response.text
        
        # Parse action from response
        action, extension, department = self.parse_action(response_text)
        
        # Estimate tokens (Gemini doesn't always provide token counts)
        tokens_used = 0
        if hasattr(response, 'usage_metadata'):
            tokens_used = getattr(response.usage_metadata, 'total_token_count', 0)
        
        return ChatResult(
            text=response_text,
            action=action,
            transfer_extension=extension,
            transfer_department=department,
            tokens_used=tokens_used,
            finish_reason="stop",
        )
    
    async def is_available(self) -> bool:
        """Check if Google Gemini is available."""
        if not self.api_key:
            return False
        
        try:
            import google.generativeai as genai
            return True
        except ImportError:
            return False
