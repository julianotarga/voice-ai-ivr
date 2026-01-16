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
    audio_bytes_received: int = 0
    audio_bytes_sent: int = 0
    turns_completed: int = 0
    response_latencies: list = field(default_factory=list)
    playback_underruns: int = 0
    barge_in_count: int = 0
    health_score: float = 100.0
    
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
        # Métricas de sessão
        self.calls_total = Counter('voice_ai_realtime_calls_total', 'Total calls', ['domain_uuid', 'provider', 'outcome'])
        self.audio_chunks = Counter('voice_ai_realtime_audio_chunks_total', 'Audio chunks', ['domain_uuid', 'direction'])
        self.audio_bytes = Counter('voice_ai_realtime_audio_bytes_total', 'Audio bytes', ['domain_uuid', 'direction'])
        self.response_latency = Histogram('voice_ai_realtime_response_latency_seconds', 'Response latency', 
            ['domain_uuid', 'provider'], buckets=[0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0])
        self.active_sessions = Gauge('voice_ai_realtime_active_sessions', 'Active sessions', ['domain_uuid', 'provider'])
        self.health_score = Gauge('voice_ai_realtime_health_score', 'Realtime health score (0-100)', ['domain_uuid', 'provider'])
        
        # FASE 6: Métricas de Transfer/Handoff
        self.transfers_total = Counter(
            'voice_ai_transfers_total', 
            'Total transfer attempts', 
            ['domain_uuid', 'status', 'destination_type']
        )
        self.transfers_duration = Histogram(
            'voice_ai_transfers_duration_seconds', 
            'Transfer duration (ring to answer/hangup)', 
            ['domain_uuid', 'status'],
            buckets=[5, 10, 15, 20, 30, 45, 60, 90, 120]
        )
        
        # FASE 6: Métricas de Callback
        self.callbacks_total = Counter(
            'voice_ai_callbacks_total', 
            'Total callbacks created', 
            ['domain_uuid', 'status']
        )
        self.callbacks_completion_time = Histogram(
            'voice_ai_callbacks_completion_seconds', 
            'Time from callback creation to completion', 
            ['domain_uuid'],
            buckets=[60, 300, 600, 1800, 3600, 7200, 14400, 28800, 86400]
        )
        
        # FASE 6: Métricas de Extension Status
        self.extension_checks_total = Counter(
            'voice_ai_extension_checks_total', 
            'Extension availability checks', 
            ['domain_uuid', 'status']
        )
        
        # FASE 6: Métricas de Click-to-Call
        self.click_to_call_total = Counter(
            'voice_ai_click_to_call_total', 
            'Click-to-call originations', 
            ['domain_uuid', 'status']
        )
    
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
            self.health_score.labels(domain_uuid=metrics.domain_uuid, provider=metrics.provider).set(metrics.health_score)
        
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

    def record_audio(self, call_uuid: str, direction: str, byte_count: int) -> None:
        metrics = self._sessions.get(call_uuid)
        if metrics:
            if direction == "in":
                metrics.audio_chunks_received += 1
                metrics.audio_bytes_received += byte_count
            else:
                metrics.audio_chunks_sent += 1
                metrics.audio_bytes_sent += byte_count
            if PROMETHEUS_AVAILABLE:
                self.audio_chunks.labels(domain_uuid=metrics.domain_uuid, direction=direction).inc()
                self.audio_bytes.labels(domain_uuid=metrics.domain_uuid, direction=direction).inc(byte_count)

    def record_playback_underrun(self, call_uuid: str) -> None:
        metrics = self._sessions.get(call_uuid)
        if metrics:
            metrics.playback_underruns += 1

    def record_barge_in(self, call_uuid: str) -> None:
        metrics = self._sessions.get(call_uuid)
        if metrics:
            metrics.barge_in_count += 1

    def update_health_score(self, call_uuid: str, score: float) -> None:
        metrics = self._sessions.get(call_uuid)
        if metrics:
            metrics.health_score = max(0.0, min(100.0, score))
            if PROMETHEUS_AVAILABLE:
                self.health_score.labels(domain_uuid=metrics.domain_uuid, provider=metrics.provider).set(metrics.health_score)

    def update_provider(self, call_uuid: str, provider: str) -> None:
        metrics = self._sessions.get(call_uuid)
        if metrics:
            metrics.provider = provider

    def get_session_metrics(self, call_uuid: str) -> Optional[SessionMetrics]:
        return self._sessions.get(call_uuid)
    
    def get_avg_latency(self, call_uuid: str) -> Optional[float]:
        """Retorna latência média em ms para uma sessão."""
        metrics = self._sessions.get(call_uuid)
        if metrics:
            return metrics.avg_latency_ms
        return None
    
    @contextmanager
    def measure_latency(self, call_uuid: str):
        start = time.time()
        try:
            yield
        finally:
            self.record_latency(call_uuid, time.time() - start)
    
    # =========================================================================
    # FASE 6: Métodos para Transfer/Callback
    # =========================================================================
    
    def record_transfer(
        self,
        call_uuid: str,
        status: str,
        destination: Optional[str] = None,
        destination_type: str = "extension",
        duration_ms: Optional[int] = None
    ) -> None:
        """
        Registra uma tentativa de transferência.
        
        Args:
            call_uuid: UUID da chamada
            status: success, no_answer, busy, offline, cancelled, failed
            destination: Nome do destino (para logging)
            destination_type: extension, ring_group, queue, external
            duration_ms: Duração do ring até resposta/desistência
        """
        metrics = self._sessions.get(call_uuid)
        domain_uuid = metrics.domain_uuid if metrics else "unknown"
        
        if PROMETHEUS_AVAILABLE:
            self.transfers_total.labels(
                domain_uuid=domain_uuid,
                status=status,
                destination_type=destination_type
            ).inc()
            
            if duration_ms:
                self.transfers_duration.labels(
                    domain_uuid=domain_uuid,
                    status=status
                ).observe(duration_ms / 1000.0)
        
        logger.info(
            "Transfer recorded",
            extra={
                "call_uuid": call_uuid,
                "domain_uuid": domain_uuid,
                "status": status,
                "destination": destination,
                "destination_type": destination_type,
                "duration_ms": duration_ms,
            }
        )
    
    def record_callback_created(
        self,
        domain_uuid: str,
        ticket_id: int,
        intended_for: Optional[str] = None,
        scheduled: bool = False
    ) -> None:
        """
        Registra criação de callback.
        
        Args:
            domain_uuid: UUID do tenant
            ticket_id: ID do ticket
            intended_for: Nome do destino pretendido
            scheduled: Se é agendado ou imediato
        """
        if PROMETHEUS_AVAILABLE:
            self.callbacks_total.labels(
                domain_uuid=domain_uuid,
                status="created"
            ).inc()
        
        logger.info(
            "Callback created",
            extra={
                "domain_uuid": domain_uuid,
                "ticket_id": ticket_id,
                "intended_for": intended_for,
                "scheduled": scheduled,
            }
        )
    
    def record_callback_completed(
        self,
        domain_uuid: str,
        ticket_id: int,
        status: str,
        wait_time_seconds: Optional[float] = None
    ) -> None:
        """
        Registra conclusão de callback.
        
        Args:
            domain_uuid: UUID do tenant
            ticket_id: ID do ticket
            status: completed, expired, cancelled, failed
            wait_time_seconds: Tempo entre criação e conclusão
        """
        if PROMETHEUS_AVAILABLE:
            self.callbacks_total.labels(
                domain_uuid=domain_uuid,
                status=status
            ).inc()
            
            if wait_time_seconds and status == "completed":
                self.callbacks_completion_time.labels(
                    domain_uuid=domain_uuid
                ).observe(wait_time_seconds)
        
        logger.info(
            "Callback completed",
            extra={
                "domain_uuid": domain_uuid,
                "ticket_id": ticket_id,
                "status": status,
                "wait_time_seconds": wait_time_seconds,
            }
        )
    
    def record_extension_check(
        self,
        domain_uuid: str,
        extension: str,
        status: str,
        available: bool
    ) -> None:
        """
        Registra verificação de disponibilidade de ramal.
        
        Args:
            domain_uuid: UUID do tenant
            extension: Número do ramal
            status: available, in_call, dnd, offline, unknown
            available: Se está disponível
        """
        if PROMETHEUS_AVAILABLE:
            self.extension_checks_total.labels(
                domain_uuid=domain_uuid,
                status=status
            ).inc()
        
        logger.debug(
            "Extension check",
            extra={
                "domain_uuid": domain_uuid,
                "extension": extension,
                "status": status,
                "available": available,
            }
        )
    
    def record_click_to_call(
        self,
        domain_uuid: str,
        extension: str,
        client_number: str,
        ticket_id: Optional[int],
        status: str
    ) -> None:
        """
        Registra originação de click-to-call.
        
        Args:
            domain_uuid: UUID do tenant
            extension: Ramal do atendente
            client_number: Número do cliente
            ticket_id: ID do ticket de callback
            status: initiated, connected, failed, agent_no_answer, client_no_answer
        """
        if PROMETHEUS_AVAILABLE:
            self.click_to_call_total.labels(
                domain_uuid=domain_uuid,
                status=status
            ).inc()
        
        logger.info(
            "Click-to-call recorded",
            extra={
                "domain_uuid": domain_uuid,
                "extension": extension,
                "client_number": client_number,
                "ticket_id": ticket_id,
                "status": status,
            }
        )


_metrics: Optional[RealtimeMetrics] = None

def get_metrics() -> RealtimeMetrics:
    global _metrics
    if _metrics is None:
        _metrics = RealtimeMetrics()
    return _metrics
