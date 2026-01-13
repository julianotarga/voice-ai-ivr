"""
Azure Speech-to-Text Provider.

Uses Azure Cognitive Services Speech SDK for high-quality transcription.
Supports multiple languages with excellent Portuguese recognition.
"""

import os
from typing import Optional

from .base import BaseSTT, TranscriptionResult


class AzureSpeechSTT(BaseSTT):
    """
    Azure Cognitive Services Speech-to-Text provider.
    
    Config:
        subscription_key: Azure Speech subscription key (required)
        region: Azure region (default: brazilsouth)
        language: Language code (default: pt-BR)
    """
    
    provider_name = "azure_speech"
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env vars Azure Speech
        self.subscription_key = config.get("subscription_key") or config.get("api_key") or os.environ.get("AZURE_SPEECH_KEY", "")
        self.region = config.get("region") or os.environ.get("AZURE_SPEECH_REGION", "brazilsouth")
        self.language = config.get("language", "pt-BR")
    
    async def transcribe(
        self,
        audio_file: str,
        language: str = "pt",
    ) -> TranscriptionResult:
        """
        Transcribe audio using Azure Speech.
        
        Args:
            audio_file: Path to audio file
            language: Language code (pt, en, es, etc.)
            
        Returns:
            TranscriptionResult with transcribed text
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"Audio file not found: {audio_file}")
        
        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            raise ImportError(
                "azure-cognitiveservices-speech not installed. "
                "Install with: pip install azure-cognitiveservices-speech"
            )
        
        # Map language code to Azure format
        lang_map = {
            "pt": "pt-BR",
            "en": "en-US",
            "es": "es-ES",
            "fr": "fr-FR",
            "de": "de-DE",
            "it": "it-IT",
        }
        azure_lang = lang_map.get(language, language)
        
        # Create speech config
        speech_config = speechsdk.SpeechConfig(
            subscription=self.subscription_key,
            region=self.region,
        )
        speech_config.speech_recognition_language = azure_lang
        
        # Create audio config from file
        audio_config = speechsdk.audio.AudioConfig(filename=audio_file)
        
        # Create recognizer
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
        
        # Perform recognition (synchronous, run in executor for async)
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            recognizer.recognize_once,
        )
        
        # Process result
        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            return TranscriptionResult(
                text=result.text.strip(),
                language=azure_lang,
                confidence=None,  # Azure doesn't provide per-result confidence
                duration_ms=int(result.duration / 10000),  # 100-nanoseconds to ms
            )
        elif result.reason == speechsdk.ResultReason.NoMatch:
            return TranscriptionResult(
                text="",
                language=azure_lang,
                confidence=0.0,
                duration_ms=0,
            )
        else:
            cancellation = result.cancellation_details
            raise RuntimeError(
                f"Azure Speech recognition failed: {cancellation.reason} - {cancellation.error_details}"
            )
    
    async def is_available(self) -> bool:
        """Check if Azure Speech is available."""
        if not self.subscription_key:
            return False
        
        try:
            import azure.cognitiveservices.speech as speechsdk
            return True
        except ImportError:
            return False
