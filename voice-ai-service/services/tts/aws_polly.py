"""
AWS Polly TTS Provider.

Uses Amazon Polly for text-to-speech synthesis.
"""

import os
import uuid
from pathlib import Path
from typing import Optional, List

from .base import BaseTTS, SynthesisResult, VoiceInfo
from config.settings import settings


class AWSPollyTTS(BaseTTS):
    """
    AWS Polly TTS provider.
    
    Config:
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key
        region_name: AWS region (default: us-east-1)
        voice_id: Voice ID (default: Camila - Brazilian Portuguese)
        engine: Engine type (neural or standard, default: neural)
    """
    
    provider_name = "aws_polly"
    
    # Brazilian Portuguese voices
    PT_BR_VOICES = [
        ("Camila", "Camila", "female", "neural"),
        ("Vitoria", "Vitória", "female", "neural"),
        ("Thiago", "Thiago", "male", "neural"),
        ("Ricardo", "Ricardo", "male", "standard"),
    ]
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env vars AWS
        self.aws_access_key_id = config.get("aws_access_key_id") or os.environ.get("AWS_ACCESS_KEY_ID", "")
        self.aws_secret_access_key = config.get("aws_secret_access_key") or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        self.region_name = config.get("region_name") or os.environ.get("AWS_REGION", "us-east-1")
        self.voice_id = config.get("voice_id", "Camila")
        self.engine = config.get("engine", "neural")
    
    def _get_client(self):
        """Get boto3 Polly client."""
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 not installed. Install with: pip install boto3"
            )
        
        kwargs = {"region_name": self.region_name}
        if self.aws_access_key_id:
            kwargs["aws_access_key_id"] = self.aws_access_key_id
        if self.aws_secret_access_key:
            kwargs["aws_secret_access_key"] = self.aws_secret_access_key
        
        return boto3.client("polly", **kwargs)
    
    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        output_path: Optional[str] = None,
    ) -> SynthesisResult:
        """
        Synthesize text using AWS Polly.
        
        Args:
            text: Text to synthesize
            voice_id: Polly voice ID
            speed: Not directly supported, use SSML
            output_path: Path to save audio (optional)
            
        Returns:
            SynthesisResult with path to audio file
        """
        voice = voice_id or self.voice_id
        
        # Generate output path if not provided
        if not output_path:
            output_path = str(settings.TEMP_DIR / f"tts_{uuid.uuid4().hex}.mp3")
        
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Build SSML for rate control
        rate_percent = int(speed * 100)
        ssml_text = f"""
        <speak>
            <prosody rate="{rate_percent}%">
                {text}
            </prosody>
        </speak>
        """
        
        # Get client
        client = self._get_client()
        
        # Synthesize speech
        import asyncio
        loop = asyncio.get_event_loop()
        
        response = await loop.run_in_executor(
            None,
            lambda: client.synthesize_speech(
                Engine=self.engine,
                LanguageCode="pt-BR",
                OutputFormat="mp3",
                SampleRate="16000",
                Text=ssml_text,
                TextType="ssml",
                VoiceId=voice,
            ),
        )
        
        # Save audio stream
        audio_stream = response["AudioStream"]
        with open(output_path, "wb") as f:
            f.write(audio_stream.read())
        
        # Estimate duration from file size
        file_size = os.path.getsize(output_path)
        duration_ms = int((file_size / 16000) * 1000)  # Rough estimate
        
        return SynthesisResult(
            audio_file=output_path,
            duration_ms=duration_ms,
            format="mp3",
        )
    
    async def list_voices(self, language: str = "pt-BR") -> List[VoiceInfo]:
        """List available Portuguese voices."""
        voices = []
        
        for voice_id, name, gender, engine in self.PT_BR_VOICES:
            voices.append(VoiceInfo(
                voice_id=voice_id,
                name=name,
                language="pt-BR",
                gender=gender,
            ))
        
        return voices
    
    async def is_available(self) -> bool:
        """Check if AWS Polly is available."""
        try:
            import boto3
            if self.aws_access_key_id or os.environ.get("AWS_ACCESS_KEY_ID"):
                return True
            return False
        except ImportError:
            return False
