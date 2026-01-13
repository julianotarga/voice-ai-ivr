"""
Play.ht TTS Provider.

Uses Play.ht API for high-quality voice synthesis.
Supports many natural voices and voice cloning.
"""

import os
import uuid
from pathlib import Path
from typing import Optional, List

import httpx

from .base import BaseTTS, SynthesisResult, VoiceInfo
from config.settings import settings


class PlayHTTTS(BaseTTS):
    """
    Play.ht TTS provider.
    
    Config:
        api_key: Play.ht API key (required)
        user_id: Play.ht user ID (required)
        voice: Voice ID (default: pt-BR-AntonioNeural)
        quality: Audio quality (draft, low, medium, high, premium)
        speed: Default speaking speed
    """
    
    provider_name = "playht"
    
    BASE_URL = "https://api.play.ht/api/v2"
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env vars Play.ht
        self.api_key = config.get("api_key") or os.environ.get("PLAYHT_API_KEY", "")
        self.user_id = config.get("user_id") or os.environ.get("PLAYHT_USER_ID", "")
        self.voice = config.get("voice", "pt-BR-AntonioNeural")
        self.quality = config.get("quality", "medium")
        self.default_speed = config.get("speed", 1.0)
    
    def _get_headers(self) -> dict:
        """Get API headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-User-ID": self.user_id,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
    
    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        output_path: Optional[str] = None,
    ) -> SynthesisResult:
        """
        Synthesize text using Play.ht.
        
        Args:
            text: Text to synthesize
            voice_id: Play.ht voice ID
            speed: Speaking rate (0.5 to 2.0)
            output_path: Path to save audio (optional)
            
        Returns:
            SynthesisResult with path to audio file
        """
        if not self.api_key or not self.user_id:
            raise ValueError("Play.ht API key and user ID are required")
        
        voice = voice_id or self.voice
        
        # Generate output path if not provided
        if not output_path:
            output_path = str(settings.TEMP_DIR / f"tts_{uuid.uuid4().hex}.mp3")
        
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Call Play.ht API
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.BASE_URL}/tts/stream",
                headers=self._get_headers(),
                json={
                    "text": text,
                    "voice": voice,
                    "quality": self.quality,
                    "output_format": "mp3",
                    "speed": speed,
                    "sample_rate": 24000,
                },
            )
            response.raise_for_status()
            
            # Save audio
            with open(output_path, "wb") as f:
                f.write(response.content)
        
        # Estimate duration from file size
        file_size = os.path.getsize(output_path)
        duration_ms = int((file_size / 16000) * 1000)
        
        return SynthesisResult(
            audio_file=output_path,
            duration_ms=duration_ms,
            format="mp3",
        )
    
    async def list_voices(self, language: str = "pt-BR") -> List[VoiceInfo]:
        """
        List available voices from Play.ht.
        """
        if not self.api_key or not self.user_id:
            return []
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.BASE_URL}/voices",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "X-User-ID": self.user_id,
                    },
                )
                response.raise_for_status()
                data = response.json()
            
            voices = []
            for voice in data:
                voice_lang = voice.get("language", "")
                
                # Filter by language
                if not language or language.lower() in voice_lang.lower():
                    voices.append(VoiceInfo(
                        voice_id=voice.get("id"),
                        name=voice.get("name", "Unknown"),
                        language=voice_lang,
                        gender=voice.get("gender"),
                    ))
            
            return voices
            
        except Exception:
            # Return default voices
            return [
                VoiceInfo(
                    voice_id="pt-BR-AntonioNeural",
                    name="Antonio",
                    language="pt-BR",
                    gender="male",
                ),
            ]
    
    async def is_available(self) -> bool:
        """Check if Play.ht is available."""
        if not self.api_key or not self.user_id:
            return False
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self.BASE_URL}/voices",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "X-User-ID": self.user_id,
                    },
                )
                return response.status_code == 200
        except Exception:
            return False
