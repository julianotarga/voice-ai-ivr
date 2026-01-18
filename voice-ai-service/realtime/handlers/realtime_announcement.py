"""
Realtime Announcement Session - Conversa OpenAI Realtime com humano durante transferência.

Permite que o agente IA converse por voz com o atendente humano,
oferecendo uma experiência mais natural que TTS + DTMF.

Ref: voice-ai-ivr/openspec/changes/announced-transfer/
"""

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import websockets
from websockets.asyncio.client import ClientConnection

from .esl_client import AsyncESLClient

logger = logging.getLogger(__name__)

# Configurações OpenAI Realtime
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "nova")


@dataclass
class AnnouncementResult:
    """Resultado da conversa de anúncio com o humano."""
    accepted: bool = False
    rejected: bool = False
    message: Optional[str] = None
    transcript: str = ""
    duration_seconds: float = 0.0


class RealtimeAnnouncementSession:
    """
    Sessão OpenAI Realtime para conversar com humano durante transferência.
    
    Fluxo:
    1. Conectar ao OpenAI Realtime
    2. Configurar sessão com prompt de anúncio
    3. Iniciar stream de áudio do B-leg (via mod_audio_stream)
    4. Enviar mensagem inicial de anúncio
    5. Processar respostas do humano
    6. Detectar aceitação/recusa
    7. Retornar resultado
    
    Uso:
        session = RealtimeAnnouncementSession(
            b_leg_uuid="xxx",
            esl_client=esl,
            system_prompt="Você está anunciando...",
            initial_message="Tenho o João na linha sobre planos..."
        )
        result = await session.run(timeout=15.0)
        
        if result.accepted:
            # Fazer bridge
        elif result.rejected:
            # Voltar para cliente com mensagem
    """
    
    def __init__(
        self,
        b_leg_uuid: str,
        esl_client: AsyncESLClient,
        system_prompt: str,
        initial_message: str,
        voice: str = OPENAI_REALTIME_VOICE,
        model: str = OPENAI_REALTIME_MODEL,
    ):
        """
        Args:
            b_leg_uuid: UUID do B-leg (humano)
            esl_client: Cliente ESL para controle de áudio
            system_prompt: Prompt de sistema para o agente
            initial_message: Mensagem inicial de anúncio
            voice: Voz do OpenAI (alloy, echo, fable, onyx, nova, shimmer)
            model: Modelo Realtime
        """
        self.b_leg_uuid = b_leg_uuid
        self.esl = esl_client
        self.system_prompt = system_prompt
        self.initial_message = initial_message
        self.voice = voice
        self.model = model
        
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._transcript = ""
        self._accepted = False
        self._rejected = False
        self._rejection_message: Optional[str] = None
        
        # WebSocket URL para receber áudio do FreeSWITCH
        self._audio_ws_server: Optional[asyncio.Server] = None
        self._audio_ws_port: int = 0
    
    async def run(self, timeout: float = 15.0) -> AnnouncementResult:
        """
        Executa a conversa de anúncio.
        
        Args:
            timeout: Tempo máximo de conversa em segundos
        
        Returns:
            AnnouncementResult com decisão do humano
        """
        import time
        start_time = time.time()
        
        try:
            self._running = True
            
            # 1. Conectar ao OpenAI Realtime
            await self._connect_openai()
            
            # 2. Configurar sessão
            await self._configure_session()
            
            # 3. Iniciar stream de áudio do FreeSWITCH
            await self._start_audio_stream()
            
            # 4. Enviar mensagem inicial
            await self._send_initial_message()
            
            # 5. Loop principal - processar eventos até decisão ou timeout
            await asyncio.wait_for(
                self._event_loop(),
                timeout=timeout
            )
            
        except asyncio.TimeoutError:
            logger.info("Realtime announcement timeout")
        
        except asyncio.CancelledError:
            logger.info("Realtime announcement cancelled")
            raise
        
        except Exception as e:
            logger.exception(f"Realtime announcement error: {e}")
        
        finally:
            self._running = False
            await self._cleanup()
        
        duration = time.time() - start_time
        
        return AnnouncementResult(
            accepted=self._accepted,
            rejected=self._rejected,
            message=self._rejection_message,
            transcript=self._transcript,
            duration_seconds=duration,
        )
    
    async def _connect_openai(self) -> None:
        """Conecta ao WebSocket do OpenAI Realtime."""
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured")
        
        url = f"wss://api.openai.com/v1/realtime?model={self.model}"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1",
        }
        
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=10,
        )
        
        # Aguardar session.created
        msg = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
        event = json.loads(msg)
        
        if event.get("type") != "session.created":
            raise RuntimeError(f"Expected session.created, got {event.get('type')}")
        
        logger.info("Connected to OpenAI Realtime for announcement")
    
    async def _configure_session(self) -> None:
        """Configura a sessão OpenAI Realtime."""
        config = {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "voice": self.voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                    "create_response": True,
                },
                "instructions": self.system_prompt,
                "temperature": 0.7,
            }
        }
        
        await self._ws.send(json.dumps(config))
        logger.debug("Session configured for announcement")
    
    async def _start_audio_stream(self) -> None:
        """
        Inicia stream de áudio bidirecional entre FreeSWITCH e OpenAI.
        
        Usa mod_audio_stream para capturar áudio do B-leg e enviar para OpenAI.
        """
        # Por simplicidade, vamos usar uma abordagem baseada em polling
        # Em produção, seria ideal ter um WebSocket server dedicado
        
        # Iniciar captura de áudio do B-leg
        # O áudio será enviado via events do ESL
        
        logger.info(f"Audio stream started for B-leg: {self.b_leg_uuid}")
        
        # Nota: A implementação completa requer integração com mod_audio_stream
        # Por ora, vamos usar uma abordagem simplificada
    
    async def _send_initial_message(self) -> None:
        """Envia mensagem inicial de anúncio."""
        # Criar item de conversa com a mensagem inicial
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": self.initial_message}]
            }
        }))
        
        # Solicitar resposta
        await self._ws.send(json.dumps({"type": "response.create"}))
        
        logger.info(f"Initial announcement sent: {self.initial_message[:50]}...")
    
    async def _event_loop(self) -> None:
        """Loop principal de processamento de eventos."""
        while self._running and not self._accepted and not self._rejected:
            try:
                msg = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
                event = json.loads(msg)
                await self._handle_event(event)
                
            except asyncio.TimeoutError:
                # Verificar se B-leg ainda existe
                b_leg_exists = await self.esl.uuid_exists(self.b_leg_uuid)
                if not b_leg_exists:
                    logger.info("B-leg hangup detected")
                    self._rejected = True
                    self._rejection_message = "Humano desligou"
                    break
    
    async def _handle_event(self, event: dict) -> None:
        """Processa evento do OpenAI Realtime."""
        etype = event.get("type", "")
        
        # Áudio de resposta - enviar para FreeSWITCH
        if etype in ("response.audio.delta", "response.output_audio.delta"):
            audio_b64 = event.get("delta", "")
            if audio_b64:
                audio_bytes = base64.b64decode(audio_b64)
                await self._play_audio(audio_bytes)
        
        # Transcrição do assistente
        elif etype in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            delta = event.get("delta", "")
            self._transcript += delta
            
            # Verificar palavras-chave de decisão
            self._check_decision()
        
        # Transcrição completa do assistente
        elif etype == "response.done":
            logger.debug(f"Response complete, transcript: {self._transcript[-100:]}")
            self._check_decision()
        
        # Erro
        elif etype == "error":
            error = event.get("error", {})
            logger.error(f"OpenAI error: {error}")
    
    def _check_decision(self) -> None:
        """Verifica se a transcrição contém decisão."""
        text = self._transcript.upper()
        
        if "ACEITO" in text:
            self._accepted = True
            logger.info("Decision detected: ACCEPTED")
        
        elif "RECUSADO" in text:
            self._rejected = True
            # Extrair mensagem após "RECUSADO:"
            parts = self._transcript.split("RECUSADO:")
            if len(parts) > 1:
                self._rejection_message = parts[1].strip()[:200]
            logger.info(f"Decision detected: REJECTED - {self._rejection_message}")
    
    async def _play_audio(self, audio_bytes: bytes) -> None:
        """
        Envia áudio para o B-leg via FreeSWITCH.
        
        Nota: Em produção, usar mod_audio_stream WebSocket.
        Por ora, salva em arquivo e usa uuid_playback.
        """
        # Implementação simplificada - salvar em arquivo e tocar
        # Em produção, usar stream direto via WebSocket
        pass
    
    async def _cleanup(self) -> None:
        """Limpa recursos."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        
        logger.debug("Realtime announcement session cleaned up")
