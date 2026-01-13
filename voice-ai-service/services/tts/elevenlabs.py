"""
ElevenLabs TTS Provider.

Premium voice synthesis with highly natural voices.
Supports voice cloning and custom voices.
"""

import os
import uuid
from pathlib import Path
from typing import Optional, List

import httpx

from .base import BaseTTS, SynthesisResult, VoiceInfo
from config.settings import settings


class ElevenLabsTTS(BaseTTS):
    """
    ElevenLabs TTS provider for premium voice synthesis.
    
    Config:
        api_key: ElevenLabs API key (required)
        voice_id: Default voice ID
        model_id: Model ID (default: eleven_multilingual_v2)
        stability: Voice stability 0-1 (default: 0.5)
        similarity_boost: Voice similarity 0-1 (default: 0.75)
        style: Style exaggeration 0-1 (default: 0.0)
    """
    
    provider_name = "elevenlabs"
    
    BASE_URL = "https://api.elevenlabs.io/v1"
    
    def __init__(self, config: dict):
        super().__init__(config)
        # Fallback para env var se não configurado no banco
        self.api_key = config.get("api_key") or os.environ.get("ELEVENLABS_API_KEY", "")
        self.default_voice_id = config.get("voice_id", "21m00Tcm4TlvDq8ikWAM")  # Rachel
        self.model_id = config.get("model_id", "eleven_multilingual_v2")
    
    def _get_headers(self) -> dict:
        """Get API headers."""
        return {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key,
        }
    
    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        output_path: Optional[str] = None,
    ) -> SynthesisResult:
        """
        Synthesize text to speech using ElevenLabs.
        
        Args:
            text: Text to synthesize
            voice_id: ElevenLabs voice ID
            speed: Not directly supported, ignored
            output_path: Path to save audio (optional)
            
        Returns:
            SynthesisResult with path to audio file
        """
        voice = voice_id or self.default_voice_id
        
        # Generate output path if not provided
        if not output_path:
            output_path = str(settings.TEMP_DIR / f"tts_{uuid.uuid4().hex}.mp3")
        
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Voice settings
        stability = self.config.get("stability", 0.5)
        similarity_boost = self.config.get("similarity_boost", 0.75)
        style = self.config.get("style", 0.0)
        
        # Call ElevenLabs API
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.BASE_URL}/text-to-speech/{voice}",
                headers=self._get_headers(),
                json={
                    "text": text,
                    "model_id": self.model_id,
                    "voice_settings": {
                        "stability": stability,
                        "similarity_boost": similarity_boost,
                        "style": style,
                        "use_speaker_boost": True,
                    },
                },
            )
            response.raise_for_status()
            
            # Save audio
            with open(output_path, "wb") as f:
                f.write(response.content)
        
        # Get file info for duration estimation
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
        List available voices from ElevenLabs.
        """
        if not self.api_key:
            return []
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.BASE_URL}/voices",
                    headers={"xi-api-key": self.api_key},
                )
                response.raise_for_status()
                data = response.json()
            
            voices = []
            for voice in data.get("voices", []):
                # Filter by language if possible
                labels = voice.get("labels", {})
                voice_lang = labels.get("language", "")
                
                # Include if Portuguese or no language filter
                if not language or "portuguese" in voice_lang.lower() or not voice_lang:
                    voices.append(VoiceInfo(
                        voice_id=voice["voice_id"],
                        name=voice["name"],
                        language=voice_lang or "multilingual",
                        gender=labels.get("gender"),
                    ))
            
            return voices
            
        except Exception:
            # Return default voices if API fails
            return [
                VoiceInfo(
                    voice_id="21m00Tcm4TlvDq8ikWAM",
                    name="Rachel",
                    language="en",
                    gender="female",
                ),
                VoiceInfo(
                    voice_id="AZnzlk1XvdvUeBnXmlld",
                    name="Domi",
                    language="multilingual",
                    gender="female",
                ),
            ]
    
    async def is_available(self) -> bool:
        """Check if ElevenLabs is available."""
        if not self.api_key:
            return False
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self.BASE_URL}/user",
                    headers={"xi-api-key": self.api_key},
                )
                return response.status_code == 200
        except Exception:
            return False
