"""
Google Gemini Live API Provider (Multimodal Live API).

=== KNOWLEDGE BASE REFERENCES ===
- Docs oficiais: https://ai.google.dev/gemini-api/docs/live
- Context7 Library ID: /websites/ai_google_dev_api (buscar com query-docs)
- Cookbook GitHub: https://github.com/google-gemini/cookbook
- Vertex AI Docs: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api

=== MODELOS DISPONÍVEIS (Jan/2026) ===
- gemini-2.5-flash-live (recomendado para baixa latência)
- gemini-3-flash-preview (mais recente)
- gemini-2.0-flash-exp (experimental)

=== FORMATO WEBSOCKET (BidiGenerateContent) ===
- URL: wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=API_KEY
- Primeira mensagem: { "setup": BidiGenerateContentSetup }
- Aguardar: { "setupComplete": ... }
- Áudio input: { "realtimeInput": { "audio": { "mimeType": "audio/pcm;rate=16000", "data": "base64..." } } }
- Áudio output: serverContent.modelTurn.parts[].inlineData (PCM @ 24kHz)
- Interrupção: { "realtimeInput": { "activityEnd": {} } }

=== CONFIGURAÇÕES DE ÁUDIO ===
- Input: 16-bit PCM, 16kHz, mono
- Output: 16-bit PCM, 24kHz, mono (precisa resample para 16kHz do FreeSWITCH)

=== VAD (Voice Activity Detection) ===
- Nativo no Gemini Live
- Suporta barge-in (interrupção do usuário)
- Configurável via generationConfig
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


# Vozes disponíveis no Gemini Live (prebuiltVoiceConfig)
# Ref: https://ai.google.dev/gemini-api/docs/live#voices
GEMINI_VOICES = {
    # Vozes principais
    "Aoede": {"gender": "female", "style": "warm"},
    "Charon": {"gender": "male", "style": "deep"},
    "Fenrir": {"gender": "male", "style": "strong"},
    "Kore": {"gender": "female", "style": "bright"},
    "Puck": {"gender": "neutral", "style": "playful"},
    # Vozes adicionais (Gemini 2.5+)
    "Orion": {"gender": "male", "style": "professional"},
    "Leda": {"gender": "female", "style": "calm"},
}

# Lista simples para validação
GEMINI_VOICE_NAMES = list(GEMINI_VOICES.keys())


class GeminiLiveProvider(BaseRealtimeProvider):
    """
    Provider para Google Gemini 2.0 Flash Live API.
    
    Usa WebSocket diretamente (não SDK google-genai) para maior controle.
    
    Sample rates (conforme documentação oficial):
    - Input: 16kHz PCM16 mono
    - Output: 24kHz PCM16 mono (CORRIGIDO - era 16kHz)
    
    Protocolo WebSocket:
    1. Conectar via WSS
    2. Enviar { "setup": BidiGenerateContentSetup }
    3. Aguardar { "setupComplete": ... }
    4. Enviar/receber mensagens
    """
    
    # URL da Live API (requer API key como query param)
    LIVE_API_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    
    # Modelo padrão para Live API (bidiGenerateContent)
    # IMPORTANTE: Testar diferentes modelos para encontrar o que funciona
    # A API Live pode ter modelos específicos que mudam com frequência
    # Ref: https://ai.google.dev/gemini-api/docs/models
    DEFAULT_MODEL = "models/gemini-2.0-flash"
    
    # Modelos para tentar em ordem de preferência
    AVAILABLE_MODELS = [
        "models/gemini-2.0-flash",             # Modelo base
        "models/gemini-2.0-flash-001",         # Versão específica
        "models/gemini-2.0-flash-live",        # Live específico
    ]
    
    def __init__(self, credentials: Dict[str, Any], config: RealtimeConfig):
        import os
        super().__init__(credentials, config)
        
        # Fallback para variáveis de ambiente se credentials estiver vazio
        self.api_key = credentials.get("api_key") or os.getenv("GOOGLE_API_KEY")
        
        if not self.api_key:
            raise ValueError("Google API key not configured (check DB config or GOOGLE_API_KEY env)")
        
        # Obter modelo das credenciais ou usar padrão
        requested_model = credentials.get("model", self.DEFAULT_MODEL)
        
        # Adicionar prefixo models/ se necessário
        if requested_model and not requested_model.startswith("models/"):
            self.model = f"models/{requested_model}"
        else:
            self.model = requested_model or self.DEFAULT_MODEL
        
        logger.info(f"Using Gemini model: {self.model}")
        
        # Validar voz
        voice = self.config.voice or "Aoede"
        if voice not in GEMINI_VOICE_NAMES:
            logger.warning(f"Voice '{voice}' not in known Gemini voices {GEMINI_VOICE_NAMES}, using Aoede")
            voice = "Aoede"
        self._voice = voice
        
        # Configurações de áudio
        self._input_sample_rate = 16000  # Gemini aceita 16kHz
        self._output_sample_rate = 24000  # Gemini retorna 24kHz
        
        logger.info("Gemini Live credentials loaded", extra={
            "api_key_source": "db" if credentials.get("api_key") else "env",
            "model": self.model,
            "voice": self._voice,
        })
        
        self._ws: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[ProviderEvent] = asyncio.Queue()
        self._setup_complete = False
    
    @property
    def name(self) -> str:
        return "gemini_live"
    
    @property
    def input_sample_rate(self) -> int:
        return 16000  # Gemini aceita 16kHz
    
    @property
    def output_sample_rate(self) -> int:
        return 24000  # CORRIGIDO: Gemini output é 24kHz (era 16kHz)
    
    async def connect(self) -> None:
        """Conecta ao Gemini Live API via WebSocket."""
        if self._connected:
            return
        
        # URL com API key como query param
        url = f"{self.LIVE_API_URL}?key={self.api_key}"
        
        logger.debug(f"Connecting to Gemini Live: {self.model}", extra={
            "domain_uuid": self.config.domain_uuid,
        })
        
        self._ws = await websockets.connect(
            url,
            max_size=None,
            ping_interval=20,
        )
        
        self._connected = True
        
        logger.info("WebSocket connected to Gemini Live", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
        })
    
    async def configure(self) -> None:
        """
        Configura sessão via BidiGenerateContentSetup.
        
        Ref: https://ai.google.dev/gemini-api/docs/live
        Ref: https://github.com/google-gemini/cookbook (quickstarts/Multimodal_Live_API)
        
        IMPORTANTE:
        - systemInstruction DEVE estar no setup inicial
        - responseModalities: ["AUDIO"] para voz
        - speechConfig com voiceConfig para escolher a voz
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Construir configuração de setup conforme docs oficiais
        # Ref: BidiGenerateContentSetup
        generation_config = {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": self._voice
                    }
                }
            }
        }
        
        # Adicionar maxOutputTokens se configurado (limitar resposta)
        if self.config.max_response_output_tokens and self.config.max_response_output_tokens > 0:
            generation_config["maxOutputTokens"] = int(self.config.max_response_output_tokens)
        
        # Configurar temperatura se especificada
        if hasattr(self.config, 'temperature') and self.config.temperature is not None:
            generation_config["temperature"] = self.config.temperature
        
        setup_message = {
            "setup": {
                "model": self.model,
                "generationConfig": generation_config,
            }
        }
        
        # System instruction (obrigatório para contexto do agente)
        # DEVE estar no setup, não pode ser enviado depois
        if self.config.system_prompt:
            setup_message["setup"]["systemInstruction"] = {
                "parts": [{"text": self.config.system_prompt}]
            }
        
        # Function calling / Tools
        if self.config.tools:
            setup_message["setup"]["tools"] = self._convert_tools(self.config.tools)
        
        logger.debug("Sending Gemini Live setup", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
            "voice": self._voice,
            "has_system_instruction": bool(self.config.system_prompt),
            "has_tools": bool(self.config.tools),
            "max_output_tokens": generation_config.get("maxOutputTokens"),
        })
        
        await self._ws.send(json.dumps(setup_message))
        
        # Aguardar setupComplete (timeout de 10s)
        try:
            response = await asyncio.wait_for(self._ws.recv(), timeout=10)
            data = json.loads(response)
            
            if "setupComplete" not in data:
                raise ConnectionError(f"Gemini setup failed, got: {data}")
            
            self._setup_complete = True
            
            logger.info("Gemini Live setup complete", extra={
                "domain_uuid": self.config.domain_uuid,
                "model": self.model,
            })
            
        except asyncio.TimeoutError:
            raise ConnectionError("Gemini setup timed out (10s)")
        
        # Iniciar receive loop
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        # Enviar first_message se configurado (como texto do usuário)
        if self.config.first_message:
            logger.debug(f"Sending first message: {self.config.first_message[:50]}...")
            await self.send_text(self.config.first_message)
    
    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Converte tools do formato OpenAI para formato Gemini."""
        gemini_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                gemini_tools.append({
                    "functionDeclarations": [{
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {})
                    }]
                })
        return gemini_tools
    
    async def send_audio(self, audio_bytes: bytes) -> None:
        """
        Envia áudio para Gemini.
        
        Formato: PCM16 @ 16kHz, base64 encoded
        Ref: BidiGenerateContentRealtimeInput
        """
        if not self._ws or not self._setup_complete:
            raise RuntimeError("Not connected or setup not complete")
        
        # Encode áudio em base64
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        
        # Formato correto conforme documentação
        message = {
            "realtimeInput": {
                "audio": {
                    "mimeType": "audio/pcm;rate=16000",
                    "data": audio_b64
                }
            }
        }
        
        await self._ws.send(json.dumps(message))
        
        logger.debug(f"Audio chunk sent to Gemini: {len(audio_bytes)} bytes", extra={
            "domain_uuid": self.config.domain_uuid,
        })
    
    async def send_text(self, text: str) -> None:
        """
        Envia texto para Gemini.
        
        Ref: BidiGenerateContentClientContent
        """
        if not self._ws or not self._setup_complete:
            raise RuntimeError("Not connected or setup not complete")
        
        message = {
            "clientContent": {
                "turns": [{
                    "role": "user",
                    "parts": [{"text": text}]
                }],
                "turnComplete": True
            }
        }
        
        await self._ws.send(json.dumps(message))
        
        logger.debug(f"Text sent to Gemini: {text[:50]}...", extra={
            "domain_uuid": self.config.domain_uuid,
        })
    
    async def interrupt(self) -> None:
        """
        Interrompe resposta atual.
        
        CORRIGIDO: Usar activityEnd (não activity_end boolean)
        Ref: BidiGenerateContentRealtimeInput.activityEnd
        """
        if not self._ws or not self._setup_complete:
            return
        
        message = {
            "realtimeInput": {
                "activityEnd": {}  # CORRIGIDO: É um objeto ActivityEnd, não boolean
            }
        }
        
        await self._ws.send(json.dumps(message))
        
        logger.debug("Interrupt signal sent to Gemini", extra={
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
        
        Ref: BidiGenerateContentToolResponse
        """
        if not self._ws or not self._setup_complete:
            raise RuntimeError("Not connected or setup not complete")
        
        message = {
            "toolResponse": {
                "functionResponses": [{
                    "id": call_id or "",
                    "name": function_name,
                    "response": result
                }]
            }
        }
        
        await self._ws.send(json.dumps(message))
        
        logger.debug(f"Function result sent to Gemini: {function_name}", extra={
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
        """Loop de recebimento de eventos do Gemini."""
        if not self._ws:
            return
        
        try:
            async for message in self._ws:
                data = json.loads(message)
                provider_event = self._parse_response(data)
                if provider_event:
                    await self._event_queue.put(provider_event)
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Gemini WebSocket closed: {e}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.SESSION_ENDED,
                data={"reason": str(e)}
            ))
        except Exception as e:
            logger.error(f"Gemini receive loop error: {e}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.ERROR,
                data={"error": str(e)}
            ))
    
    def _parse_response(self, data: Dict[str, Any]) -> Optional[ProviderEvent]:
        """
        Converte resposta Gemini para ProviderEvent.
        
        Ref: BidiGenerateContentServerMessage
        - serverContent: conteúdo do modelo (áudio, texto)
        - toolCall: chamada de função
        - inputTranscription/outputTranscription: transcrições
        """
        try:
            # ===== SERVER CONTENT (áudio/texto do modelo) =====
            if "serverContent" in data:
                content = data["serverContent"]
                
                # Verificar interrupção (barge-in)
                if content.get("interrupted"):
                    logger.debug("Gemini response interrupted", extra={
                        "domain_uuid": self.config.domain_uuid,
                    })
                    return ProviderEvent(type=ProviderEventType.INTERRUPT, data={})
                
                # Model turn com partes de áudio/texto
                if "modelTurn" in content:
                    model_turn = content["modelTurn"]
                    parts = model_turn.get("parts", [])
                    
                    for part in parts:
                        # Áudio inline (base64 PCM @ 24kHz)
                        if "inlineData" in part:
                            inline_data = part["inlineData"]
                            mime_type = inline_data.get("mimeType", "")
                            
                            if "audio" in mime_type:
                                audio_b64 = inline_data.get("data", "")
                                audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
                                
                                logger.debug(f"Gemini audio received: {len(audio_bytes)} bytes", extra={
                                    "domain_uuid": self.config.domain_uuid,
                                })
                                
                                return ProviderEvent(
                                    type=ProviderEventType.AUDIO_DELTA,
                                    data={"audio": audio_bytes}
                                )
                        
                        # Texto do modelo
                        if "text" in part and part["text"]:
                            return ProviderEvent(
                                type=ProviderEventType.TRANSCRIPT_DELTA,
                                data={"transcript": part["text"]}
                            )
                
                # Generation complete (modelo terminou de gerar)
                if content.get("generationComplete"):
                    logger.debug("Gemini generation complete", extra={
                        "domain_uuid": self.config.domain_uuid,
                    })
                    return ProviderEvent(type=ProviderEventType.AUDIO_DONE, data={})
                
                # Turn complete (modelo terminou o turno)
                if content.get("turnComplete"):
                    logger.debug("Gemini turn complete", extra={
                        "domain_uuid": self.config.domain_uuid,
                    })
                    return ProviderEvent(type=ProviderEventType.RESPONSE_DONE, data={})
            
            # ===== INPUT TRANSCRIPTION (STT do usuário) =====
            if "inputTranscription" in data:
                transcription = data["inputTranscription"]
                text = transcription.get("text", "")
                if text:
                    logger.debug(f"User transcript: {text[:50]}...", extra={
                        "domain_uuid": self.config.domain_uuid,
                    })
                    return ProviderEvent(
                        type=ProviderEventType.USER_TRANSCRIPT,
                        data={"transcript": text}
                    )
            
            # ===== OUTPUT TRANSCRIPTION (texto do modelo) =====
            if "outputTranscription" in data:
                transcription = data["outputTranscription"]
                text = transcription.get("text", "")
                if text:
                    return ProviderEvent(
                        type=ProviderEventType.TRANSCRIPT_DONE,
                        data={"transcript": text}
                    )
            
            # ===== TOOL CALL (function call) =====
            if "toolCall" in data:
                tool_call = data["toolCall"]
                function_calls = tool_call.get("functionCalls", [])
                
                if function_calls:
                    fc = function_calls[0]
                    logger.info(f"Function call: {fc.get('name')}", extra={
                        "domain_uuid": self.config.domain_uuid,
                    })
                    
                    return ProviderEvent(
                        type=ProviderEventType.FUNCTION_CALL,
                        data={
                            "function_name": fc.get("name", ""),
                            "arguments": fc.get("args", {}),
                            "call_id": fc.get("id", ""),
                        }
                    )
            
            # ===== TOOL CALL CANCELLATION =====
            if "toolCallCancellation" in data:
                logger.debug("Tool call cancelled by Gemini", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
                return None
            
            # ===== GO AWAY (server closing) =====
            if "goAway" in data:
                logger.warning("Gemini server sending goAway", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
                return ProviderEvent(
                    type=ProviderEventType.SESSION_ENDED,
                    data={"reason": "server_goaway"}
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Error parsing Gemini response: {e}", extra={
                "domain_uuid": self.config.domain_uuid,
                "response_preview": str(data)[:200],
            })
            return None
    
    async def disconnect(self) -> None:
        """Encerra conexão."""
        self._connected = False
        self._setup_complete = False
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        
        logger.info("Disconnected from Gemini Live API", extra={
            "domain_uuid": self.config.domain_uuid
        })
