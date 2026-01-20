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
    
    def _is_ws_closed(self) -> bool:
        """Verifica se WebSocket está fechado (compatível com diferentes versões)."""
        if not self._ws:
            return True
        # websockets >= 11.0 usa close_code, versões anteriores usam closed
        if hasattr(self._ws, 'close_code'):
            return self._ws.close_code is not None
        if hasattr(self._ws, 'closed'):
            return self._ws.closed
        # Fallback: verificar state se disponível
        if hasattr(self._ws, 'state'):
            from websockets.protocol import State
            return self._ws.state == State.CLOSED
        return False
    
    async def _wait_for_decision(self) -> None:
        """Aguarda decisão via function call ou patterns de texto."""
        while self._running and not self._accepted and not self._rejected:
            try:
                # Verificar se WebSocket ainda conectado
                if self._is_ws_closed():
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
                            # threshold: 0.0-1.0 (maior = menos sensível)
                            # 0.75 evita que ruídos/teclas interrompam a fala
                            "threshold": 0.75,
                            # prefix_padding_ms: buffer antes de detectar início de fala
                            # 400ms ignora sons curtos como cliques
                            "prefix_padding_ms": 400,
                            # silence_duration_ms: quanto silêncio antes de considerar fim de turno
                            # 700ms permite pausas naturais na fala
                            "silence_duration_ms": 700
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
        """
        Inicia stream de áudio bidirecional.
        
        IMPORTANTE para Docker:
        - REALTIME_BLEG_STREAM_BIND: onde o WS server escuta (default: 0.0.0.0)
        - REALTIME_BLEG_STREAM_HOST: endereço que FreeSWITCH usa para conectar
          - Se FreeSWITCH está no HOST e container em Docker: usar IP do container
          - Se ambos em Docker: usar nome do container ou IP interno
          - Se mesmo host: usar 127.0.0.1
        
        Para Docker for Mac/Windows com FreeSWITCH no host:
        - O container precisa expor a porta
        - FreeSWITCH precisa conectar ao IP do container (não 127.0.0.1)
        - Use: REALTIME_BLEG_STREAM_HOST=host.docker.internal (resolve para o host)
          OU use o IP real do container
        """
        try:
            bind_host = os.getenv("REALTIME_BLEG_STREAM_BIND", "0.0.0.0")
            # IMPORTANTE: Para Docker, usar host.docker.internal ou IP do container
            # 127.0.0.1 não funciona se FreeSWITCH está no host
            connect_host = os.getenv("REALTIME_BLEG_STREAM_HOST", "127.0.0.1")
            bleg_port_str = os.getenv("REALTIME_BLEG_STREAM_PORT", "")
            base_port = int(bleg_port_str) if bleg_port_str else 0
            
            # Log para debug de networking
            logger.info(f"🔊 Audio stream config: bind={bind_host}, connect={connect_host}, port={bleg_port_str or 'random'}")
            
            # Se porta configurada, tentar um range para suportar sessões simultâneas
            # Se porta 0, o OS escolhe uma porta livre
            if base_port == 0:
                logger.debug("Using random port for audio WS")
                ports_to_try = [0]  # OS escolhe
            else:
                # Tentar porta base e próximas 10 portas
                ports_to_try = list(range(base_port, base_port + 10))
            
            # Wrapper para logar conexões e erros
            async def ws_handler_with_logging(websocket):
                try:
                    logger.info(f"🔌 WS HANDLER CALLED - new connection incoming")
                    await self._handle_fs_ws(websocket)
                except Exception as e:
                    logger.error(f"🔌 WS HANDLER ERROR: {type(e).__name__}: {e}")
                    raise
            
            for port in ports_to_try:
                try:
                    logger.debug(f"Trying audio WS on {bind_host}:{port or 'random'}...")
                    self._audio_ws_server = await websockets.serve(
                        ws_handler_with_logging,
                        bind_host,
                        port,
                        max_size=None,
                        # Sem restrição de origin para aceitar conexão do FreeSWITCH
                        origins=None,
                        # Log de conexões no nível do servidor
                        logger=logger,
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
            logger.info(f"🔊 WS Server listening on {bind_host}:{self._audio_ws_port}")
            logger.info(f"🔊 FreeSWITCH (on HOST) will connect to: {ws_url}")
            
            # IMPORTANTE: Aguardar servidor estabilizar antes de enviar comando
            # Sem esse delay, o comando pode chegar antes do servidor estar pronto
            await asyncio.sleep(0.3)
            
            # Verificar conexão ESL antes de executar comando
            # IMPORTANTE: O atributo correto é _connected (com underscore)
            is_connected = getattr(self.esl, '_connected', False) or getattr(self.esl, 'connected', False)
            if not is_connected:
                logger.warning("🔌 ESL disconnected, attempting reconnect...")
                try:
                    await self.esl.connect()
                    logger.info("🔌 ESL reconnected successfully")
                except Exception as e:
                    logger.error(f"🔌 ESL reconnect failed: {e}")
            
            # DIAGNÓSTICO: Verificar estado do canal B-leg antes de iniciar stream
            try:
                # Verificar se canal existe
                exists_response = await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_exists {self.b_leg_uuid}"),
                    timeout=3.0
                )
                logger.info(f"🔍 B-leg exists check: {exists_response}")
                
                # Verificar estado do canal
                state_response = await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_getvar {self.b_leg_uuid} Channel-Call-State"),
                    timeout=3.0
                )
                logger.info(f"🔍 B-leg Channel-Call-State: {state_response}")
                
                # Verificar se está answered
                answered_response = await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_getvar {self.b_leg_uuid} Caller-Channel-Answered-Time"),
                    timeout=3.0
                )
                logger.info(f"🔍 B-leg Answered-Time: {answered_response}")
                
            except Exception as diag_e:
                logger.warning(f"🔍 Diagnóstico falhou: {diag_e}")
            
            # Iniciar mod_audio_stream no B-leg
            # IMPORTANTE: Tentar até 3 vezes com reconexão ESL entre tentativas
            cmd = f"uuid_audio_stream {self.b_leg_uuid} start {ws_url} mono 16k"
            logger.info(f"🔊 Executing: {cmd}")
            
            stream_started = False
            for attempt in range(3):
                try:
                    # Verificar/reconectar ESL antes de cada tentativa
                    is_connected = getattr(self.esl, '_connected', False)
                    if not is_connected:
                        logger.warning(f"🔌 [Attempt {attempt+1}] ESL disconnected, reconnecting...")
                        try:
                            await asyncio.wait_for(self.esl.connect(), timeout=3.0)
                            logger.info(f"🔌 [Attempt {attempt+1}] ESL reconnected")
                        except Exception as e:
                            logger.error(f"🔌 [Attempt {attempt+1}] ESL reconnect failed: {e}")
                            await asyncio.sleep(0.5)
                            continue
                    
                    response = await asyncio.wait_for(
                        self.esl.execute_api(cmd),
                        timeout=5.0
                    )
                    
                    # Verificar se resposta indica sucesso
                    response_str = str(response).strip() if response else ""
                    if "+OK" in response_str or response_str == "":
                        logger.info(f"🔊 Audio stream started: {response_str[:100] if response_str else 'OK'}")
                        stream_started = True
                        break
                    elif "-ERR" in response_str:
                        logger.error(f"❌ [Attempt {attempt+1}] FreeSWITCH error: {response_str}")
                        await asyncio.sleep(0.5)
                    else:
                        # Resposta desconhecida - assumir sucesso
                        logger.info(f"🔊 Audio stream response: {response_str[:100]}")
                        stream_started = True
                        break
                        
                except asyncio.TimeoutError:
                    logger.error(f"❌ [Attempt {attempt+1}] ESL command timeout")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"❌ [Attempt {attempt+1}] ESL command failed: {e}")
                    # Marcar como desconectado para forçar reconexão na próxima tentativa
                    if hasattr(self.esl, '_connected'):
                        self.esl._connected = False
                    await asyncio.sleep(0.5)
            
            if not stream_started:
                logger.error(f"❌ Failed to start audio stream after 3 attempts")
            
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
                logger.debug(f"🔊 OpenAI audio received: {len(audio_bytes)} bytes, fs_ws={self._fs_ws is not None}")
                if self._fs_ws:
                    await self._enqueue_audio_to_freeswitch(audio_bytes)
                else:
                    logger.warning("⚠️ No FS WebSocket - using TTS fallback")
                    await self._play_audio_fallback(audio_bytes)
        
        # FUNCTION CALL - Acumular argumentos (streaming)
        # Ref: Context7 /websites/platform_openai - response.function_call_arguments.delta
        elif etype == "response.function_call_arguments.delta":
            # Acumular argumentos para processar quando chegar .done
            output_index = event.get("output_index", 0)
            delta = event.get("delta", "")
            if not hasattr(self, "_function_call_args"):
                self._function_call_args = {}
            if output_index not in self._function_call_args:
                self._function_call_args[output_index] = {
                    "name": "",
                    "arguments": "",
                    "call_id": event.get("call_id", "")
                }
            self._function_call_args[output_index]["arguments"] += delta
        
        # FUNCTION CALL - Processamento final (argumentos completos)
        # Ref: Context7 /websites/platform_openai - response.function_call_arguments.done
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
        # Log detalhado da conexão
        try:
            remote = websocket.remote_address
            path = getattr(websocket, 'path', getattr(websocket, 'request', {}).path if hasattr(getattr(websocket, 'request', {}), 'path') else 'unknown')
            logger.info(f"🔌 FS WebSocket connection from: {remote}, path: {path}")
        except Exception as e:
            logger.info(f"🔌 FS WebSocket connection received (details unavailable: {e})")
        
        if self._fs_ws:
            logger.warning("🔌 FS WebSocket already connected, rejecting new connection")
            await websocket.close(1008, "Already connected")
            return
        
        self._fs_ws = websocket
        self._fs_connected.set()
        self._fs_rawaudio_sent = False
        self._fs_sender_task = asyncio.create_task(self._fs_sender_loop())
        
        total_bytes_received = 0
        messages_received = 0
        
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    total_bytes_received += len(message)
                    messages_received += 1
                    await self._handle_fs_audio(message)
                    
                    # Log a cada 100 mensagens (~2 segundos)
                    if messages_received % 100 == 0:
                        logger.debug(f"🎤 FS audio IN: {messages_received} frames ({total_bytes_received} bytes)")
        except Exception as e:
            logger.debug(f"🔌 FS WS closed: {e}")
        finally:
            logger.info(f"🔌 FS WebSocket ended: received {messages_received} frames ({total_bytes_received} bytes)")
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
        chunks_enqueued = 0
        for i in range(0, len(audio_16k), chunk_size):
            chunk = audio_16k[i:i + chunk_size]
            try:
                await self._fs_audio_queue.put(chunk)
                chunks_enqueued += 1
            except Exception:
                break
        
        if chunks_enqueued > 0:
            logger.debug(f"🔊 Audio enqueued: {chunks_enqueued} chunks ({len(audio_16k)} bytes) to FS")
    
    async def _fs_sender_loop(self) -> None:
        """Envia áudio para o FreeSWITCH."""
        if not self._fs_ws:
            logger.warning("🔊 _fs_sender_loop: No FS WebSocket!")
            return
        
        total_bytes_sent = 0
        chunks_sent = 0
        
        try:
            # Enviar mensagem de configuração inicial
            if not self._fs_rawaudio_sent:
                config_msg = json.dumps({
                    "type": "rawAudio",
                    "data": {"sampleRate": 16000}
                })
                await self._fs_ws.send(config_msg)
                self._fs_rawaudio_sent = True
                logger.info("🔊 FS sender: rawAudio config sent (16kHz)")
            
            while self._running and self._fs_ws:
                try:
                    # Timeout para evitar bloqueio indefinido
                    chunk = await asyncio.wait_for(
                        self._fs_audio_queue.get(),
                        timeout=0.5
                    )
                    await self._fs_ws.send(chunk)
                    total_bytes_sent += len(chunk)
                    chunks_sent += 1
                    
                    # Log a cada 50 chunks (~1 segundo de áudio)
                    if chunks_sent % 50 == 0:
                        logger.debug(f"🔊 FS sender: {chunks_sent} chunks sent ({total_bytes_sent} bytes total)")
                        
                except asyncio.TimeoutError:
                    # Continuar loop para verificar self._running
                    continue
                    
        except asyncio.CancelledError:
            logger.debug(f"🔊 FS sender: cancelled after {chunks_sent} chunks ({total_bytes_sent} bytes)")
        except Exception as e:
            logger.debug(f"🔊 FS sender loop ended: {e} (sent {chunks_sent} chunks, {total_bytes_sent} bytes)")
        finally:
            if chunks_sent > 0:
                logger.info(f"🔊 FS sender: TOTAL sent {chunks_sent} chunks ({total_bytes_sent} bytes)")
            else:
                logger.warning("🔊 FS sender: NO audio was sent to FreeSWITCH!")
