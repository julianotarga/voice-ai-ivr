"""
Audio codec utilities for G.711 (μ-law / A-law) conversion.

G.711 μ-law (PCMU) is the standard telephony codec used by:
- FreeSWITCH / FusionPBX
- OpenAI Realtime API (audio/pcmu)
- Traditional phone networks (PSTN)

Using G.711 natively reduces latency by ~50ms by eliminating:
- Resampling (16kHz ↔ 8kHz)
- Format conversion overhead

Reference:
- ITU-T G.711: https://www.itu.int/rec/T-REC-G.711
- OpenAI Realtime: https://platform.openai.com/docs/guides/realtime
"""

import audioop
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# G.711 constants
G711_SAMPLE_RATE = 8000
G711_BYTES_PER_SAMPLE = 1  # 8-bit compressed

# L16 PCM constants
L16_SAMPLE_RATE = 8000  # Can also be 16000, 24000
L16_BYTES_PER_SAMPLE = 2  # 16-bit linear


def pcm_to_ulaw(pcm_data: bytes, width: int = 2) -> bytes:
    """
    Convert linear PCM to G.711 μ-law.
    
    Args:
        pcm_data: Linear PCM audio bytes
        width: Sample width in bytes (2 for 16-bit, 1 for 8-bit)
    
    Returns:
        G.711 μ-law encoded audio
        
    Note:
        Output is half the size of 16-bit input (2 bytes → 1 byte per sample)
    """
    if not pcm_data:
        return b""
    
    try:
        return audioop.lin2ulaw(pcm_data, width)
    except audioop.error as e:
        logger.error(f"Failed to convert PCM to μ-law: {e}")
        return b""


def ulaw_to_pcm(ulaw_data: bytes, width: int = 2) -> bytes:
    """
    Convert G.711 μ-law to linear PCM.
    
    Args:
        ulaw_data: G.711 μ-law encoded audio
        width: Output sample width in bytes (2 for 16-bit)
    
    Returns:
        Linear PCM audio bytes
        
    Note:
        Output is double the size of input (1 byte → 2 bytes per sample)
    """
    if not ulaw_data:
        return b""
    
    try:
        return audioop.ulaw2lin(ulaw_data, width)
    except audioop.error as e:
        logger.error(f"Failed to convert μ-law to PCM: {e}")
        return b""


def pcm_to_alaw(pcm_data: bytes, width: int = 2) -> bytes:
    """
    Convert linear PCM to G.711 A-law.
    
    Args:
        pcm_data: Linear PCM audio bytes
        width: Sample width in bytes (2 for 16-bit)
    
    Returns:
        G.711 A-law encoded audio
    """
    if not pcm_data:
        return b""
    
    try:
        return audioop.lin2alaw(pcm_data, width)
    except audioop.error as e:
        logger.error(f"Failed to convert PCM to A-law: {e}")
        return b""


def alaw_to_pcm(alaw_data: bytes, width: int = 2) -> bytes:
    """
    Convert G.711 A-law to linear PCM.
    
    Args:
        alaw_data: G.711 A-law encoded audio
        width: Output sample width in bytes (2 for 16-bit)
    
    Returns:
        Linear PCM audio bytes
    """
    if not alaw_data:
        return b""
    
    try:
        return audioop.alaw2lin(alaw_data, width)
    except audioop.error as e:
        logger.error(f"Failed to convert A-law to PCM: {e}")
        return b""


def get_g711_frame_size(duration_ms: int = 20) -> int:
    """
    Calculate G.711 frame size in bytes for a given duration.
    
    Args:
        duration_ms: Frame duration in milliseconds
    
    Returns:
        Frame size in bytes
        
    Example:
        20ms @ 8kHz = 160 samples = 160 bytes (G.711)
        20ms @ 8kHz = 160 samples = 320 bytes (L16 PCM)
    """
    return int(G711_SAMPLE_RATE * duration_ms / 1000 * G711_BYTES_PER_SAMPLE)


def get_pcm_frame_size(duration_ms: int = 20, sample_rate: int = 8000) -> int:
    """
    Calculate L16 PCM frame size in bytes for a given duration.
    
    Args:
        duration_ms: Frame duration in milliseconds
        sample_rate: Sample rate in Hz (8000 or 16000)
    
    Returns:
        Frame size in bytes
    """
    return int(sample_rate * duration_ms / 1000 * L16_BYTES_PER_SAMPLE)


class G711Codec:
    """
    G.711 codec wrapper for consistent encoding/decoding.
    
    Usage:
        codec = G711Codec(law='ulaw')
        
        # Encode PCM to G.711
        g711_data = codec.encode(pcm_data)
        
        # Decode G.711 to PCM  
        pcm_data = codec.decode(g711_data)
    """
    
    def __init__(self, law: str = "ulaw"):
        """
        Initialize G.711 codec.
        
        Args:
            law: "ulaw" (μ-law, default) or "alaw" (A-law)
        """
        if law not in ("ulaw", "alaw"):
            raise ValueError(f"Invalid law: {law}. Must be 'ulaw' or 'alaw'")
        
        self.law = law
        self._encode = pcm_to_ulaw if law == "ulaw" else pcm_to_alaw
        self._decode = ulaw_to_pcm if law == "ulaw" else alaw_to_pcm
        
        logger.debug(f"G711Codec initialized with {law}")
    
    def encode(self, pcm_data: bytes) -> bytes:
        """Encode linear PCM to G.711."""
        return self._encode(pcm_data)
    
    def decode(self, g711_data: bytes) -> bytes:
        """Decode G.711 to linear PCM."""
        return self._decode(g711_data)
    
    @property
    def mime_type(self) -> str:
        """Return MIME type for this codec."""
        return f"audio/{'pcmu' if self.law == 'ulaw' else 'pcma'}"
    
    @property  
    def openai_format(self) -> str:
        """Return OpenAI audio format string."""
        return "pcmu" if self.law == "ulaw" else "pcma"


# Convenience instances
ULAW_CODEC = G711Codec("ulaw")
ALAW_CODEC = G711Codec("alaw")
