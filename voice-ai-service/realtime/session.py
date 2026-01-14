"""
Realtime Session - Gerencia uma sessão de conversa.

Referências:
- .context/docs/architecture.md: Session Manager
- .context/docs/data-flow.md: Fluxo Realtime v2
- openspec/changes/voice-ai-realtime/design.md: Decision 3 (RealtimeSession class)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .providers.base import (
    BaseRealtimeProvider,
    ProviderEvent,
    ProviderEventType,
    RealtimeConfig,
)
from .providers.factory import RealtimeProviderFactory
from .utils.resampler import ResamplerPair
from .utils.metrics import get_metrics

logger = logging.getLogger(__name__)


@dataclass
class TranscriptEntry:
    """Entrada no histórico."""
    role: str  # 'user' ou 'assistant'
    text: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class RealtimeSessionConfig:
    """
    Configuração de sessão realtime.
    
    Multi-tenant: domain_uuid OBRIGATÓRIO conforme .context/docs/security.md
    """
    domain_uuid: str
    call_uuid: str
    caller_id: str
    secretary_uuid: str
    secretary_name: str
    provider_name: str = "openai"
    system_prompt: str = ""
    greeting: Optional[str] = None
    farewell: Optional[str] = None
    voice: str = "alloy"
    vad_threshold: float = 0.5
    silence_duration_ms: int = 500
    freeswitch_sample_rate: int = 16000
    idle_timeout_seconds: int = 30
    max_duration_seconds: int = 600
    omniplay_webhook_url: Optional[str] = None


class RealtimeSession:
    """
    Gerencia uma sessão de conversa realtime.
    Uma instância por chamada ativa.
    
    Conforme openspec/changes/voice-ai-realtime/design.md (Decision 3).
    """
    
    def __init__(
        self,
        config: RealtimeSessionConfig,
        on_audio_output: Optional[Callable[[bytes], Any]] = None,
        on_transcript: Optional[Callable[[str, str], Any]] = None,
        on_function_call: Optional[Callable[[str, Dict], Any]] = None,
        on_session_end: Optional[Callable[[str], Any]] = None,
    ):
        self.config = config
        self._on_audio_output = on_audio_output
        self._on_transcript = on_transcript
        self._on_function_call = on_function_call
        self._on_session_end = on_session_end
        
        self._provider: Optional[BaseRealtimeProvider] = None
        self._resampler: Optional[ResamplerPair] = None
        
        self._started = False
        self._ended = False
        self._user_speaking = False
        self._assistant_speaking = False
        
        self._transcript: List[TranscriptEntry] = []
        self._current_assistant_text = ""
        
        self._event_task: Optional[asyncio.Task] = None
        self._timeout_task: Optional[asyncio.Task] = None
        
        self._started_at: Optional[datetime] = None
        self._last_activity: float = time.time()
        self._speech_start_time: Optional[float] = None
        
        self._metrics = get_metrics()
    
    @property
    def call_uuid(self) -> str:
        return self.config.call_uuid
    
    @property
    def domain_uuid(self) -> str:
        return self.config.domain_uuid
    
    @property
    def is_active(self) -> bool:
        return self._started and not self._ended
    
    @property
    def transcript(self) -> List[TranscriptEntry]:
        return self._transcript.copy()
    
    async def start(self) -> None:
        """Inicia a sessão."""
        if self._started:
            return
        
        self._started_at = datetime.now()
        self._started = True
        
        self._metrics.session_started(
            domain_uuid=self.domain_uuid,
            call_uuid=self.call_uuid,
            provider=self.config.provider_name,
        )
        
        try:
            await self._create_provider()
            self._setup_resampler()
            self._event_task = asyncio.create_task(self._event_loop())
            self._timeout_task = asyncio.create_task(self._timeout_monitor())
            
            logger.info("Realtime session started", extra={
                "call_uuid": self.call_uuid,
                "domain_uuid": self.domain_uuid,
                "provider": self.config.provider_name,
            })
        except Exception as e:
            logger.error(f"Failed to start session: {e}")
            await self.stop("error")
            raise
    
    async def _create_provider(self) -> None:
        """Cria e conecta ao provider."""
        # Buscar credenciais do banco (Multi-tenant)
        from services.database import db
        
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT config FROM v_voice_ai_providers
                WHERE domain_uuid = $1 AND provider_type = 'realtime'
                  AND provider_name = $2 AND is_enabled = true
                LIMIT 1
                """,
                self.domain_uuid,
                self.config.provider_name
            )
            if not row:
                raise ValueError(f"Provider '{self.config.provider_name}' not configured")
            # Config pode vir como string JSON ou dict (JSONB)
            raw_config = row["config"]
            if isinstance(raw_config, str):
                import json
                credentials = json.loads(raw_config)
            else:
                credentials = raw_config or {}
        
        provider_config = RealtimeConfig(
            domain_uuid=self.domain_uuid,
            secretary_uuid=self.config.secretary_uuid,
            system_prompt=self.config.system_prompt,
            voice=self.config.voice,
            first_message=self.config.greeting,
            vad_threshold=self.config.vad_threshold,
            silence_duration_ms=self.config.silence_duration_ms,
        )
        
        self._provider = RealtimeProviderFactory.create(
            provider_name=self.config.provider_name,
            credentials=credentials,
            config=provider_config,
        )
        
        await self._provider.connect()
        await self._provider.configure()
    
    def _setup_resampler(self) -> None:
        if self._provider:
            self._resampler = ResamplerPair(
                freeswitch_rate=self.config.freeswitch_sample_rate,
                provider_rate=self._provider.input_sample_rate,
            )
    
    async def handle_audio_input(self, audio_bytes: bytes) -> None:
        """Processa áudio do FreeSWITCH."""
        if not self.is_active or not self._provider:
            return
        
        self._last_activity = time.time()
        
        if self._resampler and self._resampler.input_resampler.needs_resample:
            audio_bytes = self._resampler.resample_input(audio_bytes)
        
        await self._provider.send_audio(audio_bytes)
    
    async def _handle_audio_output(self, audio_bytes: bytes) -> None:
        """
        Processa áudio do provider.
        
        Inclui resampling e buffer warmup de 200ms para playback suave.
        Baseado em: https://github.com/os11k/freeswitch-elevenlabs-bridge
        """
        if not audio_bytes:
            return
        
        if self._resampler:
            # resample_output já inclui o buffer warmup
            audio_bytes = self._resampler.resample_output(audio_bytes)
        
        # Durante warmup, resample_output retorna b""
        if audio_bytes and self._on_audio_output:
            await self._on_audio_output(audio_bytes)
    
    async def _handle_audio_output_direct(self, audio_bytes: bytes) -> None:
        """
        Envia áudio diretamente sem passar pelo buffer.
        Usado para flush do buffer restante.
        """
        if audio_bytes and self._on_audio_output:
            await self._on_audio_output(audio_bytes)
    
    async def interrupt(self) -> None:
        """Barge-in: interrompe resposta."""
        if self._provider and self._assistant_speaking:
            await self._provider.interrupt()
            self._assistant_speaking = False
    
    async def _event_loop(self) -> None:
        """Loop de eventos do provider."""
        if not self._provider:
            return
        
        try:
            async for event in self._provider.receive_events():
                await self._handle_event(event)
                if self._ended:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Event loop error: {e}")
            await self.stop("error")
    
    async def _handle_event(self, event: ProviderEvent) -> None:
        """Processa evento do provider."""
        self._last_activity = time.time()
        
        if event.type == ProviderEventType.RESPONSE_STARTED:
            # Reset buffer para nova resposta (warmup 200ms)
            if self._resampler:
                self._resampler.reset_output_buffer()
            logger.debug("Response started, buffer reset for warmup")
        
        elif event.type == ProviderEventType.AUDIO_DELTA:
            self._assistant_speaking = True
            if event.audio_bytes:
                await self._handle_audio_output(event.audio_bytes)
        
        elif event.type == ProviderEventType.AUDIO_DONE:
            self._assistant_speaking = False
            # Flush buffer restante ao final do áudio
            if self._resampler:
                remaining = self._resampler.flush_output()
                if remaining:
                    await self._handle_audio_output_direct(remaining)
        
        elif event.type == ProviderEventType.TRANSCRIPT_DELTA:
            if event.transcript:
                self._current_assistant_text += event.transcript
        
        elif event.type == ProviderEventType.TRANSCRIPT_DONE:
            if self._current_assistant_text:
                self._transcript.append(TranscriptEntry(role="assistant", text=self._current_assistant_text))
                if self._on_transcript:
                    await self._on_transcript("assistant", self._current_assistant_text)
                self._current_assistant_text = ""
        
        elif event.type == ProviderEventType.USER_TRANSCRIPT:
            if event.transcript:
                self._transcript.append(TranscriptEntry(role="user", text=event.transcript))
                if self._on_transcript:
                    await self._on_transcript("user", event.transcript)
        
        elif event.type == ProviderEventType.SPEECH_STARTED:
            self._user_speaking = True
            self._speech_start_time = time.time()
            if self._assistant_speaking:
                await self.interrupt()
        
        elif event.type == ProviderEventType.SPEECH_STOPPED:
            self._user_speaking = False
        
        elif event.type == ProviderEventType.RESPONSE_DONE:
            if self._speech_start_time:
                self._metrics.record_latency(self.call_uuid, time.time() - self._speech_start_time)
                self._speech_start_time = None
        
        elif event.type == ProviderEventType.FUNCTION_CALL:
            await self._handle_function_call(event)
        
        elif event.type == ProviderEventType.SESSION_ENDED:
            await self.stop("provider_ended")
    
    async def _handle_function_call(self, event: ProviderEvent) -> None:
        """Processa function call."""
        function_name = event.function_name
        function_args = event.function_args or {}
        call_id = event.data.get("call_id", "")
        
        logger.info("Function call", extra={
            "call_uuid": self.call_uuid,
            "function": function_name,
        })
        
        if self._on_function_call:
            result = await self._on_function_call(function_name, function_args)
        else:
            result = await self._execute_function(function_name, function_args)
        
        if self._provider:
            await self._provider.send_function_result(function_name, result, call_id)
    
    async def _execute_function(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Executa função internamente."""
        if name == "transfer_call":
            return {"action": "transfer", "destination": args.get("destination", "")}
        elif name == "end_call":
            asyncio.create_task(self._delayed_stop(2.0, "function_end"))
            return {"status": "ending"}
        return {"error": f"Unknown function: {name}"}
    
    async def _timeout_monitor(self) -> None:
        """Monitora timeouts."""
        while self.is_active:
            await asyncio.sleep(5)
            
            idle_time = time.time() - self._last_activity
            if idle_time > self.config.idle_timeout_seconds:
                await self.stop("idle_timeout")
                return
            
            if self._started_at:
                duration = (datetime.now() - self._started_at).total_seconds()
                if duration > self.config.max_duration_seconds:
                    await self.stop("max_duration")
                    return
    
    async def _delayed_stop(self, delay: float, reason: str) -> None:
        await asyncio.sleep(delay)
        await self.stop(reason)
    
    async def stop(self, reason: str = "normal") -> None:
        """Encerra a sessão."""
        if self._ended:
            return
        
        self._ended = True
        
        for task in [self._event_task, self._timeout_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        if self._provider:
            await self._provider.disconnect()
        
        self._metrics.session_ended(self.call_uuid, reason)
        await self._save_conversation(reason)
        
        if self._on_session_end:
            await self._on_session_end(reason)
        
        logger.info("Realtime session stopped", extra={
            "call_uuid": self.call_uuid,
            "domain_uuid": self.domain_uuid,
            "reason": reason,
        })
    
    async def _save_conversation(self, resolution: str) -> None:
        """Salva conversa no banco."""
        from services.database import db
        
        try:
            pool = await db.get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    conv_uuid = await conn.fetchval(
                        """
                        INSERT INTO v_voice_conversations (
                            domain_uuid, voice_secretary_uuid, caller_id_number, call_uuid,
                            start_time, end_time, final_action, processing_mode
                        ) VALUES ($1, $2, $3, $4, $5, NOW(), $6, 'realtime')
                        RETURNING voice_conversation_uuid
                        """,
                        self.domain_uuid, self.config.secretary_uuid,
                        self.config.caller_id, self.call_uuid,
                        self._started_at, resolution,
                    )
                    
                    for idx, entry in enumerate(self._transcript, 1):
                        await conn.execute(
                            """
                            INSERT INTO v_voice_messages (voice_conversation_uuid, turn_number, role, content, insert_date)
                            VALUES ($1, $2, $3, $4, to_timestamp($5))
                            """,
                            conv_uuid, idx, entry.role, entry.text, entry.timestamp,
                        )
        except Exception as e:
            logger.error(f"Error saving conversation: {e}")
