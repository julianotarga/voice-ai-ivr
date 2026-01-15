# ESL (Event Socket Library) Module
# FreeSWITCH direct integration via greenswitch
#
# Components:
# - server.py: ESL Outbound Server (receives calls from FS)
# - application.py: Voice AI Application handler
#
# Referências:
# - https://github.com/EvoluxBR/greenswitch
# - openspec/changes/refactor-esl-rtp-bridge/

from .server import ESLOutboundServer, create_server
from .application import VoiceAIApplication

__all__ = [
    "ESLOutboundServer",
    "create_server",
    "VoiceAIApplication",
]
