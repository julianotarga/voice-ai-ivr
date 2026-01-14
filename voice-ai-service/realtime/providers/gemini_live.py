"""
Google Gemini 2.0 Flash Live API Provider.

Referências:
- Context7: /googleapis/python-genai
- .context/docs/data-flow.md: Fluxo Realtime v2
- openspec/changes/voice-ai-realtime/design.md: Decision 4

Gemini Live API suporta streaming multimodal bidirecional.
- Input: 16kHz PCM16 ou outros formatos
- Output: 16kHz ou 24kHz
- SDK: google-genai
"""

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Optional

from .base import (
    BaseRealtimeProvider,
    ProviderEvent,
    ProviderEventType,
    RealtimeConfig,
)

logger = logging.getLogger(__name__)


class GeminiLiveProvider(BaseRealtimeProvider):
    """
    Provider para Google Gemini 2.0 Flash Live API.
    
    Usa o SDK google-genai para streaming bidirecional.
    
    Sample rates:
    - Input: 16kHz PCM16
    - Output: 16kHz PCM16 (ou 24kHz dependendo da config)
    """
    
    DEFAULT_MODEL = "gemini-2.0-flash-exp"
    
    def __init__(self, credentials: Dict[str, Any], config: RealtimeConfig):
        import os
        super().__init__(credentials, config)
        
        # Fallback para variáveis de ambiente se credentials estiver vazio
        self.api_key = credentials.get("api_key") or os.getenv("GOOGLE_API_KEY")
        self.model = credentials.get("model", self.DEFAULT_MODEL)
        
        if not self.api_key:
            raise ValueError("Google API key not configured (check DB config or GOOGLE_API_KEY env)")
        
        logger.info("Gemini Live credentials loaded", extra={
            "api_key_source": "db" if credentials.get("api_key") else "env",
            "model": self.model,
        })
        
        self._session = None
        self._client = None
        self._receive_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[ProviderEvent] = asyncio.Queue()
    
    @property
    def name(self) -> str:
        return "gemini_live"
    
    @property
    def input_sample_rate(self) -> int:
        return 16000
    
    @property
    def output_sample_rate(self) -> int:
        return 16000
    
    async def connect(self) -> None:
        """Conecta ao Gemini Live API."""
        if self._connected:
            return
        
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise ImportError("google-genai package required. Install: pip install google-genai")
        
        # Criar cliente
        self._client = genai.Client(api_key=self.api_key)
        
        # Configurar conexão
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.config.voice or "Aoede"
                    )
                )
            ),
        )
        
        # Conectar
        self._session = await self._client.aio.live.connect(
            model=self.model,
            config=config
        )
        
        self._connected = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        logger.info("Connected to Gemini Live API", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
        })
    
    async def configure(self) -> None:
        """
        Configura a sessão.
        
        Gemini usa system instruction na conexão inicial.
        """
        if not self._session:
            raise RuntimeError("Not connected")
        
        # Enviar system prompt como contexto inicial
        if self.config.system_prompt:
            await self._session.send(input=f"[System] {self.config.system_prompt}")
        
        # Enviar saudação inicial
        if self.config.first_message:
            await self._session.send(input=self.config.first_message)
    
    async def send_audio(self, audio_bytes: bytes) -> None:
        """
        Envia áudio para Gemini.
        
        Formato: PCM16 @ 16kHz
        """
        if not self._session:
            raise RuntimeError("Not connected")
        
        try:
            from google.genai import types
            
            # Criar realtime input com áudio
            await self._session.send(
                realtime_input=types.LiveClientRealtimeInput(
                    audio=audio_bytes
                )
            )
        except Exception as e:
            logger.error(f"Error sending audio to Gemini: {e}")
    
    async def send_text(self, text: str) -> None:
        """Envia texto para Gemini."""
        if not self._session:
            raise RuntimeError("Not connected")
        
        await self._session.send(input=text)
    
    async def interrupt(self) -> None:
        """
        Interrompe resposta atual.
        
        Gemini usa activity_end para sinalizar interrupção.
        """
        if self._session:
            try:
                from google.genai import types
                await self._session.send(
                    realtime_input=types.LiveClientRealtimeInput(
                        activity_end=True
                    )
                )
            except Exception as e:
                logger.error(f"Error interrupting Gemini: {e}")
    
    async def send_function_result(
        self,
        function_name: str,
        result: Dict[str, Any],
        call_id: Optional[str] = None
    ) -> None:
        """Envia resultado de function call."""
        if not self._session:
            raise RuntimeError("Not connected")
        
        # Gemini tool response
        try:
            from google.genai import types
            await self._session.send(
                tool_response=types.LiveClientToolResponse(
                    function_responses=[
                        types.FunctionResponse(
                            name=function_name,
                            response=result
                        )
                    ]
                )
            )
        except Exception as e:
            logger.error(f"Error sending function result to Gemini: {e}")
    
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
        """Loop de recebimento de eventos do Gemini."""
        if not self._session:
            return
        
        try:
            async for response in self._session.receive():
                provider_event = self._parse_response(response)
                if provider_event:
                    await self._event_queue.put(provider_event)
        except Exception as e:
            logger.error(f"Receive loop error: {e}")
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.ERROR,
                data={"error": str(e)}
            ))
    
    def _parse_response(self, response: Any) -> Optional[ProviderEvent]:
        """Converte resposta Gemini para ProviderEvent."""
        try:
            # Server content com áudio ou texto
            if hasattr(response, 'server_content') and response.server_content:
                content = response.server_content
                
                # Verificar se há model_turn
                if hasattr(content, 'model_turn') and content.model_turn:
                    for part in content.model_turn.parts:
                        # Áudio inline
                        if hasattr(part, 'inline_data') and part.inline_data:
                            if part.inline_data.mime_type and 'audio' in part.inline_data.mime_type:
                                return ProviderEvent(
                                    type=ProviderEventType.AUDIO_DELTA,
                                    data={"audio": part.inline_data.data}
                                )
                        
                        # Texto
                        if hasattr(part, 'text') and part.text:
                            return ProviderEvent(
                                type=ProviderEventType.TRANSCRIPT_DELTA,
                                data={"transcript": part.text}
                            )
                
                # Verificar turn complete
                if hasattr(content, 'turn_complete') and content.turn_complete:
                    return ProviderEvent(type=ProviderEventType.RESPONSE_DONE, data={})
            
            # Input transcription (STT do usuário)
            if hasattr(response, 'input_transcription') and response.input_transcription:
                return ProviderEvent(
                    type=ProviderEventType.USER_TRANSCRIPT,
                    data={"transcript": response.input_transcription.text or ""}
                )
            
            # Tool call
            if hasattr(response, 'tool_call') and response.tool_call:
                tool_call = response.tool_call
                if hasattr(tool_call, 'function_calls') and tool_call.function_calls:
                    fc = tool_call.function_calls[0]
                    return ProviderEvent(
                        type=ProviderEventType.FUNCTION_CALL,
                        data={
                            "function_name": fc.name,
                            "arguments": dict(fc.args) if fc.args else {},
                            "call_id": fc.id or "",
                        }
                    )
            
            return None
            
        except Exception as e:
            logger.error(f"Error parsing Gemini response: {e}")
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
        
        if self._session:
            try:
                # Fechar sessão
                await self._session.close()
            except Exception:
                pass
            self._session = None
        
        logger.info("Disconnected from Gemini Live API", extra={
            "domain_uuid": self.config.domain_uuid
        })
