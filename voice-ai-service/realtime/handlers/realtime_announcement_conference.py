"""
Realtime Announcement para Conferência - OpenAI Realtime com function calls.

Versão adaptada para trabalhar com mod_conference, usando function calls
para detectar aceitação/recusa do atendente.

Ref: voice-ai-ivr/docs/announced-transfer-conference.md
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
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
OPENAI_REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "marin")


@dataclass
class ConferenceAnnouncementResult:
    """Resultado da conversa de anúncio com o atendente."""
    accepted: bool = False
    rejected: bool = False
    message: Optional[str] = None
    transcript: str = ""
    duration_seconds: float = 0.0


# Tools/Functions para OpenAI Realtime
TRANSFER_TOOLS = [
    {
        "type": "function",
        "name": "accept_transfer",
        "description": "Chamado quando o atendente ACEITA a transferência. Use quando ouvir 'sim', 'aceito', 'pode conectar', 'pode passar', 'ok', 'tá bom'.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "function",
        "name": "reject_transfer",
        "description": "Chamado quando o atendente RECUSA a transferência. Use quando ouvir 'não', 'não posso', 'ocupado', 'recuso', 'depois', 'agora não'.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Motivo opcional da recusa"
                }
            },
            "required": []
        }
    }
]


class ConferenceAnnouncementSession:
    """
    Sessão OpenAI Realtime para anunciar transferência ao atendente em conferência.
    
    Diferente da versão anterior (realtime_announcement.py):
    - Usa function calls (accept_transfer/reject_transfer) para decisão clara
    - Otimizado para conferência (B-leg já está conectado)
    - Mais robusto e confiável
    
    Fluxo:
    1. Conectar ao OpenAI Realtime
    2. Configurar sessão com tools (function calls)
    3. Iniciar stream de áudio do B-leg
    4. Enviar mensagem inicial de anúncio
    5. Processar eventos e aguardar function call
    6. Retornar resultado
    """
    
    def __init__(
        self,
        esl_client: AsyncESLClient,
        b_leg_uuid: str,
        system_prompt: str,
        initial_message: str,
        voice: str = OPENAI_REALTIME_VOICE,
        model: str = OPENAI_REALTIME_MODEL,
    ):
        """
        Args:
            esl_client: Cliente ESL para controle de áudio
            b_leg_uuid: UUID do B-leg (atendente na conferência)
            system_prompt: Prompt de sistema para o agente
            initial_message: Mensagem inicial de anúncio
            voice: Voz do OpenAI
            model: Modelo Realtime
        """
        self.esl = esl_client
        self.b_leg_uuid = b_leg_uuid
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
        
        # Evento para sinalizar decisão via function call
        self._decision_event = asyncio.Event()
        
        # Lock para proteger contra race condition na decisão
        self._decision_lock = asyncio.Lock()
        
        # WebSocket para áudio FreeSWITCH <-> OpenAI
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
        
        # Buffer de áudio para fallback TTS
        self._audio_buffer = bytearray()
    
    async def run(self, timeout: float = 15.0) -> ConferenceAnnouncementResult:
        """
        Executa a conversa de anúncio.
        
        Args:
            timeout: Tempo máximo de conversa em segundos
        
        Returns:
            ConferenceAnnouncementResult com decisão do atendente
        """
        import time
        start_time = time.time()
        
        logger.info("=" * 60)
        logger.info("🎤 CONFERENCE ANNOUNCEMENT STARTING")
        logger.info(f"   B-leg UUID: {self.b_leg_uuid}")
        logger.info(f"   Model: {self.model}")
        logger.info(f"   Voice: {self.voice}")
        logger.info(f"   Timeout: {timeout}s")
        logger.info("=" * 60)
        
        try:
            self._running = True
            
            # 1. Conectar ao OpenAI Realtime
            logger.info("🔌 Step 1: Connecting to OpenAI Realtime...")
            await self._connect_openai()
            logger.info("✅ Step 1: Connected")
            
            # 2. Configurar sessão COM function calls
            logger.info("⚙️ Step 2: Configuring session with tools...")
            await self._configure_session_with_tools()
            logger.info("✅ Step 2: Session configured")
            
            # 3. Iniciar stream de áudio
            logger.info("🎤 Step 3: Starting audio stream...")
            await self._start_audio_stream()
            logger.info("✅ Step 3: Audio stream ready")
            
            # 4. Enviar mensagem inicial
            logger.info("💬 Step 4: Sending initial message...")
            await self._send_initial_message()
            logger.info("✅ Step 4: Initial message sent")
            
            # 5. Loop principal - processar eventos até decisão ou timeout
            logger.info("▶️ Step 5: Waiting for decision...")
            
            # Usar wait com timeout em vez de wait_for no loop inteiro
            try:
                await asyncio.wait_for(
                    self._wait_for_decision(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.info("⏱️ Timeout reached without decision")
            
        except asyncio.CancelledError:
            logger.info("Announcement cancelled")
            raise
        
        except Exception as e:
            logger.exception(f"Announcement error: {e}")
        
        finally:
            self._running = False
            await self._cleanup()
        
        duration = time.time() - start_time
        
        return ConferenceAnnouncementResult(
            accepted=self._accepted,
            rejected=self._rejected,
            message=self._rejection_message,
            transcript=self._transcript,
            duration_seconds=duration,
        )
    
    async def _wait_for_decision(self) -> None:
        """Aguarda decisão via function call ou patterns de texto."""
        while self._running and not self._accepted and not self._rejected:
            try:
                # Verificar se WebSocket ainda conectado
                if not self._ws or self._ws.closed:
                    logger.warning("OpenAI WebSocket closed")
                    break
                
                msg = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
                event = json.loads(msg)
                await self._handle_event(event)
                
                # Verificar se decision_event foi setado
                if self._decision_event.is_set():
                    break
                
            except asyncio.TimeoutError:
                # Verificar se B-leg ainda existe
                try:
                    b_exists = await asyncio.wait_for(
                        self.esl.uuid_exists(self.b_leg_uuid),
                        timeout=1.0
                    )
                    if not b_exists:
                        logger.info("B-leg hangup detected")
                        self._rejected = True
                        self._rejection_message = "Atendente desligou"
                        break
                except Exception:
                    pass
                
                # TAMBÉM verificar se A-leg (cliente) ainda existe
                # Se o cliente desligou, não faz sentido continuar o anúncio
                try:
                    # Obter A-leg UUID do transfer manager (via ESL client context)
                    # Por enquanto, apenas verificar se B-leg desligou é suficiente
                    # pois o A-leg está mudo na conferência
                    pass
                except Exception:
                    pass
    
    async def _connect_openai(self) -> None:
        """Conecta ao WebSocket do OpenAI Realtime."""
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured")
        
        url = f"wss://api.openai.com/v1/realtime?model={self.model}"
        
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        }
        
        # Preview models precisam do header beta
        if "preview" in self.model.lower():
            headers["OpenAI-Beta"] = "realtime=v1"
        
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
        
        logger.debug(f"Connected to OpenAI Realtime")
    
    async def _configure_session_with_tools(self) -> None:
        """
        Configura a sessão OpenAI Realtime COM function calls.
        
        As tools accept_transfer e reject_transfer permitem decisão clara.
        """
        config = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": self.system_prompt,
                
                # Configuração de áudio
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": 24000
                        },
                        "noise_reduction": {"type": "far_field"},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 500
                        },
                        "transcription": {
                            "model": "gpt-4o-transcribe"
                        },
                    },
                    "output": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": 24000
                        },
                        "voice": self.voice,
                    },
                },
                
                # TOOLS para decisão
                "tools": TRANSFER_TOOLS,
                "tool_choice": "auto",
            }
        }
        
        logger.debug(f"Session config with tools: {json.dumps(config)[:500]}")
        
        await self._ws.send(json.dumps(config))
        
        # Aguardar confirmação
        try:
            msg = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            event = json.loads(msg)
            if event.get("type") == "session.updated":
                logger.info("✅ Session configured with function calls")
            elif event.get("type") == "error":
                error = event.get("error", {})
                logger.error(f"❌ Session config error: {error}")
            else:
                logger.debug(f"Unexpected event: {event.get('type')}")
        except asyncio.TimeoutError:
            logger.warning("No session.updated confirmation (timeout)")
    
    async def _start_audio_stream(self) -> None:
        """Inicia stream de áudio bidirecional."""
        try:
            bind_host = os.getenv("REALTIME_BLEG_STREAM_BIND", "0.0.0.0")
            connect_host = os.getenv("REALTIME_BLEG_STREAM_HOST", "127.0.0.1")
            bleg_port_str = os.getenv("REALTIME_BLEG_STREAM_PORT", "")
            base_port = int(bleg_port_str) if bleg_port_str else 0
            
            # Se porta configurada, tentar um range para suportar sessões simultâneas
            # Se porta 0, o OS escolhe uma porta livre
            if base_port == 0:
                logger.debug("Using random port for audio WS")
                ports_to_try = [0]  # OS escolhe
            else:
                # Tentar porta base e próximas 10 portas
                ports_to_try = list(range(base_port, base_port + 10))
            
            for port in ports_to_try:
                try:
                    logger.debug(f"Trying audio WS on {bind_host}:{port or 'random'}...")
                    self._audio_ws_server = await websockets.serve(
                        self._handle_fs_ws,
                        bind_host,
                        port,
                        max_size=None,
                    )
                    break  # Sucesso
                except OSError as e:
                    if port == ports_to_try[-1]:
                        # Última tentativa falhou
                        logger.warning(f"⚠️ Cannot bind any port in range: {e}")
                        self._audio_ws_server = None
                        return
                    else:
                        logger.debug(f"Port {port} in use, trying next...")
            
            if not self._audio_ws_server or not self._audio_ws_server.sockets:
                logger.warning("⚠️ Failed to start WS server")
                return
            
            self._audio_ws_port = self._audio_ws_server.sockets[0].getsockname()[1]
            ws_url = f"ws://{connect_host}:{self._audio_ws_port}/bleg/{self.b_leg_uuid}"
            
            logger.info(f"🔊 Audio WS ready: {ws_url}")
            
            # Iniciar mod_audio_stream no B-leg
            cmd = f"uuid_audio_stream {self.b_leg_uuid} start {ws_url} mono 16k"
            
            try:
                response = await asyncio.wait_for(
                    self.esl.execute_api(cmd),
                    timeout=3.0
                )
                logger.info(f"🔊 Audio stream started: {response[:100] if response else 'OK'}")
            except asyncio.TimeoutError:
                logger.error(f"❌ ESL command timeout: {cmd}")
            except Exception as e:
                logger.error(f"❌ ESL command failed: {e}")
            
            # Aguardar conexão do FreeSWITCH
            try:
                await asyncio.wait_for(self._fs_connected.wait(), timeout=5.0)
                logger.info("✅ Audio stream connected (FULL-DUPLEX)")
            except asyncio.TimeoutError:
                logger.warning("⚠️ Audio stream did not connect - TTS fallback mode")
            
        except Exception as e:
            logger.error(f"Audio stream init failed: {e}")
    
    async def _send_initial_message(self) -> None:
        """Envia mensagem inicial de anúncio."""
        if not self._ws:
            return
        
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": self.initial_message}]
            }
        }))
        
        await self._ws.send(json.dumps({"type": "response.create"}))
        
        logger.info(f"Initial message sent: {self.initial_message[:50]}...")
    
    async def _handle_event(self, event: dict) -> None:
        """Processa evento do OpenAI Realtime."""
        etype = event.get("type", "")
        
        # Áudio de resposta - enviar para FreeSWITCH
        if etype in ("response.audio.delta", "response.output_audio.delta"):
            audio_b64 = event.get("delta", "")
            if audio_b64:
                audio_bytes = base64.b64decode(audio_b64)
                if self._fs_ws:
                    await self._enqueue_audio_to_freeswitch(audio_bytes)
                else:
                    await self._play_audio_fallback(audio_bytes)
        
        # FUNCTION CALL - Decisão do atendente
        elif etype == "response.function_call_arguments.done":
            await self._handle_function_call(event)
        
        # Transcrição do HUMANO (atendente)
        elif etype == "conversation.item.input_audio_transcription.completed":
            human_transcript = event.get("transcript", "")
            logger.info(f"Attendant said: {human_transcript}")
            # Usar lock para proteger contra race condition com function calls
            await self._check_human_decision_safe(human_transcript)
        
        # Transcrição do assistente
        elif etype in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            delta = event.get("delta", "")
            self._transcript += delta
        
        # Resposta completa
        elif etype == "response.done":
            await self._flush_audio_buffer(force=True)
            self._check_assistant_decision()
        
        # Erro
        elif etype == "error":
            error = event.get("error", {})
            error_code = error.get("code", "unknown")
            if error_code not in ("response_cancel_not_active",):
                logger.error(f"OpenAI error: {error}")
    
    async def _handle_function_call(self, event: dict) -> None:
        """
        Processa function call do OpenAI.
        
        accept_transfer() = aceita
        reject_transfer() = recusa
        
        THREAD-SAFE: Usa lock para evitar race condition com pattern matching.
        """
        async with self._decision_lock:
            # Se já temos uma decisão, ignorar novas function calls
            if self._accepted or self._rejected:
                logger.debug(f"Decision already made, ignoring function call")
                return
            
            # Extrair informações da function call
            # O formato pode variar - tentar diferentes campos
            function_name = event.get("name") or event.get("function_name")
            call_id = event.get("call_id") or event.get("id")
            arguments = event.get("arguments", "{}")
            
            if not function_name:
                # Tentar extrair do item
                item = event.get("item", {})
                function_name = item.get("name")
                call_id = item.get("call_id") or item.get("id")
                arguments = item.get("arguments", "{}")
            
            logger.info(f"🔧 Function call received: {function_name}")
            
            # Processar decisão
            if function_name == "accept_transfer":
                self._accepted = True
                logger.info("✅ Function call: ACCEPTED")
                
            elif function_name == "reject_transfer":
                self._rejected = True
                # Extrair motivo se fornecido
                try:
                    args = json.loads(arguments) if isinstance(arguments, str) else arguments
                    self._rejection_message = args.get("reason", "Recusado pelo atendente")
                except Exception:
                    self._rejection_message = "Recusado pelo atendente"
                logger.info(f"❌ Function call: REJECTED - {self._rejection_message}")
            
            # Enviar output da function (obrigatório)
            if call_id:
                await self._send_function_output(call_id, {"status": "ok"})
            
            # Sinalizar que decisão foi tomada
            self._decision_event.set()
    
    async def _send_function_output(self, call_id: str, output: dict) -> None:
        """Envia output da function call."""
        if not self._ws:
            return
        
        try:
            await self._ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output)
                }
            }))
        except Exception as e:
            logger.debug(f"Could not send function output: {e}")
    
    async def _check_human_decision_safe(self, human_text: str) -> None:
        """
        Verifica decisão baseada no que o ATENDENTE disse.
        
        THREAD-SAFE: Usa lock para evitar race condition com function calls.
        
        Backup para quando function calls não são usadas.
        """
        async with self._decision_lock:
            # Guard clause: Se já temos decisão, não processar
            if self._accepted or self._rejected:
                logger.debug("Decision already made, ignoring human transcript")
                return
            
            text_lower = human_text.lower().strip()
            
            # Patterns de ACEITE - ordenados por especificidade (mais específico primeiro)
            # Evitar patterns muito curtos que podem dar falso positivo
            accept_patterns = [
                "pode passar", "pode transferir", "pode conectar",
                "tá bom", "tá bem", "beleza",
                "aceito", "claro", "certo",
                "manda", "passa aí", "conecta",
            ]
            
            # Patterns genéricos que precisam ser palavra isolada ou início de frase
            accept_generic = ["sim", "ok", "pode"]
            
            # Patterns de RECUSA - ordenados por especificidade
            # BUG FIX: Removido "não" isolado pois é muito genérico
            # "não" deve estar acompanhado de contexto
            reject_patterns = [
                "não posso", "não dá", "não quero", "não tenho tempo",
                "estou ocupado", "ocupado", "em reunião",
                "depois", "mais tarde", "agora não",
                "recuso", "não aceito", "não vou atender",
            ]
            
            # Verificar patterns específicos de aceite
            for pattern in accept_patterns:
                if pattern in text_lower:
                    self._accepted = True
                    logger.info(f"Human ACCEPTED: matched '{pattern}'")
                    self._decision_event.set()
                    return
            
            # Verificar patterns genéricos de aceite (palavra isolada ou início)
            words = text_lower.split()
            if words:
                first_word = words[0].rstrip(".,!?")
                if first_word in accept_generic or (len(words) == 1 and first_word in accept_generic):
                    self._accepted = True
                    logger.info(f"Human ACCEPTED: generic match '{first_word}'")
                    self._decision_event.set()
                    return
            
            # Verificar patterns de recusa
            for pattern in reject_patterns:
                if pattern in text_lower:
                    self._rejected = True
                    self._rejection_message = human_text
                    logger.info(f"Human REJECTED: matched '{pattern}'")
                    self._decision_event.set()
                    return
    
    def _check_assistant_decision(self) -> None:
        """Verifica decisão na transcrição do assistente (fallback)."""
        text = self._transcript.upper()
        
        if "ACEITO" in text and not self._rejected:
            self._accepted = True
            logger.info("Assistant indicated: ACCEPTED")
            self._decision_event.set()
        
        elif "RECUSADO" in text and not self._accepted:
            self._rejected = True
            parts = self._transcript.split("RECUSADO:")
            if len(parts) > 1:
                self._rejection_message = parts[1].strip()[:200]
            logger.info(f"Assistant indicated: REJECTED")
            self._decision_event.set()
    
    async def _play_audio_fallback(self, audio_bytes: bytes) -> None:
        """Acumula áudio para playback via fallback TTS."""
        self._audio_buffer.extend(audio_bytes)
        
        # Tocar quando tiver ~250ms de áudio
        MIN_BUFFER_SIZE = 12000
        
        if len(self._audio_buffer) >= MIN_BUFFER_SIZE:
            await self._flush_audio_buffer()
    
    async def _flush_audio_buffer(self, force: bool = False) -> None:
        """Toca áudio acumulado no buffer via FreeSWITCH."""
        if len(self._audio_buffer) == 0:
            return
        
        buffer_size = len(self._audio_buffer)
        
        if not force and buffer_size < 4800:
            return
        
        import tempfile
        from pathlib import Path
        
        try:
            # Salvar PCM
            fd, pcm_path = tempfile.mkstemp(suffix=".raw", prefix="conf_audio_")
            with os.fdopen(fd, "wb") as f:
                f.write(self._audio_buffer)
            
            self._audio_buffer = bytearray()
            
            # Converter para WAV 8kHz
            wav_path = pcm_path.replace(".raw", ".wav")
            
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-f", "s16le", "-ar", "24000", "-ac", "1",
                "-i", pcm_path,
                "-ar", "8000", "-ac", "1",
                wav_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                await asyncio.wait_for(process.communicate(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                Path(pcm_path).unlink(missing_ok=True)
                return
            
            if process.returncode == 0 and Path(wav_path).exists():
                # Tocar no B-leg
                b_exists = await self.esl.uuid_exists(self.b_leg_uuid)
                if b_exists:
                    result = await self.esl.execute_api(
                        f"uuid_displace {self.b_leg_uuid} start {wav_path} 0 mux"
                    )
                    if "+OK" not in (result or ""):
                        # Fallback
                        await self.esl.execute_api(
                            f"uuid_broadcast {self.b_leg_uuid} {wav_path} both"
                        )
                    logger.debug(f"Played {buffer_size} bytes to B-leg")
            
            # Cleanup
            Path(pcm_path).unlink(missing_ok=True)
            asyncio.create_task(self._delayed_cleanup(wav_path))
            
        except Exception as e:
            logger.error(f"Audio flush error: {e}")
            self._audio_buffer = bytearray()
    
    async def _delayed_cleanup(self, file_path: str, delay: float = 5.0) -> None:
        """Remove arquivo após delay."""
        from pathlib import Path
        try:
            await asyncio.sleep(delay)
            Path(file_path).unlink(missing_ok=True)
        except Exception:
            pass
    
    async def _cleanup(self) -> None:
        """
        Limpa recursos de forma segura e idempotente.
        
        Pode ser chamado múltiplas vezes sem efeitos colaterais.
        """
        # Flag para evitar cleanup duplo
        if hasattr(self, '_cleanup_done') and self._cleanup_done:
            return
        self._cleanup_done = True
        
        logger.debug("Starting announcement session cleanup...")
        
        # 1. Flush áudio pendente
        try:
            await self._flush_audio_buffer(force=True)
        except Exception:
            pass
        
        # 2. Cancelar sender task ANTES de fechar WebSockets
        if self._fs_sender_task:
            self._fs_sender_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._fs_sender_task),
                    timeout=1.0
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._fs_sender_task = None
        
        # 3. Fechar WebSocket do FreeSWITCH
        if self._fs_ws:
            try:
                await self._fs_ws.close()
            except Exception:
                pass
            self._fs_ws = None
        
        # 4. Fechar servidor WebSocket
        if self._audio_ws_server:
            self._audio_ws_server.close()
            try:
                await asyncio.wait_for(
                    self._audio_ws_server.wait_closed(),
                    timeout=2.0
                )
            except (Exception, asyncio.TimeoutError):
                pass
            self._audio_ws_server = None
        
        # 5. Fechar WebSocket do OpenAI
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        
        # 6. Parar stream no B-leg (verificar se ainda existe)
        try:
            b_exists = await asyncio.wait_for(
                self.esl.uuid_exists(self.b_leg_uuid),
                timeout=1.0
            )
            if b_exists:
                await self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop")
        except Exception:
            pass
        
        logger.debug("Conference announcement session cleaned up")
    
    # =========================================================================
    # WebSocket handlers para áudio FreeSWITCH
    # =========================================================================
    
    async def _handle_fs_ws(self, websocket: ServerConnection) -> None:
        """Recebe áudio do FreeSWITCH e envia ao OpenAI."""
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
        except Exception as e:
            logger.debug(f"FS WS closed: {e}")
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
        
        try:
            await self._ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(audio_24k).decode("utf-8"),
            }))
        except Exception:
            pass
    
    async def _enqueue_audio_to_freeswitch(self, audio_bytes: bytes) -> None:
        """Enfileira áudio do OpenAI para o FreeSWITCH."""
        if not audio_bytes:
            return
        try:
            audio_16k = self._resampler_out.process(audio_bytes)
        except Exception:
            audio_16k = audio_bytes
        
        audio_16k = self._fs_audio_buffer.add(audio_16k)
        if not audio_16k:
            return
        
        chunk_size = 640  # 20ms @ 16kHz
        for i in range(0, len(audio_16k), chunk_size):
            chunk = audio_16k[i:i + chunk_size]
            try:
                await self._fs_audio_queue.put(chunk)
            except Exception:
                break
    
    async def _fs_sender_loop(self) -> None:
        """Envia áudio para o FreeSWITCH."""
        if not self._fs_ws:
            return
        
        try:
            if not self._fs_rawaudio_sent:
                await self._fs_ws.send(json.dumps({
                    "type": "rawAudio",
                    "data": {"sampleRate": 16000}
                }))
                self._fs_rawaudio_sent = True
            
            while self._running and self._fs_ws:
                try:
                    # Timeout para evitar bloqueio indefinido
                    # Verifica self._running a cada 500ms
                    chunk = await asyncio.wait_for(
                        self._fs_audio_queue.get(),
                        timeout=0.5
                    )
                    await self._fs_ws.send(chunk)
                except asyncio.TimeoutError:
                    # Continuar loop para verificar self._running
                    continue
        except asyncio.CancelledError:
            # Cleanup normal
            pass
        except Exception as e:
            logger.debug(f"FS sender loop ended: {e}")
