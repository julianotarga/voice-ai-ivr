"""
Azure Neural TTS Provider.

Uses Azure Cognitive Services for high-quality neural voice synthesis.
Supports many natural-sounding voices in Portuguese.
"""

import os
import uuid
from pathlib import Path
from typing import Optional, List

from .base import BaseTTS, SynthesisResult, VoiceInfo
from config.settings import settings


class AzureNeuralTTS(BaseTTS):
    """
    Azure Neural TTS provider.
    
    Config:
        subscription_key: Azure Speech subscription key (required)
        region: Azure region (default: brazilsouth)
        voice_name: Voice name (default: pt-BR-FranciscaNeural)
    """
    
    provider_name = "azure_neural"
    
    # Popular Portuguese voices
    PT_BR_VOICES = [
        ("pt-BR-FranciscaNeural", "Francisca", "female"),
        ("pt-BR-AntonioNeural", "Antonio", "male"),
        ("pt-BR-BrendaNeural", "Brenda", "female"),
        ("pt-BR-DonatoNeural", "Donato", "male"),
        ("pt-BR-ElzaNeural", "Elza", "female"),
        ("pt-BR-FabioNeural", "Fabio", "male"),
        ("pt-BR-GiovannaNeural", "Giovanna", "female"),
        ("pt-BR-HumbertoNeural", "Humberto", "male"),
    ]
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env vars Azure Speech
        self.subscription_key = config.get("subscription_key") or config.get("api_key") or os.environ.get("AZURE_SPEECH_KEY", "")
        self.region = config.get("region") or os.environ.get("AZURE_SPEECH_REGION", "brazilsouth")
        self.voice_name = config.get("voice_name", "pt-BR-FranciscaNeural")
    
    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        output_path: Optional[str] = None,
    ) -> SynthesisResult:
        """
        Synthesize text using Azure Neural TTS.
        
        Args:
            text: Text to synthesize
            voice_id: Azure voice name
            speed: Speech rate (0.5 to 2.0)
            output_path: Path to save audio (optional)
            
        Returns:
            SynthesisResult with path to audio file
        """
        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            raise ImportError(
                "azure-cognitiveservices-speech not installed. "
                "Install with: pip install azure-cognitiveservices-speech"
            )
        
        voice = voice_id or self.voice_name
        
        # Generate output path if not provided
        if not output_path:
            output_path = str(settings.TEMP_DIR / f"tts_{uuid.uuid4().hex}.wav")
        
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Create speech config
        speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key,
            region=self.region,
        )
        speech_config.speech_synthesis_voice_name = voice
        
        # Set output format to WAV
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
        )
        
        # Create audio config for file output
        audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)
        
        # Create synthesizer
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
        
        # Build SSML for rate control
        rate_percent = int((speed - 1.0) * 100)
        rate_str = f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"
        
        ssml = f"""
        <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="pt-BR">
            <voice name="{voice}">
                <prosody rate="{rate_str}">
                    {text}
                </prosody>
            </voice>
        </speak>
        """
        
        # Synthesize (synchronous, run in executor)
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: synthesizer.speak_ssml_async(ssml).get(),
        )
        
        # Check result
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            duration_ms = int(result.audio_duration / 10000)  # 100-ns to ms
            
            return SynthesisResult(
                audio_file=output_path,
                duration_ms=duration_ms,
                format="wav",
            )
        else:
            cancellation = result.cancellation_details
            raise RuntimeError(
                f"Azure TTS failed: {cancellation.reason} - {cancellation.error_details}"
            )
    
    async def list_voices(self, language: str = "pt-BR") -> List[VoiceInfo]:
        """List available Portuguese voices."""
        voices = []
        
        for voice_name, name, gender in self.PT_BR_VOICES:
            voices.append(VoiceInfo(
                voice_id=voice_name,
                name=name,
                language="pt-BR",
                gender=gender,
            ))
        
        return voices
    
    async def is_available(self) -> bool:
        """Check if Azure Neural TTS is available."""
        if not self.subscription_key:
            return False
        
        try:
            import azure.cognitiveservices.speech as speechsdk
            return True
        except ImportError:
            return False
