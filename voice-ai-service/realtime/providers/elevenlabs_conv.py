"""
ElevenLabs Conversational AI Provider.

Referências:
- AsyncAPI oficial: https://elevenlabs.io/docs/agents-platform/api-reference/agents-platform/websocket
- SDK Python: https://github.com/elevenlabs/elevenlabs-python
- Context7: /elevenlabs/elevenlabs-python

ElevenLabs Conversational AI usa WebSocket para streaming bidirecional.
- Input: 16kHz PCM16 (base64)
- Output: 16kHz PCM16 (base64)
- Endpoint: wss://api.elevenlabs.io/v1/convai/conversation

Eventos Client → Server (PUBLISH):
- UserAudioChunk: {user_audio_chunk: "base64..."} (SEM type!)
- Pong: {type: "pong", event_id: int}
- ConversationInitiationClientData: {type: "conversation_initiation_client_data", ...}
- ClientToolResult: {type: "client_tool_result", tool_call_id, result: str, is_error}
- UserMessage: {type: "user_message", text: str}

Eventos Server → Client (SUBSCRIBE):
- audio: {type: "audio", audio_event: {audio_base_64, event_id}}
- user_transcript: {type: "user_transcript", user_transcription_event: {user_transcript}}
- agent_response: {type: "agent_response", agent_response_event: {agent_response}}
- client_tool_call: {type: "client_tool_call", client_tool_call: {tool_name, tool_call_id, parameters}}
- ping: {type: "ping", ping_event: {event_id, ping_ms}}
- interruption: {type: "interruption", interruption_event: {event_id}}
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
        # IMPORTANTE:
        # A AsyncAPI/Agents Platform pode bloquear override de voice_id dependendo do Agent config.
        # Já vimos o erro: "Override for field 'voice_id' is not allowed by config."
        # Portanto, NÃO fazemos fallback para config.voice e NÃO enviamos override por padrão.
        # Só enviaremos se houver voice_id explícito em credentials e allow_voice_id_override=true.
        self.voice_id = credentials.get("voice_id")
        self.allow_voice_id_override = bool(credentials.get("allow_voice_id_override", False))
        
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
        
        Ref: SDK oficial elevenlabs-python/conversation.py
        O tipo correto é "conversation_initiation_client_data" (não "conversation_config_override")!
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Construir override de configuração do agente
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
        
        # Construir conversation_config_override
        conversation_config_override = {
            "agent": agent_config,
        }
        
        # Voice override se especificado
        # Conforme AsyncAPI oficial, voice_id override existe, mas pode ser bloqueado pelo Agent.
        # Ref: https://elevenlabs.io/docs/agents-platform/api-reference/agents-platform/websocket
        if self.voice_id and self.allow_voice_id_override:
            conversation_config_override["tts"] = {
                "voice_id": self.voice_id,
            }
        elif self.voice_id and not self.allow_voice_id_override:
            logger.warning(
                "voice_id presente nas credenciais mas override desabilitado; não enviaremos tts.voice_id para evitar policy violation",
                extra={"domain_uuid": self.config.domain_uuid},
            )
        
        # Mensagem inicial - formato do SDK oficial!
        # Tipo: "conversation_initiation_client_data" (NÃO "conversation_config_override")
        initiation_message = {
            "type": "conversation_initiation_client_data",
            "conversation_config_override": conversation_config_override,
            "dynamic_variables": {},  # Pode ser usado para passar variáveis dinâmicas
        }
        
        logger.info("Sending conversation_initiation_client_data", extra={
            "domain_uuid": self.config.domain_uuid,
            "has_system_prompt": bool(self.config.system_prompt),
            "has_first_message": bool(agent_config.get("first_message")),
            "has_voice_id": bool(self.voice_id),
        })
        
        await self._ws.send(json.dumps(initiation_message))
    
    async def send_audio(self, audio_bytes: bytes) -> None:
        """
        Envia áudio para ElevenLabs.
        
        Formato: base64 PCM16 @ 16kHz
        Ref: SDK oficial elevenlabs-python/conversation.py
        IMPORTANTE: NÃO incluir "type" - apenas {"user_audio_chunk": "base64..."}
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        
        # SDK oficial NÃO inclui "type" no payload de áudio!
        await self._ws.send(json.dumps({
            "user_audio_chunk": audio_b64,
        }))
    
    async def send_text(self, text: str) -> None:
        """
        Envia texto para ElevenLabs.
        
        Ref: SDK oficial - tipo "user_message" (não "user_transcript")
        user_transcript é o que o servidor ENVIA de volta, não o que enviamos!
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Tipo correto: user_message (não user_transcript!)
        await self._ws.send(json.dumps({
            "type": "user_message",
            "text": text,
        }))
    
    async def interrupt(self) -> None:
        """
        Interrompe resposta atual (barge-in).
        
        IMPORTANTE (AsyncAPI oficial):
        No schema Client → Server não existe mensagem `type: "interrupt"`.
        As mensagens permitidas incluem `user_activity`, então usamos isso para
        sinalizar atividade do usuário sem violar o contrato.
        
        Ref: https://elevenlabs.io/docs/agents-platform/api-reference/agents-platform/websocket
        """
        if self._ws:
            await self._ws.send(json.dumps({
                "type": "user_activity",
            }))
    
    async def send_function_result(
        self,
        function_name: str,
        result: Dict[str, Any],
        call_id: Optional[str] = None
    ) -> None:
        """
        Envia resultado de function call.
        
        Ref: SDK oficial - tipo "client_tool_result" (não "tool_result")
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        await self._ws.send(json.dumps({
            "type": "client_tool_result",
            "tool_call_id": call_id or "",
            "result": json.dumps(result) if isinstance(result, dict) else str(result),
            "is_error": False,
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
                
                # Responder ping com pong IMEDIATAMENTE para manter conexão ativa
                # Ref: SDK oficial elevenlabs-python/conversation.py - ping_ms é só para medir latência
                if event.get("type") == "ping":
                    ping_event = event.get("ping_event", {})
                    event_id = ping_event.get("event_id")
                    # Responder IMEDIATAMENTE - NÃO aguardar ping_ms!
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
        
        if etype == "client_tool_call":
            # Function call - Ref: SDK oficial
            # Formato: {"type": "client_tool_call", "client_tool_call": {"tool_name": "...", "tool_call_id": "...", "parameters": {...}}}
            tool_call = event.get("client_tool_call", {})
            tool_name = tool_call.get("tool_name", "")
            tool_call_id = tool_call.get("tool_call_id", "")
            parameters = tool_call.get("parameters", {})
            
            logger.info(f"Tool call received: {tool_name}", extra={
                "domain_uuid": self.config.domain_uuid,
                "tool_call_id": tool_call_id,
            })
            
            return ProviderEvent(
                type=ProviderEventType.FUNCTION_CALL,
                data={
                    "function_name": tool_name,
                    "arguments": parameters,
                    "call_id": tool_call_id,
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
        
        # ===== EVENTOS ADICIONAIS (conforme AsyncAPI oficial) =====
        # Ref: https://elevenlabs.io/docs/agents-platform/api-reference/agents-platform/websocket
        
        if etype == "agent_response_correction":
            # Correção de resposta truncada/interrompida
            correction_event = event.get("agent_response_correction_event", {})
            logger.debug("Agent response correction", extra={
                "domain_uuid": self.config.domain_uuid,
                "original": correction_event.get("original_agent_response", "")[:50],
                "corrected": correction_event.get("corrected_agent_response", "")[:50],
            })
            return ProviderEvent(
                type=ProviderEventType.TRANSCRIPT_DONE,
                data={"transcript": correction_event.get("corrected_agent_response", "")}
            )
        
        if etype == "vad_score":
            # Voice Activity Detection score (0.0 - 1.0)
            vad_event = event.get("vad_score_event", {})
            vad_score = vad_event.get("vad_score", 0.0)
            # Log apenas se score alto (indicando fala)
            if vad_score > 0.5:
                logger.debug(f"VAD score: {vad_score:.2f}", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
            return None  # Não emitir evento, apenas log
        
        if etype == "contextual_update":
            # Update de contexto (informação adicional)
            text = event.get("text", "")
            logger.debug(f"Contextual update: {text[:50]}...", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return None  # Informativo apenas
        
        if etype == "internal_tentative_agent_response":
            # Resposta preliminar do agente (interno, para debug)
            tentative_event = event.get("tentative_agent_response_internal_event", {})
            tentative_text = tentative_event.get("tentative_agent_response", "")
            logger.debug(f"Tentative response: {tentative_text[:50]}...", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(
                type=ProviderEventType.TRANSCRIPT_DELTA,
                data={"transcript": tentative_text}
            )
        
        # Evento desconhecido - log para debug
        logger.warning(f"Unknown ElevenLabs event: {etype}", extra={
            "domain_uuid": self.config.domain_uuid,
            "event_preview": str(event)[:200],
        })
        
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
