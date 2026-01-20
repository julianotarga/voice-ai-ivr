"""
Announcement TTS Service - Gera áudio de anúncio usando ElevenLabs ou OpenAI.

Usado para anunciar clientes ao atendente humano durante transferência.

Suporta:
- ElevenLabs TTS (eleven_multilingual_v2)
- OpenAI TTS (tts-1, tts-1-hd)

Ref: voice-ai-ivr/openspec/changes/announced-transfer/
"""

import asyncio
import hashlib
import logging
import os
import subprocess
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Diretório para cache de áudio
# Usar /tmp que é acessível pelo FreeSWITCH
AUDIO_CACHE_DIR = Path(os.getenv("ANNOUNCEMENT_CACHE_DIR", "/tmp/voice-ai-announcements"))

# TTL do cache em segundos (1 hora)
CACHE_TTL = int(os.getenv("ANNOUNCEMENT_CACHE_TTL", "3600"))

# Provider padrão para TTS (elevenlabs ou openai)
DEFAULT_TTS_PROVIDER = os.getenv("ANNOUNCEMENT_TTS_PROVIDER", "elevenlabs")


class TTSProvider(str, Enum):
    """Providers de TTS suportados."""
    ELEVENLABS = "elevenlabs"
    OPENAI = "openai"


class AnnouncementTTS:
    """
    Serviço para gerar áudio de anúncio usando ElevenLabs ou OpenAI.
    
    Fluxo:
    1. Verificar cache (mesmo texto = mesmo áudio)
    2. Se não tem cache, gerar via ElevenLabs ou OpenAI
    3. Converter MP3 para WAV (PCM 16kHz mono)
    4. Salvar em diretório acessível pelo FreeSWITCH
    5. Retornar caminho do arquivo
    """
    
    # Mapeamento de vozes OpenAI
    OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    
    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None
    ):
        """
        Args:
            provider: "elevenlabs" ou "openai" (ou usar env ANNOUNCEMENT_TTS_PROVIDER)
            api_key: API key (ou usar env ELEVENLABS_API_KEY / OPENAI_API_KEY)
            voice_id: ID da voz (depende do provider)
            model_id: Modelo (eleven_multilingual_v2 ou tts-1/tts-1-hd)
        """
        # Determinar provider
        self.provider = TTSProvider(provider or DEFAULT_TTS_PROVIDER)
        
        # Configurar baseado no provider
        if self.provider == TTSProvider.OPENAI:
            self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
            self.voice_id = voice_id or os.getenv("OPENAI_TTS_VOICE", "nova")
            self.model_id = model_id or os.getenv("OPENAI_TTS_MODEL", "tts-1")
            self.base_url = "https://api.openai.com/v1"
        else:  # ElevenLabs
            self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
            self.voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
            self.model_id = model_id or "eleven_multilingual_v2"
            self.base_url = "https://api.elevenlabs.io/v1"
        
        # Criar diretório de cache
        AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"AnnouncementTTS initialized with provider={self.provider.value}")
    
    def _get_cache_key(self, text: str, voice_id: str) -> str:
        """Gera chave de cache baseada no texto e voz."""
        content = f"{voice_id}:{text}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """Retorna caminho do arquivo de cache."""
        return AUDIO_CACHE_DIR / f"{cache_key}.wav"
    
    def _is_cache_valid(self, cache_path: Path) -> bool:
        """Verifica se cache existe e não expirou."""
        if not cache_path.exists():
            return False
        
        age = time.time() - cache_path.stat().st_mtime
        return age < CACHE_TTL
    
    def _sanitize_text(self, text: str) -> str:
        """
        Sanitiza texto para TTS.
        
        Remove/substitui caracteres que podem causar problemas:
        - Emojis
        - Caracteres de controle
        - HTML/XML tags
        - Caracteres especiais que quebram SSML
        
        Args:
            text: Texto original
        
        Returns:
            Texto sanitizado
        """
        import re
        
        if not text:
            return ""
        
        # Remover HTML/XML tags
        text = re.sub(r'<[^>]+>', '', text)
        
        # Remover caracteres de controle (exceto newline, tab)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        
        # Remover emojis (Unicode emoji ranges)
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags (iOS)
            "\U00002702-\U000027B0"  # dingbats
            "\U000024C2-\U0001F251" 
            "]+",
            flags=re.UNICODE
        )
        text = emoji_pattern.sub('', text)
        
        # Escapar caracteres SSML (para providers que usam SSML)
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        
        # Normalizar espaços múltiplos
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Limitar tamanho (prevenir abuse)
        MAX_TEXT_LENGTH = 1000
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH] + "..."
            logger.warning(f"TTS text truncated to {MAX_TEXT_LENGTH} chars")
        
        return text
    
    async def generate_announcement(
        self,
        text: str,
        voice_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> Optional[str]:
        """
        Gera áudio de anúncio.
        
        Args:
            text: Texto do anúncio
            voice_id: ID da voz (opcional, usa padrão do provider)
            timeout: Timeout em segundos para geração (padrão: 30s)
        
        Returns:
            Caminho do arquivo WAV ou None se falhar
        """
        start_time = time.time()
        
        # Sanitizar texto de entrada
        text = self._sanitize_text(text)
        if not text:
            logger.error("TTS text is empty after sanitization")
            return None
        
        voice = voice_id or self.voice_id
        
        # Validar voice_id baseado no provider
        if self.provider == TTSProvider.OPENAI:
            # OpenAI usa nomes de voz: alloy, echo, fable, onyx, nova, shimmer
            if voice not in self.OPENAI_VOICES:
                logger.warning(f"OpenAI voice '{voice}' invalid, using 'nova'")
                voice = "nova"
        else:
            # ElevenLabs usa IDs de ~20 caracteres
            if not voice:
                logger.warning("No voice_id provided, using default")
                voice = self.voice_id
            elif len(voice) < 10:
                logger.warning(f"ElevenLabs voice_id '{voice}' seems invalid, using default")
                voice = self.voice_id
        
        if not self.api_key:
            logger.error(f"{self.provider.value} API key not configured")
            return None
        
        # Verificar cache (inclui provider na chave para evitar conflitos)
        cache_key = self._get_cache_key(f"{self.provider.value}:{text}", voice)
        cache_path = self._get_cache_path(cache_key)
        
        if self._is_cache_valid(cache_path):
            duration = time.time() - start_time
            logger.info(
                f"Announcement cache hit: {cache_key}",
                extra={
                    "tts_provider": self.provider.value,
                    "text_length": len(text),
                    "cache_hit": True,
                    "duration_seconds": duration
                }
            )
            return str(cache_path)
        
        logger.info(f"Generating announcement via {self.provider.value}: {text[:50]}...")
        
        try:
            # 1. Gerar MP3 via provider selecionado (com timeout)
            if self.provider == TTSProvider.OPENAI:
                mp3_path = await asyncio.wait_for(
                    self._generate_mp3_openai(text, voice),
                    timeout=timeout
                )
            else:
                mp3_path = await asyncio.wait_for(
                    self._generate_mp3_elevenlabs(text, voice),
                    timeout=timeout
                )
            
            if not mp3_path:
                return None
            
            # 2. Converter MP3 para WAV (PCM 16kHz mono)
            wav_path = await self._convert_to_wav(mp3_path, cache_path)
            
            # 3. Limpar MP3 temporário
            try:
                os.unlink(mp3_path)
            except Exception:
                pass
            
            if wav_path:
                duration = time.time() - start_time
                logger.info(
                    f"Announcement generated: {wav_path}",
                    extra={
                        "tts_provider": self.provider.value,
                        "text_length": len(text),
                        "cache_hit": False,
                        "duration_seconds": duration,
                        "voice_id": voice
                    }
                )
                return wav_path
            
            return None
        
        except asyncio.TimeoutError:
            logger.error(f"TTS generation timeout after {timeout}s")
            return None
            
        except Exception as e:
            logger.exception(f"Error generating announcement: {e}")
            return None
    
    async def _generate_mp3_elevenlabs(self, text: str, voice_id: str) -> Optional[str]:
        """Gera MP3 via ElevenLabs API."""
        try:
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": self.api_key,
            }
            
            payload = {
                "text": text,
                "model_id": self.model_id,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "style": 0.0,
                    "use_speaker_boost": True,
                },
            }
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/text-to-speech/{voice_id}",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                
                # Salvar MP3 temporário
                fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
                with os.fdopen(fd, "wb") as f:
                    f.write(response.content)
                
                return mp3_path
                
        except httpx.HTTPStatusError as e:
            logger.error(f"ElevenLabs API error: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Error calling ElevenLabs: {e}")
            return None
    
    async def _generate_mp3_openai(self, text: str, voice: str) -> Optional[str]:
        """
        Gera MP3 via OpenAI TTS API.
        
        Ref: https://platform.openai.com/docs/guides/text-to-speech
        
        Vozes disponíveis: alloy, echo, fable, onyx, nova, shimmer
        Modelos: tts-1 (rápido), tts-1-hd (alta qualidade)
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            
            payload = {
                "model": self.model_id,  # tts-1 ou tts-1-hd
                "voice": voice,
                "input": text,
                "response_format": "mp3",
            }
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/audio/speech",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                
                # Salvar MP3 temporário
                fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
                with os.fdopen(fd, "wb") as f:
                    f.write(response.content)
                
                logger.debug(f"OpenAI TTS generated: {len(response.content)} bytes")
                return mp3_path
                
        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI TTS API error: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Error calling OpenAI TTS: {e}")
            return None
    
    async def _convert_to_wav(self, mp3_path: str, wav_path: Path) -> Optional[str]:
        """
        Converte MP3 para WAV PCM 8kHz mono (padrão telefonia).
        
        Usa ffmpeg para conversão.
        
        Args:
            mp3_path: Caminho do MP3 de entrada
            wav_path: Caminho do WAV de saída
        
        Returns:
            Caminho do WAV ou None se falhar
        """
        try:
            # Usar ffmpeg para converter
            # -ar 8000: sample rate 8kHz (padrão telefonia)
            # -ac 1: mono
            # -acodec pcm_s16le: PCM 16-bit little-endian
            cmd = [
                "ffmpeg", "-y",
                "-i", mp3_path,
                "-ar", "8000",
                "-ac", "1",
                "-acodec", "pcm_s16le",
                str(wav_path)
            ]
            
            # Executar em thread separada para não bloquear
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=30
                )
            )
            
            if result.returncode != 0:
                logger.error(f"ffmpeg error: {result.stderr.decode()}")
                return None
            
            return str(wav_path)
            
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg timeout")
            return None
        except FileNotFoundError:
            logger.error("ffmpeg not found - install with: apt-get install ffmpeg")
            return None
        except Exception as e:
            logger.error(f"Conversion error: {e}")
            return None
    
    def cleanup_old_cache(self) -> int:
        """
        Remove arquivos de cache expirados.
        
        Returns:
            Número de arquivos removidos
        """
        removed = 0
        now = time.time()
        
        try:
            for path in AUDIO_CACHE_DIR.glob("*.wav"):
                age = now - path.stat().st_mtime
                if age > CACHE_TTL:
                    path.unlink()
                    removed += 1
        except Exception as e:
            logger.warning(f"Cache cleanup error: {e}")
        
        return removed
    
    async def is_available(self) -> bool:
        """
        Verifica se o provider de TTS está disponível.
        
        Útil para health checks e fallback decisions.
        
        Returns:
            True se a API está acessível e autenticada
        """
        if not self.api_key:
            return False
        
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                if self.provider == TTSProvider.OPENAI:
                    # OpenAI: verificar endpoint de modelos
                    response = await client.get(
                        f"{self.base_url}/models",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                else:
                    # ElevenLabs: verificar endpoint de usuário
                    response = await client.get(
                        f"{self.base_url}/user",
                        headers={"xi-api-key": self.api_key},
                    )
                return response.status_code == 200
        except Exception as e:
            logger.warning(f"{self.provider.value} health check failed: {e}")
            return False


# Singletons para reuso (um por provider)
_announcement_tts_elevenlabs: Optional[AnnouncementTTS] = None
_announcement_tts_openai: Optional[AnnouncementTTS] = None


def get_announcement_tts(provider: Optional[str] = None) -> AnnouncementTTS:
    """
    Retorna instância singleton do AnnouncementTTS.
    
    Args:
        provider: "elevenlabs" ou "openai" (usa env ANNOUNCEMENT_TTS_PROVIDER se None)
    
    Returns:
        Instância do AnnouncementTTS para o provider especificado
    """
    global _announcement_tts_elevenlabs, _announcement_tts_openai
    
    # Determinar provider
    prov = TTSProvider(provider or DEFAULT_TTS_PROVIDER)
    
    if prov == TTSProvider.OPENAI:
        if _announcement_tts_openai is None:
            _announcement_tts_openai = AnnouncementTTS(provider="openai")
        return _announcement_tts_openai
    else:
        if _announcement_tts_elevenlabs is None:
            _announcement_tts_elevenlabs = AnnouncementTTS(provider="elevenlabs")
        return _announcement_tts_elevenlabs
