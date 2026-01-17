# ESL (Event Socket Library) Module
# FreeSWITCH direct integration via greenswitch
#
# Components:
# - server.py: ESL Outbound Server (receives calls from FS)
# - application.py: Voice AI Application handler (RTP mode)
# - event_relay.py: Dual mode event relay (WebSocket + ESL)
#
# Referências:
# - https://github.com/EvoluxBR/greenswitch
# - openspec/changes/refactor-esl-rtp-bridge/
# - openspec/changes/dual-mode-esl-websocket/

from .server import ESLOutboundServer, create_server
from .application import VoiceAIApplication
from .event_relay import (
    DualModeEventRelay,
    create_event_relay,
    set_main_asyncio_loop,
    get_main_asyncio_loop,
)

__all__ = [
    # Server
    "ESLOutboundServer",
    "create_server",
    # RTP Mode
    "VoiceAIApplication",
    # Dual Mode
    "DualModeEventRelay",
    "create_event_relay",
    "set_main_asyncio_loop",
    "get_main_asyncio_loop",
]
