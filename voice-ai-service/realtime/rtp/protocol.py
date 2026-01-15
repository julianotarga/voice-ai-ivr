"""
RTP Protocol Implementation

Parser e builder de pacotes RTP (RFC 3550).
Suporta codecs de áudio comuns (PCMU, PCMA, L16).

Referências:
- RFC 3550: RTP: A Transport Protocol for Real-Time Applications
- openspec/changes/refactor-esl-rtp-bridge/specs/esl-rtp-protocol/spec.md
"""

import struct
from dataclasses import dataclass, field
from typing import Optional, Tuple
import time


# RTP Payload Types (RFC 3551)
class PayloadType:
    PCMU = 0      # G.711 μ-law
    PCMA = 8      # G.711 A-law
    G722 = 9      # G.722
    L16_STEREO = 10  # Linear PCM 16-bit stereo 44.1kHz
    L16_MONO = 11    # Linear PCM 16-bit mono 44.1kHz
    G729 = 18     # G.729
    
    # Dynamic payload types (96-127)
    DYNAMIC_START = 96
    DYNAMIC_END = 127


# Sample rates por payload type
SAMPLE_RATES = {
    PayloadType.PCMU: 8000,
    PayloadType.PCMA: 8000,
    PayloadType.G722: 8000,  # Realmente 16kHz mas RTP usa 8kHz timestamp
    PayloadType.L16_STEREO: 44100,
    PayloadType.L16_MONO: 44100,
    PayloadType.G729: 8000,
}


@dataclass
class RTPHeader:
    """
    Cabeçalho RTP (12 bytes mínimo).
    
    Formato (RFC 3550):
    0                   1                   2                   3
    0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |V=2|P|X|  CC   |M|     PT      |       sequence number         |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |                           timestamp                           |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |           synchronization source (SSRC) identifier            |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    """
    version: int = 2        # RTP version (sempre 2)
    padding: bool = False   # Padding bit
    extension: bool = False # Extension bit
    cc: int = 0            # CSRC count
    marker: bool = False   # Marker bit (ex: primeiro pacote de talkspurt)
    payload_type: int = 0  # Payload type (0=PCMU, 8=PCMA, etc)
    sequence: int = 0      # Sequence number (0-65535)
    timestamp: int = 0     # Timestamp
    ssrc: int = 0          # Synchronization source ID
    csrc: list = field(default_factory=list)  # Contributing source IDs
    
    # Extension header (se extension=True)
    extension_profile: int = 0
    extension_data: bytes = b""
    
    @classmethod
    def parse(cls, data: bytes) -> Tuple["RTPHeader", int]:
        """
        Parse header de bytes.
        
        Args:
            data: Bytes do pacote RTP
            
        Returns:
            Tuple (RTPHeader, offset onde payload começa)
        """
        if len(data) < 12:
            raise ValueError(f"RTP packet too short: {len(data)} bytes")
        
        # Primeiro byte: V(2) P(1) X(1) CC(4)
        byte0 = data[0]
        version = (byte0 >> 6) & 0x03
        padding = bool((byte0 >> 5) & 0x01)
        extension = bool((byte0 >> 4) & 0x01)
        cc = byte0 & 0x0F
        
        if version != 2:
            raise ValueError(f"Unsupported RTP version: {version}")
        
        # Segundo byte: M(1) PT(7)
        byte1 = data[1]
        marker = bool((byte1 >> 7) & 0x01)
        payload_type = byte1 & 0x7F
        
        # Sequence (2 bytes), Timestamp (4 bytes), SSRC (4 bytes)
        sequence, timestamp, ssrc = struct.unpack("!HII", data[2:12])
        
        offset = 12
        
        # Parse CSRCs se cc > 0
        csrc = []
        if cc > 0:
            for i in range(cc):
                csrc_id = struct.unpack("!I", data[offset:offset+4])[0]
                csrc.append(csrc_id)
                offset += 4
        
        # Parse extension header se presente
        extension_profile = 0
        extension_data = b""
        
        if extension:
            if len(data) < offset + 4:
                raise ValueError("RTP extension header truncated")
            
            extension_profile, ext_length = struct.unpack("!HH", data[offset:offset+4])
            offset += 4
            
            ext_bytes = ext_length * 4
            if len(data) < offset + ext_bytes:
                raise ValueError("RTP extension data truncated")
            
            extension_data = data[offset:offset+ext_bytes]
            offset += ext_bytes
        
        header = cls(
            version=version,
            padding=padding,
            extension=extension,
            cc=cc,
            marker=marker,
            payload_type=payload_type,
            sequence=sequence,
            timestamp=timestamp,
            ssrc=ssrc,
            csrc=csrc,
            extension_profile=extension_profile,
            extension_data=extension_data,
        )
        
        return header, offset
    
    def to_bytes(self) -> bytes:
        """Serializa header para bytes."""
        # Byte 0
        byte0 = (
            ((self.version & 0x03) << 6) |
            ((1 if self.padding else 0) << 5) |
            ((1 if self.extension else 0) << 4) |
            (self.cc & 0x0F)
        )
        
        # Byte 1
        byte1 = ((1 if self.marker else 0) << 7) | (self.payload_type & 0x7F)
        
        # Header básico
        header = struct.pack(
            "!BBHII",
            byte0,
            byte1,
            self.sequence & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc & 0xFFFFFFFF,
        )
        
        # CSRCs
        for csrc_id in self.csrc[:15]:  # Max 15 CSRCs
            header += struct.pack("!I", csrc_id)
        
        # Extension
        if self.extension and self.extension_data:
            ext_length = (len(self.extension_data) + 3) // 4  # Pad to 4 bytes
            header += struct.pack("!HH", self.extension_profile, ext_length)
            header += self.extension_data.ljust(ext_length * 4, b'\x00')
        
        return header


