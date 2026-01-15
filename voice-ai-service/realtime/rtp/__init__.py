# RTP (Real-time Transport Protocol) Module
# Direct UDP audio transport for Voice AI
#
# Components:
# - protocol.py: RTP packet parser/builder (RFC 3550)
# - bridge.py: RTP Bridge for audio I/O
# - jitter_buffer.py: Adaptive jitter buffer
# - port_pool.py: UDP port allocation pool
#
# Referências:
# - RFC 3550: RTP Protocol
# - openspec/changes/refactor-esl-rtp-bridge/

from .protocol import RTPHeader, RTPPacket, RTPPacketBuilder, PayloadType
from .bridge import RTPBridge, RTPBridgeConfig
from .jitter_buffer import JitterBuffer, JitterStats
from .port_pool import PortPool, get_port_pool

__all__ = [
    # Protocol
    "RTPHeader",
    "RTPPacket",
    "RTPPacketBuilder",
    "PayloadType",
    # Bridge
    "RTPBridge",
    "RTPBridgeConfig",
    # Jitter Buffer
    "JitterBuffer",
    "JitterStats",
    # Port Pool
    "PortPool",
    "get_port_pool",
]
