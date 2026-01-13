# Voice AI Realtime Module
# WebSocket Bridge para comunicação bidirecional FreeSWITCH ↔ AI Providers
#
# Referências dos documentos .context:
# - .context/docs/architecture.md (voice-ai-realtime components)
# - .context/docs/data-flow.md (Fluxo Realtime v2)
# - .context/agents/backend-specialist.md (Factory Pattern)
# - openspec/changes/voice-ai-realtime/design.md (Decision 3: Arquitetura)

# Lazy imports para evitar RuntimeWarning quando executado como módulo
# Use: from realtime.server import RealtimeServer
# Ou:  from realtime import get_server_class

__all__ = [
    "RealtimeServer",
    "RealtimeSession", 
    "RealtimeSessionManager",
]


def __getattr__(name: str):
    """
    Lazy import para evitar circular imports e RuntimeWarning.
    
    Quando executamos 'python -m realtime.server', o __init__.py é
    carregado antes do server.py ser executado como __main__.
    Imports diretos causam o warning 'found in sys.modules'.
    """
    if name == "RealtimeServer":
        from .server import RealtimeServer
        return RealtimeServer
    elif name == "RealtimeSession":
        from .session import RealtimeSession
        return RealtimeSession
    elif name == "RealtimeSessionManager":
        from .session_manager import RealtimeSessionManager
        return RealtimeSessionManager
    raise AttributeError(f"module 'realtime' has no attribute {name!r}")
