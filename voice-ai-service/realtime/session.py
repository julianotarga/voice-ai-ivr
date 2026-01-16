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

# FASE 1: Handoff Inteligente
# Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
from .handlers.transfer_manager import (
    TransferManager,
    TransferStatus,
    TransferResult,
    create_transfer_manager,
)
from .handlers.transfer_destination_loader import TransferDestination

logger = logging.getLogger(__name__)


# Function call definition para o LLM usar handoff inteligente
HANDOFF_FUNCTION_DEFINITION = {
    "type": "function",
    "name": "request_handoff",
    "description": (
        "Transfere a chamada para um atendente humano, departamento ou pessoa específica. "
        "Use quando o cliente pedir para falar com alguém ou quando não souber resolver."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "description": (
                    "Nome da pessoa, departamento ou 'qualquer atendente'. "
                    "Exemplos: 'Jeni', 'financeiro', 'suporte', 'qualquer atendente disponível'"
                )
            },
            "reason": {
                "type": "string",
                "description": "Motivo pelo qual o cliente quer falar com alguém"
            }
        },
        "required": ["destination"]
    }
}


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
    omniplay_company_id: Optional[int] = None  # OmniPlay companyId para API
    # Audio Configuration (per-secretary)
    audio_warmup_chunks: int = 15  # chunks de 20ms antes do playback
    audio_warmup_ms: int = 400  # buffer de warmup em ms
    audio_adaptive_warmup: bool = True  # ajuste automático de warmup
    jitter_buffer_min: int = 100  # FreeSWITCH jitter buffer min (ms)
    jitter_buffer_max: int = 300  # FreeSWITCH jitter buffer max (ms)
    jitter_buffer_step: int = 40  # FreeSWITCH jitter buffer step (ms)
    stream_buffer_size: int = 20  # mod_audio_stream buffer in MILLISECONDS (not samples!)
    
    # FASE 1: Intelligent Handoff Configuration
    # Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
    intelligent_handoff_enabled: bool = True  # Usar TransferManager ao invés de handoff simples
    transfer_announce_enabled: bool = True  # Anunciar antes de transferir
    transfer_default_timeout: int = 30  # Timeout padrão de ring em segundos


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
        
        # Handoff handler (legacy - para fallback)
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
                    omniplay_company_id=config.omniplay_company_id,  # OmniPlay companyId
                ),
                transcript=[],  # Will be updated during session
                on_transfer=on_transfer,
                on_message=self._send_text_to_provider,
            )
        
        # FASE 1: TransferManager para handoff inteligente
        # Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
        self._transfer_manager: Optional[TransferManager] = None
        self._current_transfer: Optional[TransferResult] = None
        self._transfer_in_progress = False
    
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
            
            # FASE 1: Inicializar TransferManager para handoff inteligente
            if self.config.intelligent_handoff_enabled:
                await self._init_transfer_manager()
            
            self._event_task = asyncio.create_task(self._event_loop())
            self._timeout_task = asyncio.create_task(self._timeout_monitor())
            
            logger.info("Realtime session started", extra={
                "call_uuid": self.call_uuid,
                "domain_uuid": self.domain_uuid,
                "provider": self.config.provider_name,
                "intelligent_handoff": self.config.intelligent_handoff_enabled,
            })
        except Exception as e:
            logger.error(f"Failed to start session: {e}")
            await self.stop("error")
            raise
    
    async def _init_transfer_manager(self) -> None:
        """
        Inicializa TransferManager para handoff inteligente.
        
        Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
        """
        try:
            self._transfer_manager = await create_transfer_manager(
                domain_uuid=self.config.domain_uuid,
                call_uuid=self.config.call_uuid,
                caller_id=self.config.caller_id,
                secretary_uuid=self.config.secretary_uuid,
                on_resume=self._on_transfer_resume,
                on_transfer_complete=self._on_transfer_complete,
            )
            
            logger.info("TransferManager initialized", extra={
                "call_uuid": self.call_uuid,
                "destinations_count": len(self._transfer_manager._destinations or []),
            })
        except Exception as e:
            logger.warning(f"Failed to initialize TransferManager: {e}")
            # Continuar sem TransferManager - usará handoff legacy
            self._transfer_manager = None
    
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
        """
        Configura os resamplers para conversão de áudio.
        
        IMPORTANTE: Input e output do provider podem ter sample rates diferentes!
        - ElevenLabs: input=16kHz, output=16kHz/22050Hz/44100Hz (dinâmico)
        - OpenAI Realtime: input=24kHz, output=24kHz
        - Gemini Live: input=16kHz, output=24kHz
        """
        if self._provider:
            fs_rate = self.config.freeswitch_sample_rate
            provider_in = self._provider.input_sample_rate
            provider_out = self._provider.output_sample_rate
            
            # Log explícito para debug
            logger.info(
                f"Resampler setup: FS={fs_rate}Hz <-> Provider(in={provider_in}Hz, out={provider_out}Hz)"
            )
            
            self._resampler = ResamplerPair(
                freeswitch_rate=fs_rate,
                provider_input_rate=provider_in,
                provider_output_rate=provider_out,
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
        
        Se o áudio sair distorcido, tente estas variáveis de ambiente:
        - FS_AUDIO_SWAP_BYTES=true (inverte byte order: little <-> big endian)
        - FS_AUDIO_INVERT_PHASE=true (inverte fase: sample *= -1)
        - FS_AUDIO_FORCE_RESAMPLE=24000 (força resample de 24kHz para 16kHz)
        """
        import os
        import numpy as np
        
        if not audio_bytes:
            return
        
        # Forçar resample se o provider retornar sample rate diferente do declarado
        # Alguns providers (ElevenLabs) podem retornar 22050Hz ao invés de 16kHz
        force_resample = os.getenv("FS_AUDIO_FORCE_RESAMPLE", "").strip()
        if force_resample and force_resample.isdigit():
            from .utils.resampler import Resampler
            source_rate = int(force_resample)
            if source_rate != 16000:
                temp_resampler = Resampler(source_rate, 16000)
                audio_bytes = temp_resampler.process(audio_bytes)
        
        # Opção para corrigir byte order (big-endian <-> little-endian)
        # Útil se o áudio sair completamente distorcido
        swap_bytes = os.getenv("FS_AUDIO_SWAP_BYTES", "false").lower() in ("1", "true", "yes")
        
        if swap_bytes and len(audio_bytes) >= 2:
            # PCM16: swap bytes de cada sample (2 bytes)
            samples = np.frombuffer(audio_bytes, dtype=np.int16)
            swapped = samples.byteswap()
            audio_bytes = swapped.tobytes()
        
        # Opção para inverter fase (útil se o áudio sair "metálico")
        invert_phase = os.getenv("FS_AUDIO_INVERT_PHASE", "false").lower() in ("1", "true", "yes")
        
        if invert_phase and len(audio_bytes) >= 2:
            samples = np.frombuffer(audio_bytes, dtype=np.int16)
            inverted = -samples  # Inverte fase
            audio_bytes = np.clip(inverted, -32768, 32767).astype(np.int16).tobytes()
        
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
                        # NÃO bloquear - handoff em background
                        asyncio.create_task(self._initiate_handoff(reason="max_turns_exceeded"))
        
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
            # FASE 1: Usar TransferManager se disponível
            destination = args.get("destination", "qualquer atendente")
            reason = args.get("reason", "solicitação do cliente")
            
            if self._transfer_manager and self.config.intelligent_handoff_enabled:
                # Handoff inteligente com attended transfer
                asyncio.create_task(self._execute_intelligent_handoff(destination, reason))
                return {"status": "transfer_initiated", "destination": destination}
            else:
                # Fallback para handoff legacy (cria ticket)
                asyncio.create_task(self._initiate_handoff(reason="llm_intent"))
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
            # NÃO bloquear o event loop - handoff roda em background
            asyncio.create_task(self._initiate_handoff(reason=f"keyword_match:{keyword}"))
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
        # 6 segundos = tempo suficiente para TTS terminar (média ~4-5s)
        if self._handoff_result.action in ("ticket_created", "transferred"):
            await asyncio.sleep(6.0)  # Aguardar mensagem de despedida
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
    
    # =========================================================================
    # FASE 1: Intelligent Handoff Methods
    # Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/
    # =========================================================================
    
    async def _execute_intelligent_handoff(
        self,
        destination_text: str,
        reason: str
    ) -> None:
        """
        Executa handoff inteligente com attended transfer.
        
        Fluxo:
        1. Encontra destino pelo texto do usuário
        2. Anuncia transferência
        3. Executa attended transfer com monitoramento
        4. Se atendeu: bridge e encerra sessão
        5. Se não atendeu: retorna ao cliente com mensagem contextual
        
        Args:
            destination_text: Texto do destino (ex: "Jeni", "financeiro")
            reason: Motivo do handoff
        """
        if not self._transfer_manager or self._transfer_in_progress:
            return
        
        self._transfer_in_progress = True
        
        try:
            # 1. Encontrar destino
            destination, error = await self._transfer_manager.find_and_validate_destination(
                destination_text
            )
            
            if error:
                # Destino não encontrado - informar usuário e retomar
                await self._send_text_to_provider(error)
                self._transfer_in_progress = False
                return
            
            if not destination:
                await self._send_text_to_provider(
                    "Não consegui identificar para quem você quer falar. "
                    "Pode repetir o nome ou departamento?"
                )
                self._transfer_in_progress = False
                return
            
            # 2. Anunciar transferência (se habilitado)
            if self.config.transfer_announce_enabled:
                await self._send_text_to_provider(
                    f"Um momento, vou transferir para {destination.name}..."
                )
                # Aguardar TTS terminar (aproximadamente)
                await asyncio.sleep(2.0)
            
            logger.info(
                "Executing intelligent handoff",
                extra={
                    "call_uuid": self.call_uuid,
                    "destination": destination.name,
                    "destination_number": destination.destination_number,
                    "reason": reason,
                }
            )
            
            # 3. Executar attended transfer
            result = await self._transfer_manager.execute_attended_transfer(
                destination=destination,
                timeout=self.config.transfer_default_timeout,
            )
            
            self._current_transfer = result
            
            # 4. Processar resultado
            await self._handle_transfer_result(result, reason)
            
        except Exception as e:
            logger.exception(f"Intelligent handoff error: {e}")
            await self._send_text_to_provider(
                "Desculpe, não foi possível completar a transferência. "
                "Posso ajudar de outra forma?"
            )
            self._transfer_in_progress = False
    
    async def _handle_transfer_result(
        self,
        result: TransferResult,
        original_reason: str
    ) -> None:
        """
        Processa resultado da transferência.
        
        Args:
            result: Resultado da transferência
            original_reason: Motivo original do handoff
        """
        if result.status == TransferStatus.SUCCESS:
            # Bridge estabelecido com sucesso
            logger.info(
                "Transfer successful - bridge established",
                extra={
                    "call_uuid": self.call_uuid,
                    "destination": result.destination.name if result.destination else None,
                }
            )
            # Encerrar sessão Voice AI (cliente agora está com humano)
            await self.stop("transfer_success")
            
        elif result.status == TransferStatus.CANCELLED:
            # Cliente desligou durante a transferência
            logger.info(
                "Transfer cancelled - caller hangup",
                extra={"call_uuid": self.call_uuid}
            )
            await self.stop("caller_hangup")
            
        else:
            # Transferência não concluída - retomar Voice AI
            self._transfer_in_progress = False
            
            # Enviar mensagem contextual
            message = result.message
            await self._send_text_to_provider(message)
            
            # Aguardar TTS
            await asyncio.sleep(2.0)
            
            # Oferecer callback/recado se aplicável
            if result.should_offer_callback and result.destination:
                await self._offer_callback_or_message(result, original_reason)
    
    async def _offer_callback_or_message(
        self,
        transfer_result: TransferResult,
        reason: str
    ) -> None:
        """
        Oferece callback ou recado após transfer falhar.
        
        Args:
            transfer_result: Resultado da transferência
            reason: Motivo original
        """
        dest_name = transfer_result.destination.name if transfer_result.destination else "o ramal"
        
        # A IA vai continuar a conversa naturalmente
        # Ela já tem contexto do que aconteceu
        await self._send_text_to_provider(
            f"Quer que eu peça para {dest_name} retornar sua ligação, "
            "ou prefere deixar uma mensagem?"
        )
        
        # O fluxo continua naturalmente com o LLM
        # Se cliente aceitar, LLM chamará função apropriada
        # (será implementado na FASE 2 - Callback System)
    
    async def _on_transfer_resume(self) -> None:
        """
        Callback: Retomar Voice AI após transfer falhar.
        
        Chamado pelo TransferManager quando música de espera para
        e precisamos retomar a conversa.
        """
        self._transfer_in_progress = False
        
        logger.info(
            "Resuming Voice AI after transfer",
            extra={"call_uuid": self.call_uuid}
        )
        
        # A mensagem contextual já foi enviada em _handle_transfer_result
        # Aqui só sinalizamos que podemos receber áudio novamente
    
    async def _on_transfer_complete(self, result: TransferResult) -> None:
        """
        Callback: Transferência completada (sucesso ou falha).
        
        Args:
            result: Resultado da transferência
        """
        self._current_transfer = result
        
        self._metrics.record_transfer(
            call_uuid=self.call_uuid,
            status=result.status.value,
            destination=result.destination.name if result.destination else None,
            duration_ms=result.duration_ms,
        )
        
        logger.info(
            "Transfer completed",
            extra={
                "call_uuid": self.call_uuid,
                "status": result.status.value,
                "destination": result.destination.name if result.destination else None,
                "hangup_cause": result.hangup_cause,
                "duration_ms": result.duration_ms,
            }
        )
    
    async def request_transfer(self, user_text: str) -> Optional[TransferResult]:
        """
        API pública para solicitar transferência.
        
        Pode ser chamado diretamente ou via function call.
        
        Args:
            user_text: Texto com destino (ex: "Jeni", "financeiro")
        
        Returns:
            TransferResult ou None se não há TransferManager
        """
        if not self._transfer_manager:
            logger.warning("Transfer requested but TransferManager not available")
            return None
        
        if self._transfer_in_progress:
            logger.warning("Transfer already in progress")
            return None
        
        await self._execute_intelligent_handoff(user_text, "user_request")
        return self._current_transfer