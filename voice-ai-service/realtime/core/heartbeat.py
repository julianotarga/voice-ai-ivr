"""
HeartbeatMonitor - Monitor de saúde da conexão.

Detecta problemas de conexão ANTES do FreeSWITCH reportar,
permitindo ações proativas como reconexão ou encerramento gracioso.

Referência: voice-ai-ivr/docs/PLANO-ARQUITETURA-INTERNA.md
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List

from .events import VoiceEvent, VoiceEventType
from .event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class ConnectionHealth:
    """
    Estado de saúde da conexão.
    
    Atualizado continuamente pelo HeartbeatMonitor.
    """
    
    # Timestamps de última atividade
    last_audio_received: float = 0.0    # Última vez que recebeu áudio do caller
    last_audio_sent: float = 0.0        # Última vez que enviou áudio para caller
    last_provider_response: float = 0.0 # Última vez que OpenAI respondeu
    last_websocket_activity: float = 0.0  # Última atividade no WebSocket
    
    # Métricas de buffer
    audio_buffer_bytes: int = 0         # Bytes no buffer de saída
    pending_audio_bytes: int = 0        # Bytes aguardando envio
    
    # Métricas de latência (ms)
    websocket_latency_ms: float = 0.0   # Latência do WebSocket
    provider_latency_ms: float = 0.0    # Latência do OpenAI
    
    # Contadores
    audio_chunks_received: int = 0      # Total de chunks recebidos do caller
    audio_chunks_sent: int = 0          # Total de chunks enviados para caller
    health_checks: int = 0              # Número de verificações realizadas
    
    # Estado atual
    is_healthy: bool = True             # Conexão está saudável?
    issues: List[str] = field(default_factory=list)  # Problemas detectados


class HeartbeatMonitor:
    """
    Monitor de saúde da conexão.
    
    Funcionalidades:
    - Detecta silêncio prolongado (caller pode ter desligado)
    - Detecta timeout do provider (OpenAI lento)
    - Monitora tamanho do buffer de áudio
    - Emite eventos quando detecta problemas
    
    Vantagens:
    - Não depende do FreeSWITCH para detectar problemas
    - Detecção proativa (antes do ESL reportar HANGUP)
    - Métricas úteis para debug
    """
    
    def __init__(
        self,
        call_uuid: str,
        event_bus: EventBus,
        check_interval: float = 1.0,
        audio_silence_threshold: float = 15.0,
        provider_timeout_threshold: float = 30.0,
        buffer_low_threshold: int = 1280  # 2 chunks de 20ms
    ):
        """
        Inicializa HeartbeatMonitor.
        
        Args:
            call_uuid: UUID da chamada
            event_bus: EventBus para emitir eventos
            check_interval: Intervalo entre verificações (segundos)
            audio_silence_threshold: Tempo sem áudio antes de alertar
            provider_timeout_threshold: Tempo sem resposta do provider
            buffer_low_threshold: Bytes mínimos no buffer
        """
        self.call_uuid = call_uuid
        self.events = event_bus
        self.check_interval = check_interval
        self.audio_silence_threshold = audio_silence_threshold
        self.provider_timeout_threshold = provider_timeout_threshold
        self.buffer_low_threshold = buffer_low_threshold
        
        self.health = ConnectionHealth()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._paused = False
        
        # Debounce: evitar emitir eventos repetidos
        self._last_audio_silence_event: float = 0.0
        self._last_provider_timeout_event: float = 0.0
        self._debounce_interval: float = 10.0  # Segundos entre eventos do mesmo tipo
    
    # ========================================
    # ATUALIZAÇÕES - Chamados pelos componentes
    # ========================================
    
    def audio_received(self, chunk_size: int = 640) -> None:
        """
        Chamado quando recebe áudio do caller.
        
        Args:
            chunk_size: Tamanho do chunk em bytes
        """
        self.health.last_audio_received = time.time()
        self.health.audio_chunks_received += 1
    
    def audio_sent(self, chunk_size: int = 640) -> None:
        """
        Chamado quando envia áudio para caller.
        
        Args:
            chunk_size: Tamanho do chunk em bytes
        """
        self.health.last_audio_sent = time.time()
        self.health.audio_chunks_sent += 1
    
    def provider_responded(self) -> None:
        """Chamado quando OpenAI retorna resposta"""
        self.health.last_provider_response = time.time()
    
    def websocket_activity(self) -> None:
        """Chamado em qualquer atividade do WebSocket"""
        self.health.last_websocket_activity = time.time()
    
    def update_buffer(self, pending_bytes: int, buffer_bytes: int = 0) -> None:
        """
        Atualiza métricas de buffer.
        
        Args:
            pending_bytes: Bytes aguardando reprodução
            buffer_bytes: Bytes no buffer de warmup
        """
        self.health.pending_audio_bytes = pending_bytes
        self.health.audio_buffer_bytes = buffer_bytes
    
    def update_latency(
        self,
        websocket_ms: Optional[float] = None,
        provider_ms: Optional[float] = None
    ) -> None:
        """
        Atualiza métricas de latência.
        
        Args:
            websocket_ms: Latência do WebSocket
            provider_ms: Latência do provider
        """
        if websocket_ms is not None:
            self.health.websocket_latency_ms = websocket_ms
        if provider_ms is not None:
            self.health.provider_latency_ms = provider_ms
    
    # ========================================
    # CONTROLE - Pausar/Retomar
    # ========================================
    
    def pause(self) -> None:
        """
        Pausa monitoramento.
        
        Útil durante transferência para não gerar falsos positivos.
        """
        self._paused = True
        logger.debug(
            "HeartbeatMonitor paused",
            extra={"call_uuid": self.call_uuid}
        )
    
    def resume(self) -> None:
        """
        Retoma monitoramento.
        
        Reseta timestamps para evitar falsos positivos.
        """
        self._paused = False
        
        # Resetar timestamps para evitar alertas imediatos
        now = time.time()
        self.health.last_audio_received = now
        self.health.last_provider_response = now
        self.health.last_websocket_activity = now
        
        # Resetar debounce
        self._last_audio_silence_event = 0.0
        self._last_provider_timeout_event = 0.0
        
        logger.debug(
            "HeartbeatMonitor resumed",
            extra={"call_uuid": self.call_uuid}
        )
    
    # ========================================
    # CICLO DE VIDA
    # ========================================
    
    async def start(self) -> None:
        """Inicia monitoramento em background"""
        if self._running:
            return
        
        self._running = True
        
        # Inicializar timestamps
        now = time.time()
        self.health.last_audio_received = now
        self.health.last_provider_response = now
        self.health.last_websocket_activity = now
        
        self._task = asyncio.create_task(self._monitor_loop())
        
        logger.info(
            "HeartbeatMonitor started",
            extra={
                "call_uuid": self.call_uuid,
                "check_interval": self.check_interval,
                "audio_silence_threshold": self.audio_silence_threshold
            }
        )
    
    async def stop(self) -> None:
        """Para monitoramento"""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        logger.info(
            "HeartbeatMonitor stopped",
            extra={
                "call_uuid": self.call_uuid,
                "health_checks": self.health.health_checks
            }
        )
    
    # ========================================
    # MONITORAMENTO
    # ========================================
    
    async def _monitor_loop(self) -> None:
        """Loop principal de monitoramento"""
        while self._running:
            try:
                if not self._paused:
                    await self._check_health()
                
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"HeartbeatMonitor error: {e}",
                    extra={"call_uuid": self.call_uuid},
                    exc_info=True
                )
    
    async def _check_health(self) -> None:
        """Verifica saúde da conexão"""
        now = time.time()
        issues: List[str] = []
        self.health.health_checks += 1
        
        # 1. Verificar áudio recebido do caller
        if self.health.last_audio_received > 0:
            audio_gap = now - self.health.last_audio_received
            
            if audio_gap > self.audio_silence_threshold:
                issue = f"no_audio_for_{audio_gap:.1f}s"
                issues.append(issue)
                
                # Debounce: só emitir evento se passou tempo suficiente
                if now - self._last_audio_silence_event > self._debounce_interval:
                    self._last_audio_silence_event = now
                    
                    await self.events.emit(VoiceEvent(
                        type=VoiceEventType.CONNECTION_DEGRADED,
                        call_uuid=self.call_uuid,
                        data={
                            "reason": "audio_silence",
                            "gap_seconds": audio_gap,
                            "threshold": self.audio_silence_threshold,
                            "source": "heartbeat"
                        },
                        source="heartbeat"
                    ))
                    
                    logger.warning(
                        f"Audio silence detected: {audio_gap:.1f}s",
                        extra={"call_uuid": self.call_uuid}
                    )
        
        # 2. Verificar resposta do provider
        if self.health.last_provider_response > 0:
            provider_gap = now - self.health.last_provider_response
            
            if provider_gap > self.provider_timeout_threshold:
                issue = f"provider_silent_{provider_gap:.1f}s"
                issues.append(issue)
                
                # Debounce: só emitir evento se passou tempo suficiente
                if now - self._last_provider_timeout_event > self._debounce_interval:
                    self._last_provider_timeout_event = now
                    
                    await self.events.emit(VoiceEvent(
                        type=VoiceEventType.PROVIDER_TIMEOUT,
                        call_uuid=self.call_uuid,
                        data={
                            "gap_seconds": provider_gap,
                            "threshold": self.provider_timeout_threshold,
                            "source": "heartbeat"
                        },
                        source="heartbeat"
                    ))
                    
                    logger.warning(
                        f"Provider timeout: {provider_gap:.1f}s",
                        extra={"call_uuid": self.call_uuid}
                    )
        
        # 3. Verificar buffer de áudio baixo
        # Só verificar se estava falando (pending > 0 recentemente)
        if 0 < self.health.pending_audio_bytes < self.buffer_low_threshold:
            await self.events.emit(VoiceEvent(
                type=VoiceEventType.AI_AUDIO_BUFFER_LOW,
                call_uuid=self.call_uuid,
                data={
                    "buffer_bytes": self.health.pending_audio_bytes,
                    "threshold": self.buffer_low_threshold
                },
                source="heartbeat"
            ))
        
        # Atualizar estado
        self.health.issues = issues
        self.health.is_healthy = len(issues) == 0
        
        # Log periódico de saúde (a cada 30 verificações = ~30s)
        if self.health.health_checks % 30 == 0:
            logger.debug(
                f"Health check #{self.health.health_checks}",
                extra={
                    "call_uuid": self.call_uuid,
                    "is_healthy": self.health.is_healthy,
                    "chunks_received": self.health.audio_chunks_received,
                    "chunks_sent": self.health.audio_chunks_sent,
                }
            )
    
    def get_health_summary(self) -> dict:
        """Retorna resumo do estado de saúde para API/debug"""
        now = time.time()
        return {
            "is_healthy": self.health.is_healthy,
            "issues": self.health.issues,
            "seconds_since_audio": now - self.health.last_audio_received if self.health.last_audio_received > 0 else None,
            "seconds_since_provider": now - self.health.last_provider_response if self.health.last_provider_response > 0 else None,
            "audio_chunks_received": self.health.audio_chunks_received,
            "audio_chunks_sent": self.health.audio_chunks_sent,
            "pending_audio_bytes": self.health.pending_audio_bytes,
            "websocket_latency_ms": self.health.websocket_latency_ms,
            "provider_latency_ms": self.health.provider_latency_ms,
            "health_checks": self.health.health_checks,
            "paused": self._paused,
        }
