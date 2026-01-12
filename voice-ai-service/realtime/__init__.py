# Voice AI Realtime Module
# WebSocket Bridge para comunicação bidirecional FreeSWITCH ↔ AI Providers
#
# Referências dos documentos .context:
# - .context/docs/architecture.md (voice-ai-realtime components)
# - .context/docs/data-flow.md (Fluxo Realtime v2)
# - .context/agents/backend-specialist.md (Factory Pattern)
# - openspec/changes/voice-ai-realtime/design.md (Decision 3: Arquitetura)

from .server import RealtimeServer
from .session import RealtimeSession
from .session_manager import RealtimeSessionManager

__all__ = [
    "RealtimeServer",
    "RealtimeSession", 
    "RealtimeSessionManager",
]
