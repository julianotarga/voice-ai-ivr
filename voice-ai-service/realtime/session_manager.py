"""
Realtime Session Manager - Gerencia todas as sessões ativas.

Referências:
- .context/docs/architecture.md: Session Manager
- .context/docs/security.md: Rate limiting por domain
- openspec/changes/voice-ai-realtime/design.md: Decision 3
"""

import asyncio
import logging
from typing import Callable, Dict, List, Optional

from .session import RealtimeSession, RealtimeSessionConfig

logger = logging.getLogger(__name__)


class RealtimeSessionManager:
    """
    Gerenciador centralizado de sessões realtime.
    
    Multi-tenant: isolamento por domain_uuid, limite por tenant.
    """
    
    def __init__(
        self, 
        max_sessions_per_domain: int = 10,
        session_timeout_seconds: int = 30
    ):
        self.max_sessions_per_domain = max_sessions_per_domain
        self.session_timeout_seconds = session_timeout_seconds
        self._sessions: Dict[str, RealtimeSession] = {}
        self._domain_counts: Dict[str, int] = {}
        self._lock = asyncio.Lock()
    
    @property
    def active_session_count(self) -> int:
        return len(self._sessions)
    
    def get_domain_session_count(self, domain_uuid: str) -> int:
        return self._domain_counts.get(domain_uuid, 0)
    
    async def create_session(
        self,
        config: RealtimeSessionConfig,
        on_audio_output: Optional[Callable] = None,
        on_transcript: Optional[Callable] = None,
        on_function_call: Optional[Callable] = None,
        on_barge_in: Optional[Callable] = None,
        on_transfer: Optional[Callable] = None,
        on_audio_done: Optional[Callable] = None,
    ) -> RealtimeSession:
        """Cria nova sessão."""
        async with self._lock:
            # Rate limiting por domain conforme .context/docs/security.md
            domain_count = self._domain_counts.get(config.domain_uuid, 0)
            if domain_count >= self.max_sessions_per_domain:
                raise ValueError(f"Session limit exceeded for domain {config.domain_uuid}")
            
            if config.call_uuid in self._sessions:
                raise RuntimeError(f"Session already exists: {config.call_uuid}")
            
            session = RealtimeSession(
                config=config,
                on_audio_output=on_audio_output,
                on_transcript=on_transcript,
                on_function_call=on_function_call,
                on_session_end=lambda reason: self._on_session_end(config.call_uuid, reason),
                on_barge_in=on_barge_in,
                on_transfer=on_transfer,
                on_audio_done=on_audio_done,
            )
            
            self._sessions[config.call_uuid] = session
            self._domain_counts[config.domain_uuid] = domain_count + 1
            
            logger.info("Session created", extra={
                "call_uuid": config.call_uuid,
                "domain_uuid": config.domain_uuid,
                "active_sessions": len(self._sessions),
            })
        
        await session.start()
        return session
    
    def get_session(self, call_uuid: str) -> Optional[RealtimeSession]:
        return self._sessions.get(call_uuid)
    
    async def remove_session(self, call_uuid: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(call_uuid, None)
            if not session:
                return False
            
            domain_uuid = session.domain_uuid
            if domain_uuid in self._domain_counts:
                self._domain_counts[domain_uuid] -= 1
                if self._domain_counts[domain_uuid] <= 0:
                    del self._domain_counts[domain_uuid]
        
        # CORREÇÃO: Notificar ESL EventRelay que sessão terminou (modo dual)
        # Isso permite que o relay pare de tentar correlação
        try:
            from .esl.event_relay import notify_session_ended
            notify_session_ended(call_uuid)
        except ImportError:
            pass  # ESL module não disponível
        
        return True
    
    async def stop_session(self, call_uuid: str, reason: str = "manager_stop") -> bool:
        session = self.get_session(call_uuid)
        if not session:
            return False
        
        await session.stop(reason)
        await self.remove_session(call_uuid)
        return True
    
    async def stop_all_sessions(self, reason: str = "shutdown") -> int:
        call_uuids = list(self._sessions.keys())
        count = 0
        for call_uuid in call_uuids:
            if await self.stop_session(call_uuid, reason):
                count += 1
        return count
    
    async def _on_session_end(self, call_uuid: str, reason: str) -> None:
        await self.remove_session(call_uuid)
    
    async def route_audio(self, call_uuid: str, audio_bytes: bytes) -> bool:
        """Roteia áudio para sessão."""
        session = self.get_session(call_uuid)
        if not session or not session.is_active:
            return False
        
        await session.handle_audio_input(audio_bytes)
        return True
    
    def get_all_sessions(self) -> List[RealtimeSession]:
        """Retorna todas as sessões ativas."""
        return list(self._sessions.values())
    
    def get_sessions_by_domain(self, domain_uuid: str) -> List[RealtimeSession]:
        """Retorna sessões de um domínio específico."""
        return [s for s in self._sessions.values() if s.domain_uuid == domain_uuid]
    
    async def cleanup_expired_sessions(self) -> int:
        """Limpa sessões expiradas."""
        import time
        
        expired = []
        for call_uuid, session in self._sessions.items():
            if hasattr(session, '_last_activity'):
                idle_time = time.time() - session._last_activity
                if idle_time > self.session_timeout_seconds:
                    expired.append(call_uuid)
        
        count = 0
        for call_uuid in expired:
            await self.stop_session(call_uuid, "expired")
            count += 1
        
        return count
    
    def get_stats(self) -> Dict:
        return {
            "total_sessions": len(self._sessions),
            "sessions_by_domain": dict(self._domain_counts),
            "max_per_domain": self.max_sessions_per_domain,
        }


_manager: Optional[RealtimeSessionManager] = None

def get_session_manager() -> RealtimeSessionManager:
    global _manager
    if _manager is None:
        _manager = RealtimeSessionManager()
    return _manager
