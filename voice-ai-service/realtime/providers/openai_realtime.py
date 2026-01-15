"""
OpenAI Realtime API Provider.

=== KNOWLEDGE BASE REFERENCES ===
- Context7 Library ID: /websites/platform_openai (9418 snippets)
- Context7 SDK: /openai/openai-python (429 snippets)
- Docs: https://platform.openai.com/docs/guides/realtime-conversations
- API Reference: https://platform.openai.com/docs/api-reference/realtime
- SDK: https://github.com/openai/openai-python (src/openai/resources/beta/realtime/)

=== MODELOS DISPONÍVEIS (Jan/2026) ===
- gpt-4o-realtime-preview (principal)
- gpt-realtime-2025-08-28 (mais recente, se disponível)

=== CONFIGURAÇÕES DE ÁUDIO ===
- Input: 24kHz PCM16 mono (audio/pcm)
- Output: 24kHz PCM16 mono (audio/pcm)
- Endpoint: wss://api.openai.com/v1/realtime?model={model}
- Headers: Authorization: Bearer {api_key}, OpenAI-Beta: realtime=v1

=== FORMATO DE EVENTOS (Context7 verificado) ===
Client → Server:
- session.update: Configurar sessão (modalities, voice, VAD, tools)
- input_audio_buffer.append: Enviar áudio (base64, até 15MiB)
- input_audio_buffer.commit: Commit manual (se VAD desabilitado)
- conversation.item.create: Criar item (message, function_call_output)
- response.create: Solicitar resposta
- response.cancel: Interromper resposta (barge-in)

Server → Client:
- session.created: Sessão iniciada
- session.updated: Configuração atualizada
- response.output_audio.delta: Chunk de áudio (base64)
- response.output_audio.done: Áudio completo
- response.audio_transcript.delta/done: Transcrição do assistente
- conversation.item.input_audio_transcription.completed: STT do usuário
- input_audio_buffer.speech_started/speech_stopped: VAD
- input_audio_buffer.timeout_triggered: Timeout de silêncio (idle_timeout_ms)
- response.function_call_arguments.done: Function call
- error: Erros (rate_limit_exceeded, etc.)

=== VAD (TURN DETECTION) ===
API Beta (atual): turn_detection no nível superior
- type: "server_vad"
- threshold: 0.5 (0.0-1.0, sensibilidade)
- prefix_padding_ms: 300 (áudio antes da fala)
- silence_duration_ms: 200 (silêncio para encerrar turno)
- create_response: true (auto-responder)

=== SESSION.UPDATE FORMAT ===
IMPORTANTE: API Beta usa formato DIFERENTE da documentação GA!

API BETA (ATUAL):
- modalities: ["audio", "text"] (não "output_modalities")
- voice: "alloy" (nível superior)
- input_audio_format: "pcm16" (string plana)
- output_audio_format: "pcm16" (string plana)
- turn_detection: {...} (nível superior)
- instructions: "system prompt"
- NÃO TEM session.type!

API GA (FUTURO - Context7):
- session.type: "realtime"
- output_modalities: ["audio"]
- audio: { input: {...}, output: {...} }
"""

import asyncio
import base64
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from .base import (
    BaseRealtimeProvider,
    ProviderEvent,
    ProviderEventType,
    RealtimeConfig,
)

logger = logging.getLogger(__name__)


# Tools padrão conforme design.md Decision 7
DEFAULT_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "name": "transfer_call",
        "description": "Transfere a chamada para outro ramal ou departamento",
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {"type": "string"},
                "reason": {"type": "string"}
            },
            "required": ["destination"]
        }
    },
    {
        "type": "function", 
        "name": "create_ticket",
        "description": "Cria um ticket no sistema",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]}
            },
            "required": ["subject"]
        }
    },
    {
        "type": "function",
        "name": "end_call",
        "description": "Encerra a chamada",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}}
        }
    }
]


