"""
Handler para conexões do FreeSWITCH.

Referências:
- .context/docs/data-flow.md: mod_audio_stream
- openspec/changes/voice-ai-realtime/design.md: Decision 2 (Protocol)
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FreeSwitchMetadata:
    """Metadata recebido do FreeSWITCH."""
    caller_id: str = ""
    called_number: str = ""
    domain: str = ""
    extension: str = ""


class FreeSwitchHandler:
    """
    Handler para processamento de conexões do FreeSWITCH.
    
    Protocolo conforme design.md:
    - BINARY FRAMES: PCM16 bytes (640 bytes = 20ms @ 16kHz)
    - TEXT FRAMES: JSON control messages
    """
    
    CHUNK_SIZE = 640  # 20ms @ 16kHz mono
    
    def __init__(self, call_uuid: str, domain_uuid: str):
        self.call_uuid = call_uuid
        self.domain_uuid = domain_uuid
        self.metadata: Optional[FreeSwitchMetadata] = None
        self._audio_buffer = b""
    
    def parse_metadata(self, data: dict) -> FreeSwitchMetadata:
        """Parseia metadata do FreeSWITCH."""
        self.metadata = FreeSwitchMetadata(
            caller_id=data.get("caller_id", ""),
            called_number=data.get("called_number", ""),
            domain=data.get("domain", ""),
            extension=data.get("extension", ""),
        )
        
        logger.info("FreeSWITCH metadata parsed", extra={
            "call_uuid": self.call_uuid,
            "caller_id": self.metadata.caller_id,
        })
        
        return self.metadata
    
    def process_audio_chunk(self, audio_bytes: bytes) -> bytes:
        """
        Processa chunk de áudio do FreeSWITCH.
        Acumula em buffer se necessário.
        """
        self._audio_buffer += audio_bytes
        
        # Retorna chunks completos
        if len(self._audio_buffer) >= self.CHUNK_SIZE:
            chunk = self._audio_buffer[:self.CHUNK_SIZE]
            self._audio_buffer = self._audio_buffer[self.CHUNK_SIZE:]
            return chunk
        
        return b""
    
    def handle_dtmf(self, digit: str) -> None:
        """Processa DTMF."""
        logger.debug(f"DTMF received: {digit}", extra={
            "call_uuid": self.call_uuid,
            "digit": digit,
        })
    
    def flush_buffer(self) -> bytes:
        """Retorna e limpa buffer restante."""
        data = self._audio_buffer
        self._audio_buffer = b""
        return data
