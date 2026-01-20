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
from websockets.asyncio.server import ServerConnection

from .esl_client import AsyncESLClient
from ..utils.resampler import Resampler, AudioBuffer

logger = logging.getLogger(__name__)

# Configurações OpenAI Realtime (GA)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Modelo GA (General Availability) - Recomendado
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
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
        self._fs_ws: Optional[ServerConnection] = None
        self._fs_connected = asyncio.Event()
        self._fs_sender_task: Optional[asyncio.Task] = None
        self._fs_audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._fs_rawaudio_sent = False
        
        # Resamplers: FS 16kHz <-> OpenAI 24kHz
        self._resampler_in = Resampler(16000, 24000)
        self._resampler_out = Resampler(24000, 16000)
        self._fs_audio_buffer = AudioBuffer(warmup_ms=300, sample_rate=16000)
    
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
        """Conecta ao WebSocket do OpenAI Realtime (GA)."""
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured")
        
        url = f"wss://api.openai.com/v1/realtime?model={self.model}"
        
        # Headers para API GA - OpenAI-Beta não é mais necessário
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        }
        
        # Fallback para modelos preview (deprecated)
        if "preview" in self.model.lower():
            headers["OpenAI-Beta"] = "realtime=v1"
            logger.warning(f"Using preview model - consider migrating to gpt-realtime")
        
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
        
        logger.info(f"Connected to OpenAI Realtime (GA) for announcement - model={self.model}")
    
    async def _configure_session(self) -> None:
        """
        Configura a sessão OpenAI Realtime.
        
        FORMATO GA (gpt-realtime):
        - Campos DENTRO de "session" wrapper
        - Ref: https://platform.openai.com/docs/api-reference/realtime
        
        NOTA: Para anúncios curtos, usamos semantic_vad com eagerness=high
        para responder rapidamente quando o humano aceitar/recusar.
        """
        # FORMATO GA (gpt-4o-realtime-preview-2024-12-17):
        # - output_modalities ao invés de modalities
        # - audio.input/output ao invés de input_audio_format/output_audio_format
        # - VAD em audio.input.turn_detection
        config = {
            "type": "session.update",
            "session": {
                "type": "realtime",  # OBRIGATÓRIO para GA API
                "output_modalities": ["audio"],
                "instructions": self.system_prompt,
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": 24000
                        },
                        "noise_reduction": {"type": "far_field"},
                        # semantic_vad para conversa com humano
                        # eagerness=high porque queremos resposta rápida
                        "turn_detection": {
                            "type": "semantic_vad",
                            "eagerness": "high",
                            "create_response": True,
                            "interrupt_response": True,
                        },
                        "transcription": {
                            "model": "gpt-4o-transcribe",
                        },
                    },
                    "output": {
                        "format": {
                            "type": "audio/pcm"
                        },
                        "voice": self.voice,
                    },
                },
            }
        }
        
        await self._ws.send(json.dumps(config))
        
        # Aguardar confirmação session.updated
        try:
            msg = await asyncio.wait_for(self._ws.recv(), timeout=3.0)
            event = json.loads(msg)
            if event.get("type") == "session.updated":
                logger.info("Session configured for announcement (semantic_vad, eagerness=high)")
            elif event.get("type") == "error":
                error = event.get("error", {})
                logger.error(f"Session config error: {error}")
                raise RuntimeError(f"Session config failed: {error.get('message', 'unknown')}")
        except asyncio.TimeoutError:
            logger.warning("No session.updated confirmation received, continuing...")
    
    async def _start_audio_stream(self) -> None:
        """
        Inicia stream de áudio bidirecional entre FreeSWITCH e OpenAI.
        
        ⚠️ LIMITAÇÃO ATUAL:
        A implementação completa de stream bidirecional requer:
        1. WebSocket server intermediário
        2. mod_audio_stream conectado a esse server
        3. Bridge WS <-> OpenAI Realtime
        
        IMPLEMENTAÇÃO ATUAL (Simplificada):
        - OpenAI → FreeSWITCH: via _play_audio (arquivo temporário + uuid_broadcast)
        - FreeSWITCH → OpenAI: ⚠️ NÃO IMPLEMENTADO
        
        Por isso, o Realtime Announcement funciona de forma SEMI-DUPLEX:
        1. Assistente fala o anúncio (gerado via text → OpenAI → TTS)
        2. Sistema detecta resposta do humano via:
           - Patterns de texto simples se o humano falar
           - Timeout para aceitar automaticamente
        
        Para implementação FULL-DUPLEX futura:
        - Usar mod_audio_stream com WebSocket intermediário
        - Ou usar FreeSWITCH com WebRTC + SRTP
        """
        try:
            # Inicializar buffer de áudio para fallback TTS
            self._audio_buffer = bytearray()
            self._human_transcript = ""
            
            # 1) Subir WS server local para receber áudio do B-leg
            host = os.getenv("REALTIME_BLEG_STREAM_HOST", "127.0.0.1")
            self._audio_ws_server = await websockets.serve(
                self._handle_fs_ws,
                host,
                0,
                max_size=None,
            )
            if not self._audio_ws_server.sockets:
                raise RuntimeError("Failed to allocate port for B-leg audio WS")
            
            self._audio_ws_port = self._audio_ws_server.sockets[0].getsockname()[1]
            ws_url = f"ws://{host}:{self._audio_ws_port}/bleg/{self.b_leg_uuid}"
            
            # 2) Iniciar mod_audio_stream no B-leg
            cmd = f"uuid_audio_stream {self.b_leg_uuid} start {ws_url} mono 16k"
            response = await self.esl.execute_api(cmd)
            logger.info(
                "B-leg audio stream started",
                extra={
                    "b_leg_uuid": self.b_leg_uuid,
                    "ws_url": ws_url,
                    "esl_response": response,
                },
            )
            
            # 3) Aguardar conexão do FreeSWITCH
            try:
                await asyncio.wait_for(self._fs_connected.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "B-leg audio stream did not connect, fallback to semi-duplex",
                    extra={"b_leg_uuid": self.b_leg_uuid},
                )
                return
            
            logger.info(
                "B-leg audio stream connected (FULL-DUPLEX)",
                extra={"b_leg_uuid": self.b_leg_uuid},
            )
            
        except Exception as e:
            logger.error(f"Failed to initialize audio stream: {e}")
    
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
        
        # Áudio de resposta do assistente - enviar para FreeSWITCH (B-leg/humano)
        if etype in ("response.audio.delta", "response.output_audio.delta"):
            audio_b64 = event.get("delta", "")
            if audio_b64:
                audio_bytes = base64.b64decode(audio_b64)
                if self._fs_ws:
                    await self._enqueue_audio_to_freeswitch(audio_bytes)
                else:
                    await self._play_audio(audio_bytes)
        
        # Transcrição do HUMANO (input) - IMPORTANTE: é aqui que detectamos aceite/recusa
        elif etype == "conversation.item.input_audio_transcription.completed":
            human_transcript = event.get("transcript", "")
            logger.info(f"Human said: {human_transcript}")
            
            # Atualizar transcript do humano
            if not hasattr(self, '_human_transcript'):
                self._human_transcript = ""
            self._human_transcript += human_transcript + " "
            
            # Verificar decisão baseada no que o HUMANO disse
            self._check_human_decision(human_transcript)
        
        # Transcrição do assistente (para log)
        elif etype in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            delta = event.get("delta", "")
            self._transcript += delta
        
        # Resposta completa do assistente
        elif etype == "response.done":
            response = event.get("response", {})
            status = response.get("status", "completed")
            logger.debug(f"Response complete (status={status}), transcript: {self._transcript[-100:]}")
            
            # Verificar se assistente decidiu
            self._check_decision()
        
        # Erro
        elif etype == "error":
            error = event.get("error", {})
            error_code = error.get("code", "unknown")
            # Ignorar erros não-críticos
            if error_code not in ("response_cancel_not_active",):
                logger.error(f"OpenAI error: {error}")
    
    def _check_human_decision(self, human_text: str) -> None:
        """
        Verifica decisão baseada no que o HUMANO disse.
        
        Detecta frases de:
        - Aceite: "pode passar", "pode transferir", "ok", "sim"
        - Recusa: "não posso", "estou ocupado", "depois", "recuso"
        
        Args:
            human_text: Transcrição do que o humano falou
        """
        text_lower = human_text.lower().strip()
        
        # Palavras/frases de ACEITE
        accept_patterns = [
            "pode passar", "pode transferir", "pode conectar",
            "ok", "tá bom", "tá bem", "beleza",
            "sim", "claro", "certo", "pode",
            "manda", "passa aí", "conecta",
        ]
        
        # Palavras/frases de RECUSA
        reject_patterns = [
            "não posso", "não dá", "não",
            "estou ocupado", "ocupado", "em reunião",
            "depois", "mais tarde", "agora não",
            "recuso", "não quero", "não tenho tempo",
        ]
        
        # Verificar aceite
        for pattern in accept_patterns:
            if pattern in text_lower:
                self._accepted = True
                logger.info(f"Human ACCEPTED: matched '{pattern}' in '{text_lower[:50]}'")
                return
        
        # Verificar recusa
        for pattern in reject_patterns:
            if pattern in text_lower:
                self._rejected = True
                self._rejection_message = human_text
                logger.info(f"Human REJECTED: matched '{pattern}' in '{text_lower[:50]}'")
                return
    
    def _check_decision(self) -> None:
        """
        Verifica se a transcrição do ASSISTENTE contém decisão.
        
        O assistente deve responder com "ACEITO" ou "RECUSADO: [motivo]"
        após interpretar a resposta do humano.
        """
        text = self._transcript.upper()
        
        if "ACEITO" in text:
            self._accepted = True
            logger.info("Decision from assistant: ACCEPTED")
        
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
        
        OpenAI Realtime retorna PCM16 @ 24kHz.
        FreeSWITCH precisa de WAV com header ou conversão.
        
        Estratégia: Acumular chunks e tocar via uuid_broadcast.
        """
        # Acumular áudio no buffer
        if not hasattr(self, '_audio_buffer'):
            self._audio_buffer = bytearray()
        
        self._audio_buffer.extend(audio_bytes)
        
        # Tocar quando tiver áudio suficiente (~500ms = 24000 samples @ 24kHz)
        MIN_BUFFER_SIZE = 24000  # 0.5s de áudio @ 24kHz PCM16
        
        if len(self._audio_buffer) >= MIN_BUFFER_SIZE:
            await self._flush_audio_buffer()
    
    async def _flush_audio_buffer(self) -> None:
        """
        Toca áudio acumulado no buffer.
        
        Usa asyncio subprocess para não bloquear o event loop.
        """
        if not hasattr(self, '_audio_buffer') or len(self._audio_buffer) == 0:
            return
        
        import tempfile
        from pathlib import Path
        
        try:
            # Salvar PCM raw em arquivo temporário
            fd, pcm_path = tempfile.mkstemp(suffix=".raw")
            with os.fdopen(fd, "wb") as f:
                f.write(self._audio_buffer)
            
            # Converter PCM 24kHz para WAV 16kHz (FreeSWITCH padrão)
            wav_path = pcm_path.replace(".raw", ".wav")
            
            # ffmpeg: PCM 24kHz mono -> WAV 16kHz mono
            # IMPORTANTE: Usar asyncio subprocess para não bloquear
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-f", "s16le",           # Input: PCM 16-bit signed
                "-ar", "24000",          # Input: 24kHz (OpenAI output)
                "-ac", "1",              # Input: mono
                "-i", pcm_path,
                "-ar", "16000",          # Output: 16kHz (FreeSWITCH)
                "-ac", "1",              # Output: mono
                wav_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                logger.warning("ffmpeg timeout, killing process")
                self._audio_buffer = bytearray()
                return
            
            if process.returncode == 0 and Path(wav_path).exists():
                # Tocar via uuid_broadcast
                await self.esl.execute_api(
                    f"uuid_broadcast {self.b_leg_uuid} {wav_path} aleg"
                )
                logger.debug(f"Played {len(self._audio_buffer)} bytes to B-leg")
            else:
                error_msg = stderr.decode()[:200] if stderr else "unknown"
                logger.warning(f"ffmpeg conversion failed: {error_msg}")
            
            # Limpar buffer
            self._audio_buffer = bytearray()
            
            # Cleanup temp files
            Path(pcm_path).unlink(missing_ok=True)
            # Não deletar wav_path imediatamente, FreeSWITCH precisa acessar
            
        except Exception as e:
            logger.error(f"Error flushing audio buffer: {e}")
            self._audio_buffer = bytearray()
    
    async def _cleanup(self) -> None:
        """Limpa recursos."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        
        if self._fs_sender_task:
            self._fs_sender_task.cancel()
            self._fs_sender_task = None
        
        if self._fs_ws:
            try:
                await self._fs_ws.close()
            except Exception:
                pass
            self._fs_ws = None
        
        if self._audio_ws_server:
            self._audio_ws_server.close()
            try:
                await self._audio_ws_server.wait_closed()
            except Exception:
                pass
            self._audio_ws_server = None
        
        # Opcional: parar stream no B-leg (pode derrubar canal em alguns ambientes)
        if os.getenv("REALTIME_BLEG_STREAM_STOP", "false").lower() in ("1", "true", "yes"):
            try:
                await self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop")
            except Exception:
                pass
        
        logger.debug("Realtime announcement session cleaned up")

    async def _handle_fs_ws(self, websocket: ServerConnection) -> None:
        """Recebe áudio do FreeSWITCH (B-leg) e envia ao OpenAI."""
        if self._fs_ws:
            await websocket.close(1008, "Already connected")
            return
        
        self._fs_ws = websocket
        self._fs_connected.set()
        self._fs_rawaudio_sent = False
        self._fs_sender_task = asyncio.create_task(self._fs_sender_loop())
        
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    await self._handle_fs_audio(message)
                elif isinstance(message, str):
                    try:
                        data = json.loads(message)
                        if data.get("type") == "metadata":
                            logger.debug("B-leg metadata received", extra={"b_leg_uuid": self.b_leg_uuid})
                    except Exception:
                        pass
        except Exception as e:
            logger.info(f"B-leg ws closed: {e}")
        finally:
            if self._fs_sender_task:
                self._fs_sender_task.cancel()
                self._fs_sender_task = None

    async def _handle_fs_audio(self, audio_bytes: bytes) -> None:
        """Resample 16kHz -> 24kHz e envia ao OpenAI."""
        if not audio_bytes or not self._ws:
            return
        try:
            audio_24k = self._resampler_in.process(audio_bytes)
        except Exception:
            audio_24k = audio_bytes
        
        payload = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(audio_24k).decode("utf-8"),
        }
        try:
            await self._ws.send(json.dumps(payload))
        except Exception:
            pass

    async def _enqueue_audio_to_freeswitch(self, audio_bytes: bytes) -> None:
        """Enfileira áudio do OpenAI para o FreeSWITCH (24kHz -> 16kHz)."""
        if not audio_bytes:
            return
        try:
            audio_16k = self._resampler_out.process(audio_bytes)
        except Exception:
            audio_16k = audio_bytes
        
        audio_16k = self._fs_audio_buffer.add(audio_16k)
        if not audio_16k:
            return
        
        # Quebrar em chunks de 20ms (640 bytes @16kHz PCM16)
        chunk_size = 640
        for i in range(0, len(audio_16k), chunk_size):
            chunk = audio_16k[i:i + chunk_size]
            try:
                await self._fs_audio_queue.put(chunk)
            except Exception:
                break

    async def _fs_sender_loop(self) -> None:
        """Envia áudio para o FreeSWITCH via rawAudio + binário."""
        if not self._fs_ws:
            return
        
        try:
            if not self._fs_rawaudio_sent:
                header = {"type": "rawAudio", "data": {"sampleRate": 16000}}
                await self._fs_ws.send(json.dumps(header))
                self._fs_rawaudio_sent = True
            
            while self._running and self._fs_ws:
                chunk = await self._fs_audio_queue.get()
                await self._fs_ws.send(chunk)
        except Exception:
            pass
