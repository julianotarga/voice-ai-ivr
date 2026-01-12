"""
Audio Resampler para conversão de sample rates.

Referências:
- openspec/changes/voice-ai-realtime/design.md: Decision 5 (Resampling)
- .context/docs/data-flow.md: PCM 16kHz do FreeSWITCH

Sample rates:
- FreeSWITCH: 16kHz
- OpenAI Realtime: 24kHz  
- ElevenLabs: 16kHz
- Gemini: 16kHz
"""

import logging
from math import gcd
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class Resampler:
    """
    Resampler eficiente para streaming de áudio.
    Usa scipy.signal.resample_poly para qualidade.
    """
    
    def __init__(self, input_rate: int, output_rate: int):
        self.input_rate = input_rate
        self.output_rate = output_rate
        
        g = gcd(input_rate, output_rate)
        self.up = output_rate // g
        self.down = input_rate // g
        
        self.needs_resample = (input_rate != output_rate)
        
        logger.debug(f"Resampler: {input_rate}Hz -> {output_rate}Hz (up={self.up}, down={self.down})")
    
    def process(self, audio_bytes: bytes) -> bytes:
        """Resamplea chunk de áudio PCM16."""
        if not audio_bytes or not self.needs_resample:
            return audio_bytes
        
        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        if len(samples) == 0:
            return b""
        
        try:
            from scipy import signal
            float_samples = samples.astype(np.float32)
            resampled = signal.resample_poly(float_samples, self.up, self.down)
            return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
        except ImportError:
            return self._simple_resample(samples).tobytes()
    
    def _simple_resample(self, samples: np.ndarray) -> np.ndarray:
        """Fallback: interpolação linear."""
        new_length = int(len(samples) * self.up / self.down)
        indices = np.linspace(0, len(samples) - 1, new_length)
        return np.interp(indices, np.arange(len(samples)), samples).astype(np.int16)


class ResamplerPair:
    """
    Par de resamplers para comunicação bidirecional.
    
    - Input: FreeSWITCH (16kHz) -> Provider (24kHz)
    - Output: Provider (24kHz) -> FreeSWITCH (16kHz)
    """
    
    def __init__(self, freeswitch_rate: int = 16000, provider_rate: int = 24000):
        self.input_resampler = Resampler(freeswitch_rate, provider_rate)
        self.output_resampler = Resampler(provider_rate, freeswitch_rate)
    
    def resample_input(self, audio_bytes: bytes) -> bytes:
        """FS -> Provider"""
        return self.input_resampler.process(audio_bytes)
    
    def resample_output(self, audio_bytes: bytes) -> bytes:
        """Provider -> FS"""
        return self.output_resampler.process(audio_bytes)