@dataclass
class RTPPacket:
    """
    Pacote RTP completo (header + payload).
    """
    header: RTPHeader
    payload: bytes
    
    @classmethod
    def parse(cls, data: bytes) -> "RTPPacket":
        """Parse pacote completo de bytes."""
        header, offset = RTPHeader.parse(data)
        payload = data[offset:]
        
        # Handle padding
        if header.padding and payload:
            padding_length = payload[-1]
            payload = payload[:-padding_length]
        
        return cls(header=header, payload=payload)
    
    def to_bytes(self) -> bytes:
        """Serializa pacote completo para bytes."""
        return self.header.to_bytes() + self.payload
    
    @property
    def payload_type(self) -> int:
        return self.header.payload_type
    
    @property
    def sequence(self) -> int:
        return self.header.sequence
    
    @property
    def timestamp(self) -> int:
        return self.header.timestamp
    
    @property
    def ssrc(self) -> int:
        return self.header.ssrc


class RTPPacketBuilder:
    """
    Builder para criar pacotes RTP em sequência.
    Gerencia sequence number, timestamp e SSRC.
    """
    
    def __init__(
        self,
        payload_type: int = PayloadType.PCMU,
        ssrc: Optional[int] = None,
        sample_rate: Optional[int] = None,
    ):
        """
        Args:
            payload_type: Tipo de payload (0=PCMU, 8=PCMA, etc)
            ssrc: SSRC fixo (ou None para gerar aleatório)
            sample_rate: Sample rate (ou auto-detectar do payload type)
        """
        self.payload_type = payload_type
        self.ssrc = ssrc or self._generate_ssrc()
        self.sample_rate = sample_rate or SAMPLE_RATES.get(payload_type, 8000)
        
        self._sequence = 0
        self._timestamp = 0
        self._start_time = time.time()
    
    def _generate_ssrc(self) -> int:
        """Gera SSRC aleatório."""
        import random
        return random.randint(0, 0xFFFFFFFF)
    
    def build(
        self,
        payload: bytes,
        marker: bool = False,
        samples: Optional[int] = None,
    ) -> RTPPacket:
        """
        Constrói um pacote RTP.
        
        Args:
            payload: Dados de áudio
            marker: Marker bit (True para primeiro pacote de talkspurt)
            samples: Número de samples (para incrementar timestamp)
        
        Returns:
            RTPPacket pronto para envio
        """
        header = RTPHeader(
            version=2,
            padding=False,
            extension=False,
            cc=0,
            marker=marker,
            payload_type=self.payload_type,
            sequence=self._sequence,
            timestamp=self._timestamp,
            ssrc=self.ssrc,
        )
        
        packet = RTPPacket(header=header, payload=payload)
        
        # Incrementar sequence (wrap at 65536)
        self._sequence = (self._sequence + 1) & 0xFFFF
        
        # Incrementar timestamp
        if samples is not None:
            self._timestamp += samples
        else:
            # Assumir 20ms de áudio
            samples_per_20ms = self.sample_rate // 50
            self._timestamp += samples_per_20ms
        
        self._timestamp &= 0xFFFFFFFF
        
        return packet
    
    def reset(self) -> None:
        """Reseta sequence e timestamp."""
        self._sequence = 0
        self._timestamp = 0
        self._start_time = time.time()