class OpenAIRealtimeProvider(BaseRealtimeProvider):
    """
    Provider para OpenAI Realtime API.
    
    Sample rates (conforme SDK oficial):
    - Input: 24kHz PCM16 mono
    - Output: 24kHz PCM16 mono
    
    Protocolo WebSocket:
    - Header: Authorization: Bearer {api_key}
    - Header: OpenAI-Beta: realtime=v1
    - URL: wss://api.openai.com/v1/realtime?model={model}
    """
    
    REALTIME_URL = "wss://api.openai.com/v1/realtime"
    # Modelo padrão para Realtime API (Beta)
    # IMPORTANTE: A API ainda está em Beta! Usar gpt-4o-realtime-preview
    # O modelo "gpt-realtime" é para a versão GA (futura)
    DEFAULT_MODEL = "gpt-4o-realtime-preview"
    
    def __init__(self, credentials: Dict[str, Any], config: RealtimeConfig):
        import os
        super().__init__(credentials, config)
        
        # Fallback para variáveis de ambiente se credentials estiver vazio
        self.api_key = credentials.get("api_key") or os.getenv("OPENAI_API_KEY")
        self.model = credentials.get("model", self.DEFAULT_MODEL)
        
        if not self.api_key:
            raise ValueError("OpenAI API key not configured (check DB config or OPENAI_API_KEY env)")
        
        logger.info("OpenAI Realtime credentials loaded", extra={
            "api_key_source": "db" if credentials.get("api_key") else "env",
            "model": self.model,
        })
        
        self._ws: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[ProviderEvent] = asyncio.Queue()
        self._session_id: Optional[str] = None
    
    @property
    def name(self) -> str:
        return "openai_realtime"
    
    @property
    def input_sample_rate(self) -> int:
        return 24000  # OpenAI Realtime requer 24kHz
    
    @property
    def output_sample_rate(self) -> int:
        return 24000
    
    async def connect(self) -> None:
        """Conecta ao OpenAI Realtime API."""
        if self._connected:
            return
        
        url = f"{self.REALTIME_URL}?model={self.model}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1"
        }
        
        logger.debug(f"Connecting to OpenAI Realtime: {url}", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
        })
        
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=None,
            ping_interval=20,
        )
        
        # Aguardar session.created
        response = await asyncio.wait_for(self._ws.recv(), timeout=10)
        event = json.loads(response)
        
        if event.get("type") != "session.created":
            raise ConnectionError(f"Unexpected initial event: {event.get('type')}")
        
        self._session_id = event.get("session", {}).get("id")
        self._connected = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        logger.info("Connected to OpenAI Realtime", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
            "session_id": self._session_id,
        })
    
    async def configure(self) -> None:
        """
        Configura sessão com prompt, voz, VAD, tools.
        
        Ref: session.update event
        
        IMPORTANTE: A API Beta usa formato DIFERENTE da documentação GA!
        
        FORMATO BETA (ATUAL - Jan/2026):
        - modalities: ["audio", "text"] (não "output_modalities")
        - voice: string (nível superior, não em audio.output)
        - input_audio_format: "pcm16" (plano, não aninhado)
        - output_audio_format: "pcm16" (plano, não aninhado)
        - turn_detection: { type: "server_vad", ... } (nível superior)
        - instructions: string
        - NÃO TEM session.type!
        
        FORMATO GA (FUTURO - Context7 docs):
        - session.type: "realtime"
        - output_modalities: ["audio"]
        - audio: { input: {...}, output: {...} }
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Vozes disponíveis: alloy, echo, fable, onyx, nova, shimmer, sage
        voice = self.config.voice or "alloy"
        
        # === FORMATO BETA (ATUAL) ===
        # Ref: session.updated response from API Beta
        session_config = {
            "type": "session.update",
            "session": {
                # Modalities (formato Beta: "modalities", não "output_modalities")
                "modalities": ["audio", "text"],
                
                # System prompt
                "instructions": self.config.system_prompt or "",
                
                # Voz (formato Beta: nível superior, não aninhado em audio.output)
                "voice": voice,
                
                # Formatos de áudio (formato Beta: strings planas)
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                
                # Transcrição do input (opcional)
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                
                # VAD - Turn Detection (formato Beta: nível superior)
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": self.config.vad_threshold or 0.5,
                    "prefix_padding_ms": self.config.prefix_padding_ms or 300,
                    "silence_duration_ms": self.config.silence_duration_ms or 200,
                    "create_response": True,
                },
                
                # Tools (function calling)
                "tools": self.config.tools or DEFAULT_TOOLS,
                "tool_choice": "auto",
            }
        }
        
        # max_response_output_tokens (limitar resposta)
        if self.config.max_response_output_tokens and self.config.max_response_output_tokens > 0:
            session_config["session"]["max_response_output_tokens"] = int(self.config.max_response_output_tokens)
        else:
            session_config["session"]["max_response_output_tokens"] = "inf"
        
        logger.debug("Sending session.update (Beta format)", extra={
            "domain_uuid": self.config.domain_uuid,
            "has_instructions": bool(self.config.system_prompt),
            "voice": voice,
            "vad_threshold": self.config.vad_threshold or 0.5,
        })
        
        await self._ws.send(json.dumps(session_config))
        
        # Se houver first_message (saudação), enviar como texto e solicitar resposta
        if self.config.first_message:
            logger.debug(f"Sending first message: {self.config.first_message[:50]}...")
            await self.send_text(self.config.first_message)
            # Solicitar resposta do modelo
            await self._ws.send(json.dumps({"type": "response.create"}))
    
    async def send_audio(self, audio_bytes: bytes) -> None:
        """
        Envia áudio para OpenAI.
        
        Formato: base64 PCM16 @ 24kHz
        Ref: input_audio_buffer.append event (SDK oficial)
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        
        await self._ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }))
        
        logger.debug(f"Audio chunk sent to OpenAI: {len(audio_bytes)} bytes", extra={
            "domain_uuid": self.config.domain_uuid,
        })
    
    async def send_text(self, text: str) -> None:
        """
        Envia mensagem de texto.
        
        Ref: conversation.item.create event (SDK oficial)
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}]
            }
        }))
        
        logger.debug(f"Text sent to OpenAI: {text[:50]}...", extra={
            "domain_uuid": self.config.domain_uuid,
        })
    
    async def interrupt(self) -> None:
        """
        Interrompe resposta atual (barge-in).
        
        Ref: response.cancel event (SDK oficial)
        """
        if self._ws:
            await self._ws.send(json.dumps({"type": "response.cancel"}))
            logger.debug("Interrupt signal sent to OpenAI", extra={
                "domain_uuid": self.config.domain_uuid,
            })
    
    async def send_function_result(
        self,
        function_name: str,
        result: Dict[str, Any],
        call_id: Optional[str] = None
    ) -> None:
        """
        Envia resultado de function call.
        
        Ref: conversation.item.create (type: function_call_output) (SDK oficial)
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id or "",
                "output": json.dumps(result)
            }
        }))
        
        # Solicitar nova resposta após enviar resultado da função
        await self._ws.send(json.dumps({"type": "response.create"}))
        
        logger.debug(f"Function result sent to OpenAI: {function_name}", extra={
            "domain_uuid": self.config.domain_uuid,
            "call_id": call_id,
        })
    
    async def receive_events(self) -> AsyncIterator[ProviderEvent]:
        """Generator de eventos."""
        while self._connected:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                yield event
                if event.type in (ProviderEventType.SESSION_ENDED, ProviderEventType.ERROR):
                    break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
    
    async def _receive_loop(self) -> None:
        """Loop de recebimento de eventos."""
        if not self._ws:
            return
        
        try:
            async for message in self._ws:
                event = json.loads(message)
                provider_event = self._parse_event(event)
                if provider_event:
                    await self._event_queue.put(provider_event)
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"OpenAI WebSocket closed: {e}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.SESSION_ENDED,
                data={"reason": str(e)}
            ))
        except Exception as e:
            logger.error(f"OpenAI receive loop error: {e}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.ERROR,
                data={"error": str(e)}
            ))
    
    def _parse_event(self, event: Dict[str, Any]) -> Optional[ProviderEvent]:
        """
        Converte evento OpenAI para ProviderEvent.
        
        IMPORTANTE - Nomes de eventos conforme documentação oficial (Jan/2026):
        - response.output_audio.delta (NÃO response.audio.delta!)
        - response.output_audio.done
        - response.audio_transcript.delta/done
        - conversation.item.input_audio_transcription.completed
        """
        etype = event.get("type", "")
        
        # Log para debug de eventos desconhecidos
        if etype not in (
            "response.output_audio.delta", "response.output_audio.done",
            "response.audio_transcript.delta", "response.audio_transcript.done",
            "conversation.item.input_audio_transcription.completed",
            "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
            "response.created", "response.done",
            "response.function_call_arguments.done",
            "session.created", "session.updated",
            "input_audio_buffer.committed", "input_audio_buffer.cleared",
            "conversation.item.created", "error"
        ):
            logger.debug(f"OpenAI event received: {etype}", extra={
                "domain_uuid": self.config.domain_uuid,
                "event_data_preview": str(event)[:200],
            })
        
        # ===== ÁUDIO OUTPUT =====
        # CORRIGIDO: Era response.audio.delta, agora é response.output_audio.delta
        if etype == "response.output_audio.delta":
            audio_b64 = event.get("delta", "")
            audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
            
            logger.debug(f"OpenAI audio received: {len(audio_bytes)} bytes", extra={
                "domain_uuid": self.config.domain_uuid,
                "response_id": event.get("response_id"),
            })
            
            return ProviderEvent(
                type=ProviderEventType.AUDIO_DELTA,
                data={"audio": audio_bytes},
                response_id=event.get("response_id"),
                item_id=event.get("item_id"),
            )
        
        # CORRIGIDO: Era response.audio.done
        if etype == "response.output_audio.done":
            logger.debug("OpenAI audio output done", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(type=ProviderEventType.AUDIO_DONE, data={})
        
        # ===== TRANSCRIÇÃO DO ASSISTENTE =====
        if etype == "response.audio_transcript.delta":
            return ProviderEvent(
                type=ProviderEventType.TRANSCRIPT_DELTA,
                data={"transcript": event.get("delta", "")}
            )
        
        if etype == "response.audio_transcript.done":
            return ProviderEvent(
                type=ProviderEventType.TRANSCRIPT_DONE,
                data={"transcript": event.get("transcript", "")}
            )
        
        # ===== TRANSCRIÇÃO DO USUÁRIO (STT) =====
        if etype == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript", "")
            logger.debug(f"User transcript: {transcript[:50]}...", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(
                type=ProviderEventType.USER_TRANSCRIPT,
                data={"transcript": transcript}
            )
        
        if etype == "conversation.item.input_audio_transcription.failed":
            logger.warning("User audio transcription failed", extra={
                "domain_uuid": self.config.domain_uuid,
                "error": event.get("error"),
            })
            return None  # Não é erro crítico
        
        # ===== VAD (Voice Activity Detection) =====
        if etype == "input_audio_buffer.speech_started":
            logger.debug("Speech started (VAD)", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(type=ProviderEventType.SPEECH_STARTED, data={})
        
        if etype == "input_audio_buffer.speech_stopped":
            logger.debug("Speech stopped (VAD)", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(type=ProviderEventType.SPEECH_STOPPED, data={})
        
        # ===== RESPONSE LIFECYCLE =====
        if etype == "response.created":
            logger.debug("Response started", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(type=ProviderEventType.RESPONSE_STARTED, data={})
        
        if etype == "response.done":
            response = event.get("response", {})
            status = response.get("status", "completed")
            logger.debug(f"Response done: {status}", extra={
                "domain_uuid": self.config.domain_uuid,
                "status": status,
            })
            return ProviderEvent(
                type=ProviderEventType.RESPONSE_DONE,
                data={"status": status}
            )
        
        # ===== FUNCTION CALLS =====
        if etype == "response.function_call_arguments.done":
            func_name = event.get("name", "")
            try:
                arguments = json.loads(event.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}
            
            logger.info(f"Function call: {func_name}", extra={
                "domain_uuid": self.config.domain_uuid,
                "call_id": event.get("call_id"),
            })
            
            return ProviderEvent(
                type=ProviderEventType.FUNCTION_CALL,
                data={
                    "function_name": func_name,
                    "arguments": arguments,
                    "call_id": event.get("call_id", ""),
                }
            )
        
        # ===== ERRORS =====
        if etype == "error":
            error = event.get("error", {})
            error_code = error.get("code", "unknown")
            error_message = error.get("message", "Unknown error")
            
            logger.error(f"OpenAI error: {error_code} - {error_message}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            
            if error_code == "rate_limit_exceeded":
                return ProviderEvent(type=ProviderEventType.RATE_LIMITED, data={"error": error})
            
            return ProviderEvent(type=ProviderEventType.ERROR, data={"error": error})
        
        # ===== SESSION EVENTS (confirmações, ignorar) =====
        if etype in ("session.updated", "input_audio_buffer.committed", 
                     "input_audio_buffer.cleared", "conversation.item.created"):
            return None  # Eventos de confirmação, não precisam de handling
        
        return None
    
    async def disconnect(self) -> None:
        """Encerra conexão."""
        self._connected = False
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        logger.info("Disconnected from OpenAI Realtime", extra={
            "domain_uuid": self.config.domain_uuid,
            "session_id": self._session_id,
        })
