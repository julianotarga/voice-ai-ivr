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
from .handlers.handoff import HandoffHandler, HandoffConfig, HandoffResult

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
    prefix_padding_ms: int = 300
    freeswitch_sample_rate: int = 16000
    idle_timeout_seconds: int = 30
    max_duration_seconds: int = 600
    omniplay_webhook_url: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    max_response_output_tokens: int = 4096
    fallback_providers: List[str] = field(default_factory=list)
    barge_in_enabled: bool = True
    # Handoff configuration
    handoff_enabled: bool = True
    handoff_timeout_ms: int = 30000
    handoff_keywords: List[str] = field(default_factory=lambda: ["atendente", "humano", "pessoa", "operador"])
    handoff_max_ai_turns: int = 20
    handoff_queue_id: Optional[int] = None


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
        on_barge_in: Optional[Callable[[str], Any]] = None,
        on_transfer: Optional[Callable[[str], Any]] = None,
    ):
        self.config = config
        self._on_audio_output = on_audio_output
        self._on_transcript = on_transcript
        self._on_function_call = on_function_call
        self._on_session_end = on_session_end
        self._on_barge_in = on_barge_in
        self._on_transfer = on_transfer
        
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
        self._fallback_index = 0
        self._fallback_active = False
        
        # Handoff handler
        self._handoff_handler: Optional[HandoffHandler] = None
        self._handoff_result: Optional[HandoffResult] = None
        if config.handoff_enabled:
            self._handoff_handler = HandoffHandler(
                domain_uuid=config.domain_uuid,
                call_uuid=config.call_uuid,
                config=HandoffConfig(
                    enabled=config.handoff_enabled,
                    timeout_ms=config.handoff_timeout_ms,
                    keywords=config.handoff_keywords,
                    max_ai_turns=config.handoff_max_ai_turns,
                    fallback_queue_id=config.handoff_queue_id,
                    secretary_uuid=config.secretary_uuid,
                ),
                transcript=[],  # Will be updated during session
                on_transfer=on_transfer,
                on_message=self._send_text_to_provider,
            )
    
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
            prefix_padding_ms=self.config.prefix_padding_ms,
            tools=self.config.tools,
            max_response_output_tokens=self.config.max_response_output_tokens,
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
        while self.is_active:
            if not self._provider:
                return

            try:
                async for event in self._provider.receive_events():
                    action = await self._handle_event(event)
                    if action == "fallback":
                        break
                    if action == "stop" or self._ended:
                        return
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Event loop error: {e}")
                if not await self._try_fallback("provider_error"):
                    await self.stop("error")
                return
    
    async def _handle_event(self, event: ProviderEvent) -> str:
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
                logger.info(f"Audio delta received: {len(event.audio_bytes)} bytes", extra={
                    "call_uuid": self.call_uuid,
                    "audio_size": len(event.audio_bytes),
                })
                await self._handle_audio_output(event.audio_bytes)
            else:
                logger.warning("Audio delta event with no audio bytes", extra={
                    "call_uuid": self.call_uuid,
                })
        
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
                
                # Check for handoff keyword
                if self._handoff_handler and not self._handoff_result:
                    self._handoff_handler.increment_turn()
                    await self._check_handoff_keyword(event.transcript)
                    
                    # Check max turns
                    if self._handoff_handler.should_check_handoff():
                        logger.info("Max AI turns reached, initiating handoff", extra={
                            "call_uuid": self.call_uuid,
                        })
                        await self._initiate_handoff(reason="max_turns_exceeded")
        
        elif event.type == ProviderEventType.SPEECH_STARTED:
            self._user_speaking = True
            self._speech_start_time = time.time()
            if self._assistant_speaking:
                await self.interrupt()
                if self.config.barge_in_enabled and self._on_barge_in:
                    try:
                        await self._on_barge_in(self.call_uuid)
                        self._metrics.record_barge_in(self.call_uuid)
                    except Exception:
                        logger.debug("Failed to clear playback on barge-in", extra={"call_uuid": self.call_uuid})
        
        elif event.type == ProviderEventType.SPEECH_STOPPED:
            self._user_speaking = False
        
        elif event.type == ProviderEventType.RESPONSE_DONE:
            if self._speech_start_time:
                self._metrics.record_latency(self.call_uuid, time.time() - self._speech_start_time)
                self._speech_start_time = None
        
        elif event.type == ProviderEventType.FUNCTION_CALL:
            await self._handle_function_call(event)
        
        elif event.type in (ProviderEventType.ERROR, ProviderEventType.RATE_LIMITED, ProviderEventType.SESSION_ENDED):
            reason = {
                ProviderEventType.ERROR: "provider_error",
                ProviderEventType.RATE_LIMITED: "provider_rate_limited",
                ProviderEventType.SESSION_ENDED: "provider_ended",
            }[event.type]
            if await self._try_fallback(reason):
                return "fallback"
            await self.stop(reason)
            return "stop"

        return "continue"
    
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
        elif name == "request_handoff":
            # Handoff requested by LLM via function call
            await self._initiate_handoff(reason="llm_intent")
            return {"status": "handoff_initiated"}
        return {"error": f"Unknown function: {name}"}
    
    async def _send_text_to_provider(self, text: str) -> None:
        """Envia texto para o provider (TTS)."""
        if self._provider:
            await self._provider.send_text(text)
    
    async def _check_handoff_keyword(self, user_text: str) -> bool:
        """Verifica se o texto contém keyword de handoff."""
        if not self._handoff_handler:
            return False
        
        keyword = self._handoff_handler.detect_handoff_keyword(user_text)
        if keyword:
            logger.info("Handoff keyword detected", extra={
                "call_uuid": self.call_uuid,
                "keyword": keyword,
            })
            await self._initiate_handoff(reason=f"keyword_match:{keyword}")
            return True
        return False
    
    async def _initiate_handoff(self, reason: str) -> None:
        """Inicia processo de handoff."""
        if not self._handoff_handler or self._handoff_result:
            return
        
        # Sincronizar transcript com o handler
        from .handlers.handoff import TranscriptEntry as HTranscriptEntry
        self._handoff_handler.transcript = [
            HTranscriptEntry(role=t.role, text=t.text, timestamp=t.timestamp)
            for t in self._transcript
        ]
        
        # Calcular métricas
        duration = 0
        if self._started_at:
            duration = int((datetime.now() - self._started_at).total_seconds())
        
        avg_latency = self._metrics.get_avg_latency(self.call_uuid)
        
        # Iniciar handoff
        self._handoff_result = await self._handoff_handler.initiate_handoff(
            reason=reason,
            caller_number=self.config.caller_id,
            provider=self.config.provider_name,
            language="pt-BR",
            duration_seconds=duration,
            avg_latency_ms=avg_latency,
        )
        
        logger.info("Handoff completed", extra={
            "call_uuid": self.call_uuid,
            "result": self._handoff_result.action,
            "ticket_id": self._handoff_result.ticket_id,
        })
        
        # Se criou ticket ou transferiu, encerrar após mensagem de despedida
        if self._handoff_result.action in ("ticket_created", "transferred"):
            await asyncio.sleep(3.0)  # Aguardar mensagem de despedida
            await self.stop(f"handoff_{self._handoff_result.action}")
    
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

    async def _try_fallback(self, reason: str) -> bool:
        """
        Tenta alternar para um provider fallback, se configurado.
        """
        if self._fallback_active or not self.config.fallback_providers:
            return False

        self._fallback_active = True
        try:
            while self._fallback_index < len(self.config.fallback_providers):
                next_provider = self.config.fallback_providers[self._fallback_index]
                self._fallback_index += 1

                if not next_provider or next_provider == self.config.provider_name:
                    continue

                logger.warning("Attempting fallback provider", extra={
                    "call_uuid": self.call_uuid,
                    "from_provider": self.config.provider_name,
                    "to_provider": next_provider,
                    "reason": reason,
                })

                try:
                    if self._provider:
                        await self._provider.disconnect()
                except Exception:
                    pass

                self.config.provider_name = next_provider
                await self._create_provider()
                self._setup_resampler()
                self._assistant_speaking = False
                self._user_speaking = False
                self._metrics.update_provider(self.call_uuid, next_provider)

                logger.info("Fallback provider activated", extra={
                    "call_uuid": self.call_uuid,
                    "provider": next_provider,
                })
                return True

            return False
        finally:
            self._fallback_active = False
    
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
