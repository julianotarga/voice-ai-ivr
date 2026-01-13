"""
AWS Transcribe STT Provider.

Uses Amazon Transcribe for speech recognition.
"""

import os
import uuid
import asyncio
from typing import Optional

from .base import BaseSTT, TranscriptionResult


class AWSTranscribeSTT(BaseSTT):
    """
    AWS Transcribe STT provider.
    
    Config:
        aws_access_key_id: AWS access key (or use AWS_ACCESS_KEY_ID env)
        aws_secret_access_key: AWS secret key (or use AWS_SECRET_ACCESS_KEY env)
        region_name: AWS region (default: us-east-1)
        language: Language code (default: pt-BR)
        s3_bucket: S3 bucket for audio files (required for async transcription)
    """
    
    provider_name = "aws_transcribe"
    
    def __init__(self, config: dict):
        super().__init__(config)
        import os
        # Fallback para env vars AWS
        self.aws_access_key_id = config.get("aws_access_key_id") or os.environ.get("AWS_ACCESS_KEY_ID", "")
        self.aws_secret_access_key = config.get("aws_secret_access_key") or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        self.region_name = config.get("region_name") or os.environ.get("AWS_REGION", "us-east-1")
        self.language = config.get("language", "pt-BR")
        self.s3_bucket = config.get("s3_bucket")
    
    def _get_client(self, service: str):
        """Get boto3 client."""
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
        
        return boto3.client(service, **kwargs)
    
    async def transcribe(
        self,
        audio_file: str,
        language: str = "pt",
    ) -> TranscriptionResult:
        """
        Transcribe audio using AWS Transcribe.
        
        Note: AWS Transcribe requires audio to be in S3 for the standard API.
        This implementation uploads to S3, transcribes, and cleans up.
        
        Args:
            audio_file: Path to audio file
            language: Language code (pt, en, es, etc.)
            
        Returns:
            TranscriptionResult with transcribed text
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"Audio file not found: {audio_file}")
        
        if not self.s3_bucket:
            raise ValueError("s3_bucket is required for AWS Transcribe")
        
        # Map language code to AWS format
        lang_map = {
            "pt": "pt-BR",
            "en": "en-US",
            "es": "es-ES",
            "fr": "fr-FR",
            "de": "de-DE",
        }
        aws_lang = lang_map.get(language, language)
        
        # Generate unique job name
        job_name = f"voice-ai-{uuid.uuid4().hex[:8]}"
        s3_key = f"transcribe/{job_name}/{os.path.basename(audio_file)}"
        
        loop = asyncio.get_event_loop()
        
        # Upload to S3
        s3_client = self._get_client("s3")
        await loop.run_in_executor(
            None,
            lambda: s3_client.upload_file(audio_file, self.s3_bucket, s3_key),
        )
        
        s3_uri = f"s3://{self.s3_bucket}/{s3_key}"
        
        try:
            # Start transcription job
            transcribe_client = self._get_client("transcribe")
            
            # Detect media format
            ext = os.path.splitext(audio_file)[1].lower().lstrip(".")
            media_format = ext if ext in ["mp3", "mp4", "wav", "flac", "ogg", "webm"] else "wav"
            
            await loop.run_in_executor(
                None,
                lambda: transcribe_client.start_transcription_job(
                    TranscriptionJobName=job_name,
                    Media={"MediaFileUri": s3_uri},
                    MediaFormat=media_format,
                    LanguageCode=aws_lang,
                ),
            )
            
            # Wait for completion
            import time
            max_wait = 60  # seconds
            start_time = time.time()
            
            while True:
                response = await loop.run_in_executor(
                    None,
                    lambda: transcribe_client.get_transcription_job(
                        TranscriptionJobName=job_name
                    ),
                )
                
                status = response["TranscriptionJob"]["TranscriptionJobStatus"]
                
                if status == "COMPLETED":
                    break
                elif status == "FAILED":
                    reason = response["TranscriptionJob"].get("FailureReason", "Unknown")
                    raise RuntimeError(f"Transcription failed: {reason}")
                
                if time.time() - start_time > max_wait:
                    raise RuntimeError("Transcription timed out")
                
                await asyncio.sleep(2)
            
            # Get transcript
            transcript_uri = response["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
            
            import httpx
            async with httpx.AsyncClient() as client:
                transcript_response = await client.get(transcript_uri)
                transcript_data = transcript_response.json()
            
            text = transcript_data["results"]["transcripts"][0]["transcript"]
            
            return TranscriptionResult(
                text=text.strip(),
                language=aws_lang,
                confidence=None,
                duration_ms=0,
            )
            
        finally:
            # Cleanup S3 and transcription job
            try:
                await loop.run_in_executor(
                    None,
                    lambda: s3_client.delete_object(Bucket=self.s3_bucket, Key=s3_key),
                )
                await loop.run_in_executor(
                    None,
                    lambda: transcribe_client.delete_transcription_job(
                        TranscriptionJobName=job_name
                    ),
                )
            except Exception:
                pass  # Cleanup failures are non-critical
    
    async def is_available(self) -> bool:
        """Check if AWS Transcribe is available."""
        try:
            import boto3
            # Check if we have credentials
            if self.aws_access_key_id or os.environ.get("AWS_ACCESS_KEY_ID"):
                return bool(self.s3_bucket)
            return False
        except ImportError:
            return False
