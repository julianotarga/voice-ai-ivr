# Realtime handlers
# Conforme openspec/changes/voice-ai-realtime/design.md (Decision 3)

from .freeswitch import FreeSwitchHandler
from .function_call import FunctionCallHandler
from .handoff import HandoffHandler, HandoffConfig, HandoffResult, TranscriptEntry

# FASE 1: Handoff Inteligente
# Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
from .transfer_destination_loader import (
    TransferDestination,
    TransferDestinationLoader,
    get_destination_loader,
)
from .esl_client import (
    AsyncESLClient,
    ESLEvent,
    ESLError,
    ESLConnectionError,
    ESLCommandError,
    OriginateResult,
    get_esl_client,
)
from .transfer_manager import (
    TransferManager,
    TransferStatus,
    TransferResult,
    create_transfer_manager,
    HANGUP_CAUSE_MAP,
    STATUS_MESSAGES,
)

# FASE 2: Callback Handler
# Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
from .callback_handler import (
    CallbackHandler,
    CallbackData,
    CallbackResult,
    CallbackStatus,
    PhoneNumberUtils,
    ResponseAnalyzer,
    CALLBACK_FUNCTION_DEFINITIONS,
)

# FASE 3: Conference Transfer (mod_conference)
# Ref: voice-ai-ivr/docs/announced-transfer-conference.md
from .transfer_manager_conference import (
    ConferenceTransferManager,
    ConferenceTransferResult,
    ConferenceTransferConfig,
    TransferDecision,
)
from .realtime_announcement_conference import (
    ConferenceAnnouncementSession,
    ConferenceAnnouncementResult,
)

__all__ = [
    # Legacy handlers
    "FreeSwitchHandler",
    "FunctionCallHandler",
    "HandoffHandler",
    "HandoffConfig",
    "HandoffResult",
    "TranscriptEntry",
    # Transfer Destination Loader
    "TransferDestination",
    "TransferDestinationLoader",
    "get_destination_loader",
    # ESL Client
    "AsyncESLClient",
    "ESLEvent",
    "ESLError",
    "ESLConnectionError",
    "ESLCommandError",
    "OriginateResult",
    "get_esl_client",
    # Transfer Manager
    "TransferManager",
    "TransferStatus",
    "TransferResult",
    "create_transfer_manager",
    "HANGUP_CAUSE_MAP",
    "STATUS_MESSAGES",
    # Callback Handler (FASE 2)
    "CallbackHandler",
    "CallbackData",
    "CallbackResult",
    "CallbackStatus",
    "PhoneNumberUtils",
    "ResponseAnalyzer",
    "CALLBACK_FUNCTION_DEFINITIONS",
    # Conference Transfer (FASE 3)
    "ConferenceTransferManager",
    "ConferenceTransferResult",
    "ConferenceTransferConfig",
    "TransferDecision",
    "ConferenceAnnouncementSession",
    "ConferenceAnnouncementResult",
]
