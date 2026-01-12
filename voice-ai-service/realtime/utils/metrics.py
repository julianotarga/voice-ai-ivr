"""
Métricas Prometheus para o Realtime Bridge.

Referências:
- openspec/changes/voice-ai-realtime/design.md: Decision 9 (Métricas)
- .context/agents/backend-specialist.md: Logs estruturados
"""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Histogram, Gauge
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


@dataclass
class SessionMetrics:
    """Métricas de uma sessão."""
    domain_uuid: str
    call_uuid: str
    provider: str
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    audio_chunks_received: int = 0
    audio_chunks_sent: int = 0
    turns_completed: int = 0
    response_latencies: list = field(default_factory=list)
    
    @property
    def duration_seconds(self) -> float:
        return (self.ended_at or time.time()) - self.started_at
    
    @property
    def avg_latency_ms(self) -> float:
        return sum(self.response_latencies) / len(self.response_latencies) if self.response_latencies else 0.0


class RealtimeMetrics:
    """Gerenciador de métricas (Prometheus ou fallback)."""
    
    def __init__(self):
        self._sessions: Dict[str, SessionMetrics] = {}
        
        if PROMETHEUS_AVAILABLE:
            self._init_prometheus()
    
    def _init_prometheus(self):
        self.calls_total = Counter('voice_ai_realtime_calls_total', 'Total calls', ['domain_uuid', 'provider', 'outcome'])
        self.audio_chunks = Counter('voice_ai_realtime_audio_chunks_total', 'Audio chunks', ['domain_uuid', 'direction'])
        self.response_latency = Histogram('voice_ai_realtime_response_latency_seconds', 'Response latency', 
            ['domain_uuid', 'provider'], buckets=[0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0])
        self.active_sessions = Gauge('voice_ai_realtime_active_sessions', 'Active sessions', ['domain_uuid', 'provider'])
    
    def session_started(self, domain_uuid: str, call_uuid: str, provider: str) -> SessionMetrics:
        metrics = SessionMetrics(domain_uuid=domain_uuid, call_uuid=call_uuid, provider=provider)
        self._sessions[call_uuid] = metrics
        
        if PROMETHEUS_AVAILABLE:
            self.active_sessions.labels(domain_uuid=domain_uuid, provider=provider).inc()
        
        logger.info("Realtime session started", extra={"domain_uuid": domain_uuid, "call_uuid": call_uuid, "provider": provider})
        return metrics
    
    def session_ended(self, call_uuid: str, outcome: str = "completed") -> Optional[SessionMetrics]:
        metrics = self._sessions.pop(call_uuid, None)
        if not metrics:
            return None
        
        metrics.ended_at = time.time()
        
        if PROMETHEUS_AVAILABLE:
            self.calls_total.labels(domain_uuid=metrics.domain_uuid, provider=metrics.provider, outcome=outcome).inc()
            self.active_sessions.labels(domain_uuid=metrics.domain_uuid, provider=metrics.provider).dec()
        
        logger.info("Realtime session ended", extra={
            "domain_uuid": metrics.domain_uuid,
            "call_uuid": call_uuid,
            "outcome": outcome,
            "duration_seconds": metrics.duration_seconds,
            "avg_latency_ms": metrics.avg_latency_ms,
        })
        return metrics
    
    def record_latency(self, call_uuid: str, latency_seconds: float):
        metrics = self._sessions.get(call_uuid)
        if metrics:
            metrics.response_latencies.append(latency_seconds * 1000)
            metrics.turns_completed += 1
            if PROMETHEUS_AVAILABLE:
                self.response_latency.labels(domain_uuid=metrics.domain_uuid, provider=metrics.provider).observe(latency_seconds)
    
    @contextmanager
    def measure_latency(self, call_uuid: str):
        start = time.time()
        try:
            yield
        finally:
            self.record_latency(call_uuid, time.time() - start)


_metrics: Optional[RealtimeMetrics] = None

def get_metrics() -> RealtimeMetrics:
    global _metrics
    if _metrics is None:
        _metrics = RealtimeMetrics()
    return _metrics
