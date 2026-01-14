"""
ElevenLabs Conversational AI Provider.

Referências:
- Context7: /elevenlabs/elevenlabs-python
- .context/docs/data-flow.md: Fluxo Realtime v2
- openspec/changes/voice-ai-realtime/design.md: Decision 4

ElevenLabs Conversational AI usa WebSocket para streaming bidirecional.
- Input: 16kHz PCM16
- Output: 16kHz PCM16
- Endpoint: wss://api.elevenlabs.io/v1/convai/conversation
"""

import asyncio
import base64
import json
import logging
from typing import Any, AsyncIterator, Dict, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from .base import (
    BaseRealtimeProvider,
    ProviderEvent,
    ProviderEventType,
    RealtimeConfig,
)

logger = logging.getLogger(__name__)


class ElevenLabsConversationalProvider(BaseRealtimeProvider):
    """
    Provider para ElevenLabs Conversational AI.
    
    Sample rates:
    - Input: 16kHz (nativo FreeSWITCH, sem resampling)
    - Output: 16kHz
    """
    
    CONV_API_URL = "wss://api.elevenlabs.io/v1/convai/conversation"
    
    def __init__(self, credentials: Dict[str, Any], config: RealtimeConfig):
        import os
        super().__init__(credentials, config)
        
        # Fallback para variáveis de ambiente se credentials estiver vazio
        self.api_key = credentials.get("api_key") or os.getenv("ELEVENLABS_API_KEY")
        self.agent_id = credentials.get("agent_id") or os.getenv("ELEVENLABS_AGENT_ID")
        self.voice_id = credentials.get("voice_id") or config.voice
        
        if not self.api_key:
            raise ValueError("ElevenLabs API key not configured (check DB config or ELEVENLABS_API_KEY env)")
        if not self.agent_id:
            raise ValueError("ElevenLabs Agent ID not configured (check DB config or ELEVENLABS_AGENT_ID env)")
        
        logger.info("ElevenLabs credentials loaded", extra={
            "api_key_source": "db" if credentials.get("api_key") else "env",
            "agent_id_source": "db" if credentials.get("agent_id") else "env",
            "agent_id": self.agent_id[:20] + "..." if self.agent_id else None,
        })
        
        self._ws: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[ProviderEvent] = asyncio.Queue()
    
    @property
    def name(self) -> str:
        return "elevenlabs_conversational"
    
    @property
    def input_sample_rate(self) -> int:
        return 16000  # Mesmo que FreeSWITCH
    
    @property
    def output_sample_rate(self) -> int:
        return 16000
    
    async def connect(self) -> None:
        """Conecta ao ElevenLabs Conversational AI."""
        if self._connected:
            return
        
        # URL com parâmetros
        url = f"{self.CONV_API_URL}?agent_id={self.agent_id}"
        
        headers = {
            "xi-api-key": self.api_key,
        }
        
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=None,
            ping_interval=20,
        )
        
        # Aguardar conversation_initiation_metadata
        response = await asyncio.wait_for(self._ws.recv(), timeout=10)
        event = json.loads(response)
        
        if event.get("type") != "conversation_initiation_metadata":
            raise ConnectionError(f"Unexpected initial event: {event.get('type')}")
        
        self._connected = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        logger.info("Connected to ElevenLabs Conversational AI", extra={
            "domain_uuid": self.config.domain_uuid,
            "agent_id": self.agent_id,
        })
    
    async def configure(self) -> None:
        """
        Configura a sessão.
        
        ElevenLabs usa conversation_config_override para customização.
        Ref: https://elevenlabs.io/docs/agents-platform/customization/personalization/dynamic-variables
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Construir override de configuração
        agent_config = {}
        
        # System prompt (personalidade)
        if self.config.system_prompt:
            agent_config["prompt"] = {
                "prompt": self.config.system_prompt,
            }
        
        # First message (saudação) - CRÍTICO para iniciar a conversa
        if self.config.first_message:
            agent_config["first_message"] = self.config.first_message
            logger.info(f"Setting first_message: {self.config.first_message[:50]}...", extra={
                "domain_uuid": self.config.domain_uuid,
            })
        else:
            # Se não tem first_message, usar um padrão para garantir que o agente fale
            agent_config["first_message"] = "Olá! Como posso ajudar você hoje?"
            logger.warning("No first_message configured, using default", extra={
                "domain_uuid": self.config.domain_uuid,
            })
        
        config_override = {
            "type": "conversation_config_override",
            "conversation_config_override": {
                "agent": agent_config,
            },
        }
        
        # Voice override se especificado
        if self.voice_id:
            config_override["conversation_config_override"]["tts"] = {
                "voice_id": self.voice_id,
            }
        
        logger.info("Sending conversation_config_override", extra={
            "domain_uuid": self.config.domain_uuid,
            "has_system_prompt": bool(self.config.system_prompt),
            "has_first_message": bool(self.config.first_message),
        })
        
        await self._ws.send(json.dumps(config_override))
    
    async def send_audio(self, audio_bytes: bytes) -> None:
        """
        Envia áudio para ElevenLabs.
        
        Formato: base64 PCM16 @ 16kHz
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        
        await self._ws.send(json.dumps({
            "type": "user_audio_chunk",
            "user_audio_chunk": audio_b64,
        }))
    
    async def send_text(self, text: str) -> None:
        """
        Envia texto para ElevenLabs.
        
        ElevenLabs Conversational AI suporta texto via user_transcript.
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Simular input de texto como transcript
        await self._ws.send(json.dumps({
            "type": "user_transcript",
            "user_transcript": text,
        }))
    
    async def interrupt(self) -> None:
        """Interrompe resposta atual (barge-in)."""
        if self._ws:
            await self._ws.send(json.dumps({
                "type": "interrupt",
            }))
    
    async def send_function_result(
        self,
        function_name: str,
        result: Dict[str, Any],
        call_id: Optional[str] = None
    ) -> None:
        """Envia resultado de function call."""
        if not self._ws:
            raise RuntimeError("Not connected")
        
        await self._ws.send(json.dumps({
            "type": "tool_result",
            "tool_call_id": call_id or "",
            "result": json.dumps(result),
        }))
    
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
                
                # Responder ping com pong para manter conexão ativa
                if event.get("type") == "ping":
                    ping_event = event.get("ping_event", {})
                    event_id = ping_event.get("event_id")
                    ping_ms = ping_event.get("ping_ms", 0)
                    # Aguardar o tempo indicado antes de responder
                    if ping_ms > 0:
                        await asyncio.sleep(ping_ms / 1000.0)
                    await self._ws.send(json.dumps({
                        "type": "pong",
                        "event_id": event_id,
                    }))
                    continue
                
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
        """Converte evento ElevenLabs para ProviderEvent."""
        etype = event.get("type", "")
        
        # Log de eventos recebidos para debug
        if etype not in ("ping", "pong"):
            logger.debug(f"ElevenLabs event received: {etype}", extra={
                "domain_uuid": self.config.domain_uuid,
                "event_type": etype,
            })
        
        if etype == "audio":
            # Áudio em base64 - formato: {"type": "audio", "audio_event": {"audio_base_64": "...", "event_id": 123}}
            # Ref: https://elevenlabs.io/docs/agents-platform/customization/events/client-events
            audio_event = event.get("audio_event", {})
            audio_b64 = audio_event.get("audio_base_64", "")
            audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
            logger.info(f"ElevenLabs audio received: {len(audio_bytes)} bytes", extra={
                "domain_uuid": self.config.domain_uuid,
                "audio_size": len(audio_bytes),
                "event_id": audio_event.get("event_id"),
            })
            return ProviderEvent(
                type=ProviderEventType.AUDIO_DELTA,
                data={"audio": audio_bytes},
            )
        
        if etype == "audio_done":
            return ProviderEvent(type=ProviderEventType.AUDIO_DONE, data={})
        
        if etype == "agent_response":
            # Transcript da resposta do agente
            # Formato: {"type": "agent_response", "agent_response_event": {"agent_response": "..."}}
            agent_event = event.get("agent_response_event", {})
            transcript = agent_event.get("agent_response", "")
            logger.debug(f"Agent response: {transcript[:50]}..." if transcript else "Agent response (empty)")
            return ProviderEvent(
                type=ProviderEventType.TRANSCRIPT_DONE,
                data={"transcript": transcript}
            )
        
        if etype == "user_transcript":
            # Transcript do usuário
            # Formato: {"type": "user_transcript", "user_transcription_event": {"user_transcript": "..."}}
            user_event = event.get("user_transcription_event", {})
            transcript = user_event.get("user_transcript", "")
            logger.debug(f"User transcript: {transcript[:50]}..." if transcript else "User transcript (empty)")
            return ProviderEvent(
                type=ProviderEventType.USER_TRANSCRIPT,
                data={"transcript": transcript}
            )
        
        if etype == "interruption":
            # Formato: {"type": "interruption", "interruption_event": {"event_id": 123}}
            logger.debug("Interruption event received")
            return ProviderEvent(type=ProviderEventType.SPEECH_STARTED, data={})
        
        if etype == "agent_response_started":
            return ProviderEvent(type=ProviderEventType.RESPONSE_STARTED, data={})
        
        if etype == "agent_response_done":
            return ProviderEvent(type=ProviderEventType.RESPONSE_DONE, data={})
        
        if etype == "tool_use":
            # Function call
            tool_calls = event.get("tool_calls", [])
            if tool_calls:
                tool = tool_calls[0]
                return ProviderEvent(
                    type=ProviderEventType.FUNCTION_CALL,
                    data={
                        "function_name": tool.get("name", ""),
                        "arguments": json.loads(tool.get("arguments", "{}")),
                        "call_id": tool.get("id", ""),
                    }
                )
        
        if etype == "conversation_ended":
            return ProviderEvent(
                type=ProviderEventType.SESSION_ENDED,
                data={"reason": event.get("reason", "ended")}
            )
        
        if etype == "error":
            return ProviderEvent(
                type=ProviderEventType.ERROR,
                data={"error": event.get("message", "Unknown error")}
            )
        
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
        
        logger.info("Disconnected from ElevenLabs Conversational AI", extra={
            "domain_uuid": self.config.domain_uuid
        })
