"""
OpenAI TTS Provider.

Uses OpenAI's text-to-speech API for high-quality voice synthesis.
Supports multiple voices: alloy, echo, fable, onyx, nova, shimmer.
"""

import os
import uuid
from pathlib import Path
from typing import Optional, List

from openai import AsyncOpenAI

from .base import BaseTTS, SynthesisResult, VoiceInfo
from config.settings import settings


class OpenAITTS(BaseTTS):
    """
    OpenAI TTS provider.
    
    Config:
        api_key: OpenAI API key (required)
        model: TTS model (tts-1 or tts-1-hd, default: tts-1)
        voice: Default voice (alloy, echo, fable, onyx, nova, shimmer)
        speed: Speech speed 0.25 to 4.0 (default: 1.0)
    """
    
    provider_name = "openai_tts"
    
    # Available voices
    VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    
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
    
    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        output_path: Optional[str] = None,
    ) -> SynthesisResult:
        """
        Synthesize text to speech using OpenAI TTS.
        
        Args:
            text: Text to synthesize
            voice_id: Voice to use (alloy, echo, fable, onyx, nova, shimmer)
            speed: Speech speed (0.25 to 4.0)
            output_path: Path to save audio (optional)
            
        Returns:
            SynthesisResult with path to audio file
        """
        client = self._get_client()
        
        # Get config options
        model = self.config.get("model", "tts-1")
        voice = voice_id or self.config.get("voice", "nova")
        speed = speed or self.config.get("speed", 1.0)
        
        # Validate voice
        if voice not in self.VOICES:
            voice = "nova"  # Default fallback
        
        # Clamp speed to valid range
        speed = max(0.25, min(4.0, speed))
        
        # Generate output path if not provided
        if not output_path:
            output_path = str(settings.TEMP_DIR / f"tts_{uuid.uuid4().hex}.mp3")
        
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Generate speech with streaming
        async with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text,
            speed=speed,
            response_format="mp3",
        ) as response:
            # Stream to file
            with open(output_path, "wb") as f:
                async for chunk in response.iter_bytes():
                    f.write(chunk)
        
        # Get file info for duration estimation
        # OpenAI TTS doesn't return duration, so we estimate
        file_size = os.path.getsize(output_path)
        # Rough estimate: MP3 at 128kbps = 16KB/sec
        duration_ms = int((file_size / 16000) * 1000)
        
        return SynthesisResult(
            audio_file=output_path,
            duration_ms=duration_ms,
            format="mp3",
        )
    
    async def list_voices(self, language: str = "pt-BR") -> List[VoiceInfo]:
        """
        List available voices.
        
        OpenAI TTS voices are multilingual and work well with Portuguese.
        """
        return [
            VoiceInfo(
                voice_id="alloy",
                name="Alloy",
                language="multilingual",
                gender="neutral",
            ),
            VoiceInfo(
                voice_id="echo",
                name="Echo",
                language="multilingual",
                gender="male",
            ),
            VoiceInfo(
                voice_id="fable",
                name="Fable",
                language="multilingual",
                gender="neutral",
            ),
            VoiceInfo(
                voice_id="onyx",
                name="Onyx",
                language="multilingual",
                gender="male",
            ),
            VoiceInfo(
                voice_id="nova",
                name="Nova",
                language="multilingual",
                gender="female",
            ),
            VoiceInfo(
                voice_id="shimmer",
                name="Shimmer",
                language="multilingual",
                gender="female",
            ),
        ]
    
    async def is_available(self) -> bool:
        """Check if OpenAI TTS is available."""
        if not self.config.get("api_key"):
            return False
        
        try:
            client = self._get_client()
            await client.models.list()
            return True
        except Exception:
            return False
