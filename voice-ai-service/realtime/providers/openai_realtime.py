"""
OpenAI Realtime API Provider.

=== KNOWLEDGE BASE REFERENCES ===
- Context7 Library ID: /websites/platform_openai (9418 snippets)
- Context7 SDK: /openai/openai-python (429 snippets)
- Docs: https://platform.openai.com/docs/guides/realtime-conversations
- API Reference: https://platform.openai.com/docs/api-reference/realtime
- SDK: https://github.com/openai/openai-python (src/openai/resources/beta/realtime/)

=== MODELOS DISPONÍVEIS (Jan/2026) ===
- gpt-realtime (GA - RECOMENDADO)
- gpt-realtime-mini (GA - mais barato, menor latência)
- gpt-4o-realtime-preview (DEPRECATED - não usar)

=== CONFIGURAÇÕES DE ÁUDIO ===
- Input: 24kHz PCM16 mono (audio/pcm)
- Output: 24kHz PCM16 mono (audio/pcm)
- Endpoint: wss://api.openai.com/v1/realtime?model={model}
- Headers: Authorization: Bearer {api_key}
- NOTA: Header OpenAI-Beta NÃO É MAIS NECESSÁRIO para modelos GA

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
Dois tipos disponíveis:

1. server_vad (baseado em silêncio):
   - threshold: 0.5 (0.0-1.0, sensibilidade)
   - prefix_padding_ms: 300 (áudio antes da fala)
   - silence_duration_ms: 500 (silêncio para encerrar turno)
   - create_response: true (auto-responder)

2. semantic_vad (baseado em semântica - RECOMENDADO):
   - eagerness: "low" | "medium" | "high"
     - low: Paciente, espera pausas longas
     - medium: Balanceado (recomendado pt-BR)
     - high: Responde rápido
   - create_response: true

3. disabled (push-to-talk):
   - Não inclui turn_detection no session.update
   - Requer input_audio_buffer.commit manual

=== API GA (General Availability) ===
Migrado para API GA em Jan/2026.
- Modelo: gpt-realtime (padrão)
- Sem header OpenAI-Beta necessário
- Limite de sessão: 60 minutos
- Custo ~20% menor que versão preview
Ref: openai.com/blog/introducing-gpt-realtime

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
import time
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
    Provider para OpenAI Realtime API (GA - General Availability).
    
    Sample rates (conforme SDK oficial):
    - Input: 24kHz PCM16 mono
    - Output: 24kHz PCM16 mono
    
    Protocolo WebSocket (GA):
    - Header: Authorization: Bearer {api_key}
    - URL: wss://api.openai.com/v1/realtime?model={model}
    
    NOTA: Header OpenAI-Beta NÃO é mais necessário para modelos GA.
    """
    
    REALTIME_URL = "wss://api.openai.com/v1/realtime"
    # Modelo padrão para Realtime API (GA - Janeiro 2026)
    # Ref: https://platform.openai.com/docs/guides/realtime
    DEFAULT_MODEL = "gpt-realtime"
    
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
        
        # Tracking de tempo de sessão (limite OpenAI: 60 minutos)
        self._session_start_time: Optional[float] = None
        self._max_session_duration_seconds: int = 55 * 60  # 55 min (5 min de margem)
        self._response_active: bool = False
    
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
        
        # Headers para API GA (General Availability)
        # NOTA: OpenAI-Beta NÃO é mais necessário para modelos GA (gpt-realtime)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        
        # Adicionar header Beta apenas para modelos preview (fallback)
        if "preview" in self.model.lower():
            headers["OpenAI-Beta"] = "realtime=v1"
            logger.warning(f"Using preview model {self.model} - consider migrating to gpt-realtime")
        
        logger.debug(f"Connecting to OpenAI Realtime (GA): {url}", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
            "is_ga": "preview" not in self.model.lower(),
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
        self._session_start_time = time.time()  # Registrar início da sessão
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        logger.info("Connected to OpenAI Realtime", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
            "session_id": self._session_id,
            "max_duration_minutes": self._max_session_duration_seconds // 60,
        })
    
    async def configure(self) -> None:
        """
        Configura sessão com prompt, voz, VAD, tools.
        
        FORMATO GA (gpt-realtime):
        {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {...},
                "instructions": "system prompt",
                ...
            }
        }
        
        NOTA: Formato GA é o mesmo do Beta para session.update.
        Ref: https://platform.openai.com/docs/api-reference/realtime
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Vozes disponíveis: alloy, ash, ballad, coral, echo, sage, shimmer, verse
        voice = self.config.voice or "alloy"
        
        # === FORMATO GA (gpt-realtime) ===
        # Campos dentro de "session" wrapper
        # Documentação oficial: session.type deve ser "realtime" (speech-to-speech) ou "transcription"
        session_config = {
            "type": "session.update",
            "session": {
                # Tipo de sessão: "realtime" para conversação por voz (speech-to-speech)
                "type": "realtime",
                
                # Modelo (opcional, já especificado na URL de conexão)
                "model": self.config.model or "gpt-4o-realtime-preview",
                
                # System prompt
                "instructions": self.config.system_prompt or "",
                
                # Tools (function calling)
                "tools": self.config.tools or DEFAULT_TOOLS,
                "tool_choice": "auto",
                
                # Configuração de áudio (formato GA)
                "audio": {
                    "output": {
                        "voice": voice,
                    },
                },
            }
        }
        
        # VAD - Turn Detection (nível superior)
        # Tipos: "server_vad", "semantic_vad" ou None (push-to-talk)
        vad_config = self._build_vad_config()
        if vad_config is not None:
            session_config["session"]["turn_detection"] = vad_config
        # Se vad_config é None, não incluímos turn_detection = push-to-talk
        
        vad_type = vad_config.get("type") if vad_config else "disabled"
        vad_eagerness = vad_config.get("eagerness") if vad_config else None
        
        logger.info(f"Sending session.update (GA) - voice={voice}, vad={vad_type}", extra={
            "domain_uuid": self.config.domain_uuid,
            "has_instructions": bool(self.config.system_prompt),
            "voice": voice,
            "vad_type": vad_type,
            "vad_eagerness": vad_eagerness,
        })
        
        try:
            await self._ws.send(json.dumps(session_config))
            logger.info("session.update sent successfully")
        except Exception as e:
            logger.error(f"Failed to send session.update: {e}")
            raise
        
        # Se houver first_message (saudação), enviar como texto e solicitar resposta
        if self.config.first_message:
            logger.debug(f"Sending first message: {self.config.first_message[:50]}...")
            await self.send_text(self.config.first_message)
            # Solicitar resposta do modelo
            if not self._response_active:
                await self._ws.send(json.dumps({"type": "response.create"}))
            else:
                logger.debug("Response already active; skipping response.create for first_message")
    
    def _build_vad_config(self) -> Dict[str, Any]:
        """
        Constrói configuração de VAD (Voice Activity Detection).
        
        Suporta dois tipos:
        - server_vad: Baseado em silêncio (threshold, silence_duration_ms)
        - semantic_vad: Baseado em semântica (eagerness) - MAIS INTELIGENTE
        
        semantic_vad entende quando o usuário TERMINOU de falar,
        não apenas quando fez uma pausa. Melhor para pt-BR e linguagem natural.
        
        Parâmetros semantic_vad (conforme docs Jan/2026):
        - eagerness: "low" | "medium" | "high"
          - low: Paciente, espera pausas longas antes de responder
          - medium: Balanceado (recomendado para pt-BR)
          - high: Responde rápido, pode interromper
        - create_response: true para responder automaticamente
        - interrupt_response: true para permitir barge-in
        
        Ref: https://platform.openai.com/docs/guides/realtime-transcription
        Ref: https://platform.openai.com/docs/guides/realtime-model-capabilities
        """
        vad_type = getattr(self.config, 'vad_type', 'server_vad')
        
        if vad_type == "semantic_vad":
            # semantic_vad: Mais inteligente, entende contexto semântico
            eagerness = getattr(self.config, 'vad_eagerness', 'medium')
            
            # Validar eagerness (deve ser low, medium ou high)
            valid_eagerness = ["low", "medium", "high"]
            if eagerness not in valid_eagerness:
                logger.warning(f"Invalid eagerness '{eagerness}', using 'medium'")
                eagerness = "medium"
            
            logger.info(f"VAD: semantic_vad (eagerness={eagerness})")
            
            return {
                "type": "semantic_vad",
                "eagerness": eagerness,
                "create_response": True,
                # interrupt_response: permite usuário interromper agente (barge-in)
                # IMPORTANTE: Sem isso, barge-in não funciona com semantic_vad
                "interrupt_response": True,
            }
        
        elif vad_type == "disabled":
            # Push-to-talk: VAD desabilitado, controle manual
            logger.info("VAD: disabled (push-to-talk mode)")
            return None  # Sem turn_detection = push-to-talk
        
        else:
            # server_vad: Baseado em silêncio (padrão tradicional)
            threshold = self.config.vad_threshold or 0.5
            silence_ms = self.config.silence_duration_ms or 500
            prefix_ms = self.config.prefix_padding_ms or 300
            
            logger.info(f"VAD: server_vad (threshold={threshold}, silence={silence_ms}ms)")
            
            return {
                "type": "server_vad",
                "threshold": threshold,
                "prefix_padding_ms": prefix_ms,
                "silence_duration_ms": silence_ms,
                "create_response": True,
            }
    
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

    async def commit_audio_buffer(self) -> None:
        """Commit manual do buffer de áudio (necessário quando VAD está desabilitado)."""
        if not self._ws:
            raise RuntimeError("Not connected")

        await self._ws.send(json.dumps({
            "type": "input_audio_buffer.commit"
        }))
        logger.debug("Audio buffer committed", extra={
            "domain_uuid": self.config.domain_uuid,
        })

    async def request_response(self) -> None:
        """Solicita resposta do modelo (útil para push-to-talk)."""
        if not self._ws:
            raise RuntimeError("Not connected")
        if not self._response_active:
            await self._ws.send(json.dumps({"type": "response.create"}))
            logger.debug("Response requested from OpenAI", extra={
                "domain_uuid": self.config.domain_uuid,
            })
        else:
            logger.debug("Response already active; skipping response.create", extra={
                "domain_uuid": self.config.domain_uuid,
            })
    
    async def send_text(self, text: str, request_response: bool = True) -> None:
        """
        Envia mensagem de texto e solicita resposta.
        
        Ref: conversation.item.create + response.create events (SDK oficial)
        
        Args:
            text: Texto a enviar (será interpretado como input do usuário)
            request_response: Se True, solicita resposta do modelo após enviar
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
        
        # Solicitar resposta do modelo (gera áudio TTS)
        if request_response:
            if not self._response_active:
                await self._ws.send(json.dumps({"type": "response.create"}))
                logger.debug("Response requested from OpenAI", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
            else:
                logger.debug("Response already active; skipping response.create", extra={
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
            # Otimista: marcar como não ativa para evitar response.create concorrente
            self._response_active = False
    
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
        if not self._response_active:
            await self._ws.send(json.dumps({"type": "response.create"}))
        else:
            logger.debug("Response already active; skipping response.create after function", extra={
                "domain_uuid": self.config.domain_uuid,
                "call_id": call_id,
            })
        
        logger.debug(f"Function result sent to OpenAI: {function_name}", extra={
            "domain_uuid": self.config.domain_uuid,
            "call_id": call_id,
        })
    
    def get_session_remaining_seconds(self) -> Optional[int]:
        """
        Retorna segundos restantes antes do limite de sessão OpenAI.
        
        OpenAI Realtime tem limite de 60 minutos por sessão.
        Retornamos tempo restante para permitir reconexão preventiva.
        
        Returns:
            Segundos restantes ou None se não conectado
        """
        if not self._session_start_time:
            return None
        
        elapsed = time.time() - self._session_start_time
        remaining = self._max_session_duration_seconds - elapsed
        return max(0, int(remaining))
    
    def is_session_expiring_soon(self, threshold_seconds: int = 300) -> bool:
        """
        Verifica se sessão está perto de expirar.
        
        Args:
            threshold_seconds: Segundos de threshold (default: 5 min)
        
        Returns:
            True se sessão expira em menos de threshold_seconds
        """
        remaining = self.get_session_remaining_seconds()
        if remaining is None:
            return False
        return remaining < threshold_seconds
    
    async def receive_events(self) -> AsyncIterator[ProviderEvent]:
        """Generator de eventos."""
        while self._connected:
            try:
                # Verificar limite de tempo de sessão a cada iteração
                if self.is_session_expiring_soon(threshold_seconds=60):
                    remaining = self.get_session_remaining_seconds()
                    logger.warning(
                        f"OpenAI session expiring in {remaining}s (limit: 60min)",
                        extra={
                            "domain_uuid": self.config.domain_uuid,
                            "session_id": self._session_id,
                            "remaining_seconds": remaining,
                        }
                    )
                    # Emitir evento de warning para permitir ação preventiva
                    yield ProviderEvent(
                        type=ProviderEventType.ERROR,
                        data={
                            "error": {
                                "code": "session_expiring",
                                "message": f"Session expiring in {remaining}s",
                            }
                        }
                    )
                    break
                
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
        # COMPATIBILIDADE: Inclui formatos antigos (response.audio.*) e novos (response.output_audio.*)
        KNOWN_EVENTS = {
            # Áudio (formatos antigo e novo)
            "response.audio.delta", "response.output_audio.delta",
            "response.audio.done", "response.output_audio.done",
            # Transcrição do assistente
            "response.audio_transcript.delta", "response.audio_transcript.done",
            # Transcrição do usuário (STT)
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.failed",
            "conversation.item.input_audio_transcription.delta",  # Novo em 2026
            # VAD
            "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
            # Response lifecycle
            "response.created", "response.done",
            "response.content_part.added", "response.content_part.done",
            "response.output_item.added", "response.output_item.done",
            # Text output (se modality text habilitada)
            "response.text.delta", "response.text.done",
            # Function calls
            "response.function_call_arguments.delta", "response.function_call_arguments.done",
            # Session
            "session.created", "session.updated",
            # Buffers
            "input_audio_buffer.committed", "input_audio_buffer.cleared",
            "conversation.item.created", "conversation.item.truncated",
            # Rate limits (info, não erro)
            "rate_limits.updated",
            # Errors
            "error",
        }
        
        if etype not in KNOWN_EVENTS:
            logger.debug(f"OpenAI event received: {etype}", extra={
                "domain_uuid": self.config.domain_uuid,
                "event_data_preview": str(event)[:200],
            })
        
        # ===== ÁUDIO OUTPUT =====
        # COMPATIBILIDADE: API pode retornar response.audio.delta OU response.output_audio.delta
        # dependendo da versão/modelo. Suportamos ambos.
        if etype in ("response.audio.delta", "response.output_audio.delta"):
            audio_b64 = event.get("delta", "")
            audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
            
            logger.info(f"OpenAI audio received: {len(audio_bytes)} bytes", extra={
                "domain_uuid": self.config.domain_uuid,
                "response_id": event.get("response_id"),
                "event_type": etype,
            })
            
            return ProviderEvent(
                type=ProviderEventType.AUDIO_DELTA,
                data={"audio": audio_bytes},
                response_id=event.get("response_id"),
                item_id=event.get("item_id"),
            )
        
        # COMPATIBILIDADE: Suporta ambos os formatos de audio.done
        if etype in ("response.audio.done", "response.output_audio.done"):
            logger.info("OpenAI audio output done", extra={
                "domain_uuid": self.config.domain_uuid,
                "event_type": etype,
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
            self._response_active = True
            logger.debug("Response started", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(type=ProviderEventType.RESPONSE_STARTED, data={})
        
        if etype == "response.done":
            self._response_active = False
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
            
            # Erros não-críticos (esperados em alguns fluxos)
            non_critical_errors = [
                "response_cancel_not_active",  # Tentar cancelar quando não há resposta
                "conversation_already_has_active_response",  # Já tem resposta ativa
            ]
            
            if error_code in non_critical_errors:
                logger.warning(f"OpenAI non-critical: {error_code} - {error_message}", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
                return None  # Ignorar, não é erro crítico
            
            logger.error(f"OpenAI error: {error_code} - {error_message}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            
            if error_code == "rate_limit_exceeded":
                return ProviderEvent(type=ProviderEventType.RATE_LIMITED, data={"error": error})
            
            return ProviderEvent(type=ProviderEventType.ERROR, data={"error": error})
        
        # ===== SESSION/META EVENTS (confirmações, ignorar) =====
        if etype in (
            "session.updated", "session.created",
            "input_audio_buffer.committed", "input_audio_buffer.cleared",
            "conversation.item.created",
            "response.content_part.added", "response.content_part.done",
            "response.output_item.added", "response.output_item.done",
            "rate_limits.updated",  # Info de rate limits (não é erro)
        ):
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
