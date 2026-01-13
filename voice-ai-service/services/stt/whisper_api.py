"""
OpenAI Whisper API STT Provider.

Uses OpenAI's Whisper API for cloud-based transcription.
High accuracy but requires API key and has cost per minute.
"""

import os
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from .base import BaseSTT, TranscriptionResult


class OpenAIWhisperSTT(BaseSTT):
    """
    OpenAI Whisper API STT provider.
    
    Config:
        api_key: OpenAI API key (required)
        model: Whisper model (default: whisper-1)
        language: Language code (default: pt for Portuguese)
        response_format: Output format (default: verbose_json)
    """
    
    provider_name = "whisper_api"
    
    def __init__(self, config: dict):
        super().__init__(config)
        self._client: Optional[AsyncOpenAI] = None
    
    def _get_client(self) -> AsyncOpenAI:
        """Get or create OpenAI client."""
        if self._client is None:
            # Fallback para env var OPENAI_API_KEY (auto-detectado pelo client se None)
            api_key = self.config.get("api_key") or None
            self._client = AsyncOpenAI(api_key=api_key)
        return self._client
    
    async def transcribe(
        self,
        audio_file: str,
        language: str = "pt",
    ) -> TranscriptionResult:
        """
        Transcribe audio using OpenAI Whisper API.
        
        Args:
            audio_file: Path to audio file
            language: Language code (pt, en, es, etc.)
            
        Returns:
            TranscriptionResult with transcribed text
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"Audio file not found: {audio_file}")
        
        client = self._get_client()
        
        # Get config options
        model = self.config.get("model", "whisper-1")
        response_format = self.config.get("response_format", "verbose_json")
        
        # Open file and transcribe
        with open(audio_file, "rb") as f:
            transcription = await client.audio.transcriptions.create(
                model=model,
                file=f,
                language=language,
                response_format=response_format,
                temperature=0.0,  # More deterministic
            )
        
        # Extract results based on format
        if response_format == "verbose_json":
            text = transcription.text
            detected_language = getattr(transcription, 'language', language)
            duration = getattr(transcription, 'duration', 0)
        else:
            # Simple text response
            text = transcription if isinstance(transcription, str) else transcription.text
            detected_language = language
            duration = 0
        
        return TranscriptionResult(
            text=text.strip(),
            language=detected_language,
            confidence=None,  # Whisper API doesn't return confidence
            duration_ms=int(duration * 1000) if duration else 0,
        )
    
    async def is_available(self) -> bool:
        """Check if OpenAI Whisper API is available."""
        if not self.config.get("api_key"):
            return False
        
        try:
            client = self._get_client()
            # Can't easily test Whisper without an audio file
            # Just verify we can create a client
            await client.models.list()
            return True
        except Exception:
            return False
