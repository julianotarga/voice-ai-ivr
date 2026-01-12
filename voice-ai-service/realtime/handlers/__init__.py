# Realtime handlers
# Conforme openspec/changes/voice-ai-realtime/design.md (Decision 3)

from .freeswitch import FreeSwitchHandler
from .function_call import FunctionCallHandler

__all__ = [
    "FreeSwitchHandler",
    "FunctionCallHandler",
]
