"""
Deepgram STT Provider.

Uses Deepgram Nova for fast, accurate speech recognition.
Excellent for real-time and batch transcription.
"""

import os
from typing import Optional

import httpx

from .base import BaseSTT, TranscriptionResult


class DeepgramSTT(BaseSTT):
    """
    Deepgram Nova STT provider.
    
    Config:
        api_key: Deepgram API key (required)
        model: Model to use (default: nova-2)
        language: Language code (default: pt)
        smart_format: Enable smart formatting (default: True)
        punctuate: Enable punctuation (default: True)
    """
    
    provider_name = "deepgram"
    
    BASE_URL = "https://api.deepgram.com/v1/listen"
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env var DEEPGRAM_API_KEY
        self.api_key = config.get("api_key") or os.environ.get("DEEPGRAM_API_KEY", "")
        self.model = config.get("model", "nova-2")
        self.smart_format = config.get("smart_format", True)
        self.punctuate = config.get("punctuate", True)
    
    async def transcribe(
        self,
        audio_file: str,
        language: str = "pt",
    ) -> TranscriptionResult:
        """
        Transcribe audio using Deepgram.
        
        Args:
            audio_file: Path to audio file
            language: Language code (pt, en, es, etc.)
            
        Returns:
            TranscriptionResult with transcribed text
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"Audio file not found: {audio_file}")
        
        if not self.api_key:
            raise ValueError("Deepgram API key is required")
        
        # Detect content type
        ext = os.path.splitext(audio_file)[1].lower()
        content_type_map = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
            ".m4a": "audio/mp4",
            ".webm": "audio/webm",
        }
        content_type = content_type_map.get(ext, "audio/wav")
        
        # Build query parameters
        params = {
            "model": self.model,
            "language": language,
            "smart_format": str(self.smart_format).lower(),
            "punctuate": str(self.punctuate).lower(),
        }
        
        # Read audio file
        with open(audio_file, "rb") as f:
            audio_data = f.read()
        
        # Call Deepgram API
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self.BASE_URL,
                params=params,
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": content_type,
                },
                content=audio_data,
            )
            response.raise_for_status()
            data = response.json()
        
        # Extract results
        results = data.get("results", {})
        channels = results.get("channels", [])
        
        if channels:
            alternatives = channels[0].get("alternatives", [])
            if alternatives:
                alt = alternatives[0]
                text = alt.get("transcript", "")
                confidence = alt.get("confidence", None)
                
                # Get duration from metadata
                metadata = results.get("metadata", {})
                duration = metadata.get("duration", 0)
                
                return TranscriptionResult(
                    text=text.strip(),
                    language=language,
                    confidence=confidence,
                    duration_ms=int(duration * 1000),
                )
        
        return TranscriptionResult(
            text="",
            language=language,
            confidence=0.0,
            duration_ms=0,
        )
    
    async def is_available(self) -> bool:
        """Check if Deepgram is available."""
        if not self.api_key:
            return False
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.deepgram.com/v1/projects",
                    headers={"Authorization": f"Token {self.api_key}"},
                )
                return response.status_code == 200
        except Exception:
            return False
