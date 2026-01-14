"""
OpenAI Realtime API Provider.

Referências:
- .context/docs/data-flow.md: Fluxo Realtime v2
- openspec/changes/voice-ai-realtime/design.md: Decision 2 (Protocol)
- Context7: /openai/openai-cookbook (Realtime API examples)
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
    
    Sample rates:
    - Input: 24kHz (API requirement)
    - Output: 24kHz
    """
    
    REALTIME_URL = "wss://api.openai.com/v1/realtime"
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
    
    @property
    def name(self) -> str:
        return "openai_realtime"
    
    @property
    def input_sample_rate(self) -> int:
        return 24000
    
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
            raise ConnectionError(f"Unexpected: {event.get('type')}")
        
        self._connected = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        # Log estruturado conforme backend-specialist.md
        logger.info("Connected to OpenAI Realtime", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
        })
    
    async def configure(self) -> None:
        """Configura sessão com prompt, voz, VAD, tools."""
        if not self._ws:
            raise RuntimeError("Not connected")
        
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "instructions": self.config.system_prompt,
                "voice": self.config.voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": self.config.vad_threshold,
                    "silence_duration_ms": self.config.silence_duration_ms,
                    "prefix_padding_ms": self.config.prefix_padding_ms,
                },
                "tools": self.config.tools or DEFAULT_TOOLS,
                "tool_choice": "auto",
                "max_response_output_tokens": self.config.max_response_output_tokens,
            }
        }
        
        await self._ws.send(json.dumps(session_config))
        
        if self.config.first_message:
            await self.send_text(self.config.first_message)
            await self._ws.send(json.dumps({"type": "response.create"}))
    
    async def send_audio(self, audio_bytes: bytes) -> None:
        """Envia áudio (base64 PCM16 @ 24kHz)."""
        if not self._ws:
            raise RuntimeError("Not connected")
        
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        await self._ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }))
    
    async def send_text(self, text: str) -> None:
        """Envia mensagem de texto."""
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
    
    async def interrupt(self) -> None:
        """Interrompe resposta atual (barge-in)."""
        if self._ws:
            await self._ws.send(json.dumps({"type": "response.cancel"}))
    
    async def send_function_result(
        self,
        function_name: str,
        result: Dict[str, Any],
        call_id: Optional[str] = None
    ) -> None:
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
        await self._ws.send(json.dumps({"type": "response.create"}))
    
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
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.SESSION_ENDED,
                data={"reason": str(e)}
            ))
        except Exception as e:
            logger.error(f"Receive loop error: {e}")
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.ERROR,
                data={"error": str(e)}
            ))
    
    def _parse_event(self, event: Dict[str, Any]) -> Optional[ProviderEvent]:
        """Converte evento OpenAI para ProviderEvent."""
        etype = event.get("type", "")
        
        if etype == "response.audio.delta":
            audio_b64 = event.get("delta", "")
            return ProviderEvent(
                type=ProviderEventType.AUDIO_DELTA,
                data={"audio": base64.b64decode(audio_b64) if audio_b64 else b""},
                response_id=event.get("response_id"),
            )
        
        if etype == "response.audio.done":
            return ProviderEvent(type=ProviderEventType.AUDIO_DONE, data={})
        
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
        
        if etype == "conversation.item.input_audio_transcription.completed":
            return ProviderEvent(
                type=ProviderEventType.USER_TRANSCRIPT,
                data={"transcript": event.get("transcript", "")}
            )
        
        if etype == "input_audio_buffer.speech_started":
            return ProviderEvent(type=ProviderEventType.SPEECH_STARTED, data={})
        
        if etype == "input_audio_buffer.speech_stopped":
            return ProviderEvent(type=ProviderEventType.SPEECH_STOPPED, data={})
        
        if etype == "response.created":
            return ProviderEvent(type=ProviderEventType.RESPONSE_STARTED, data={})
        
        if etype == "response.done":
            return ProviderEvent(type=ProviderEventType.RESPONSE_DONE, data={})
        
        if etype == "response.function_call_arguments.done":
            return ProviderEvent(
                type=ProviderEventType.FUNCTION_CALL,
                data={
                    "function_name": event.get("name", ""),
                    "arguments": json.loads(event.get("arguments", "{}")),
                    "call_id": event.get("call_id", ""),
                }
            )
        
        if etype == "error":
            error = event.get("error", {})
            if error.get("code") == "rate_limit_exceeded":
                return ProviderEvent(type=ProviderEventType.RATE_LIMITED, data={"error": error})
            return ProviderEvent(type=ProviderEventType.ERROR, data={"error": error})
        
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
            "domain_uuid": self.config.domain_uuid
        })
