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
import time
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
# IMPORTANTE: Descrições detalhadas para evitar falsos positivos/negativos
TRANSFER_TOOLS = [
    {
        "type": "function",
        "name": "accept_transfer",
        "description": (
            "Chamado SOMENTE quando o atendente ACEITA EXPLICITAMENTE a transferência. "
            "Use APENAS quando ouvir confirmação INEQUÍVOCA como: "
            "'pode passar', 'pode conectar', 'manda', 'ok pode', 'coloca na linha', 'pode colocar'. "
            "NÃO use para saudações (alô, oi, bom dia) nem para perguntas (quem é?) nem para expressões irônicas (meu querido)."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "function",
        "name": "reject_transfer",
        "description": (
            "Chamado SOMENTE quando o atendente RECUSA EXPLICITAMENTE a transferência. "
            "Use APENAS quando ouvir recusa CLARA como: "
            "'não posso', 'estou ocupado', 'agora não', 'não dá', 'depois', 'liga mais tarde'. "
            "NÃO use para saudações (alô, oi, bom dia) nem para perguntas (quem é?) nem para expressões irônicas (meu querido)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Motivo da recusa (ex: 'ocupado', 'em reunião')"
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
        courtesy_message: Optional[str] = None,
        a_leg_hangup_event: Optional[asyncio.Event] = None,
        warmup_ms: Optional[int] = None,
    ):
        """
        Args:
            esl_client: Cliente ESL para controle de áudio
            b_leg_uuid: UUID do B-leg (atendente na conferência)
            system_prompt: Prompt de sistema para o agente
            initial_message: Mensagem inicial de anúncio
            voice: Voz do OpenAI
            model: Modelo Realtime
            courtesy_message: Mensagem de cortesia ao recusar (do banco de dados)
            a_leg_hangup_event: Evento para detectar quando cliente (A-leg) desliga
        """
        self.esl = esl_client
        self.b_leg_uuid = b_leg_uuid
        self.system_prompt = system_prompt
        self.initial_message = initial_message
        self.voice = voice
        self.model = model
        self.courtesy_message = courtesy_message
        self._a_leg_hangup_event = a_leg_hangup_event
        
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._transcript = ""
        self._last_human_transcript = ""  # Último transcript do atendente para verificação de segurança
        self._all_human_transcripts: list = []  # TODOS os transcripts do atendente
        self._accepted = False
        self._rejected = False
        self._rejection_message: Optional[str] = None
        self._reject_retry_count = 0
        
        # Flag para pular flush de áudio quando aceitar via pattern matching
        # Isso evita que áudio residual (ex: "vou anotar recado") seja reproduzido
        self._skip_audio_flush = False
        
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
        # IMPORTANTE: mod_audio_stream playback espera L16 @ 8kHz
        # Resample direto 24kHz -> 8kHz (evita artefatos de resampling em cadeia)
        self._resampler_out_8k = Resampler(24000, 8000)
        # Warmup para B-leg (configurável via banco)
        # Evita stutter sem adicionar latência excessiva
        warmup_ms = int(warmup_ms) if warmup_ms is not None else 100
        self._fs_audio_buffer = AudioBuffer(warmup_ms=warmup_ms, sample_rate=8000)
        
        # Buffer de áudio para fallback TTS
        self._audio_buffer = bytearray()
        
        # =========================================================================
        # TRACKING DINÂMICO DE ÁUDIO
        # O áudio da IA é dinâmico (nomes diferentes, frases diferentes).
        # Precisamos calcular e respeitar a duração REAL do áudio.
        # =========================================================================
        self._pending_audio_bytes: int = 0  # Bytes de áudio na fila aguardando envio
        self._audio_playback_done = asyncio.Event()  # Sinaliza quando todo áudio foi enviado
        self._audio_playback_done.set()  # Inicialmente sem áudio pendente
        self._response_audio_generating = False  # Indica se OpenAI está gerando áudio
        self._response_active = False
    
    async def _wait_for_audio_complete(
        self,
        context: str = "audio",
        max_wait: float = 8.0
    ) -> float:
        """
        Aguarda o áudio terminar de ser GERADO e REPRODUZIDO.
        
        Lógica em 3 fases:
        1. Esperar bytes chegarem (se ainda não chegaram)
        2. Esperar OpenAI terminar de GERAR (_response_audio_generating = False)
        3. Calcular tempo de reprodução restante baseado nos bytes reais
        
        Args:
            context: Contexto para logging
            max_wait: Tempo máximo de espera total
        
        Returns:
            Tempo total aguardado em segundos
        """
        import time
        start_time = time.time()
        
        # =========================================================
        # FASE 1: Esperar bytes chegarem
        # =========================================================
        bytes_wait = 0.0
        warmup_buffered = self._fs_audio_buffer.buffered_bytes if self._fs_audio_buffer else 0
        total_pending = self._pending_audio_bytes + warmup_buffered
        
        while total_pending == 0 and bytes_wait < 2.0:
            await asyncio.sleep(0.05)
            bytes_wait += 0.05
            warmup_buffered = self._fs_audio_buffer.buffered_bytes if self._fs_audio_buffer else 0
            total_pending = self._pending_audio_bytes + warmup_buffered
        
        if total_pending > 0 and bytes_wait > 0.1:
            logger.debug(
                f"⏳ [{context}] Bytes chegaram após {bytes_wait:.2f}s ({total_pending}b)"
            )
        
        # =========================================================
        # FASE 2: Esperar OpenAI terminar de GERAR
        # =========================================================
        generation_wait = time.time() - start_time
        while self._response_audio_generating and generation_wait < max_wait:
            await asyncio.sleep(0.1)
            generation_wait = time.time() - start_time
        
        if generation_wait > 0.5:
            warmup_buffered = self._fs_audio_buffer.buffered_bytes if self._fs_audio_buffer else 0
            total_pending = self._pending_audio_bytes + warmup_buffered
            logger.info(
                f"⏳ [{context}] OpenAI terminou de gerar após {generation_wait:.1f}s "
                f"({total_pending}b pendentes)"
            )
        
        # =========================================================
        # FASE 3: Calcular tempo de reprodução restante
        # =========================================================
        warmup_buffered = self._fs_audio_buffer.buffered_bytes if self._fs_audio_buffer else 0
        total_pending = self._pending_audio_bytes + warmup_buffered
        
        if total_pending > 0:
            # L16 @ 8kHz = 16 bytes/ms = 16000 bytes/s
            audio_duration = total_pending / 16000.0
            
            # Margem de 500ms para latência de rede/buffer
            MARGIN = 0.5
            wait_playback = audio_duration + MARGIN
            wait_playback = min(wait_playback, max_wait - generation_wait)
            wait_playback = max(wait_playback, 0.3)
            
            logger.info(
                f"⏳ [{context}] Aguardando reprodução: "
                f"{total_pending}b = {audio_duration:.1f}s + margem, wait={wait_playback:.1f}s"
            )
            
            # Esperar o evento de playback done OU o timeout
            try:
                await asyncio.wait_for(
                    self._audio_playback_done.wait(),
                    timeout=wait_playback
                )
                logger.debug(f"⏳ [{context}] Playback done sinalizado")
            except asyncio.TimeoutError:
                logger.debug(f"⏳ [{context}] Timeout aguardando playback")
            
            # Margem adicional pós-envio (buffer FreeSWITCH -> telefone)
            await asyncio.sleep(0.3)
        else:
            # Sem bytes, margem mínima
            await asyncio.sleep(0.3)
        
        total_wait = time.time() - start_time
        logger.debug(f"⏳ [{context}] Total aguardado: {total_wait:.1f}s")
        
        return total_wait
    
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
            
            # Helper para verificar hangup do A-leg (cliente)
            def check_a_leg_hangup() -> bool:
                if self._a_leg_hangup_event and self._a_leg_hangup_event.is_set():
                    logger.warning("🚨 [ANNOUNCEMENT] A-leg hangup detected - aborting")
                    self._rejected = True
                    self._rejection_message = "Cliente desligou"
                    return True
                return False
            
            # 1. Conectar ao OpenAI Realtime
            logger.info("🔌 Step 1: Connecting to OpenAI Realtime...")
            await self._connect_openai()
            logger.info("✅ Step 1: Connected")
            
            # Verificar A-leg antes de continuar
            if check_a_leg_hangup():
                return ConferenceAnnouncementResult(
                    accepted=False, rejected=True, 
                    message="Cliente desligou", duration_seconds=time.time() - start_time
                )
            
            # 2. Configurar sessão COM function calls
            logger.info("⚙️ Step 2: Configuring session with tools...")
            await self._configure_session_with_tools()
            logger.info("✅ Step 2: Session configured")
            
            # Verificar A-leg antes de continuar
            if check_a_leg_hangup():
                return ConferenceAnnouncementResult(
                    accepted=False, rejected=True,
                    message="Cliente desligou", duration_seconds=time.time() - start_time
                )
            
            # 3. Iniciar stream de áudio
            logger.info("🎤 Step 3: Starting audio stream...")
            await self._start_audio_stream()
            logger.info("✅ Step 3: Audio stream ready")
            
            # Verificar A-leg antes de continuar
            if check_a_leg_hangup():
                return ConferenceAnnouncementResult(
                    accepted=False, rejected=True,
                    message="Cliente desligou", duration_seconds=time.time() - start_time
                )
            
            # 4. Enviar mensagem inicial
            logger.info("💬 Step 4: Sending initial message...")
            await self._send_initial_message()
            logger.info("✅ Step 4: Initial message sent")
            
            # Verificar A-leg antes de entrar no loop
            if check_a_leg_hangup():
                return ConferenceAnnouncementResult(
                    accepted=False, rejected=True,
                    message="Cliente desligou", duration_seconds=time.time() - start_time
                )
            
            # 5. Loop principal - processar eventos até decisão ou timeout
            logger.info(f"▶️ Step 5: Waiting for decision (timeout={timeout}s)...")
            
            # Usar wait com timeout em vez de wait_for no loop inteiro
            try:
                await asyncio.wait_for(
                    self._wait_for_decision(),
                    timeout=timeout
                )
                logger.info(
                    f"✅ [DECISION] Decision received: accepted={self._accepted}, rejected={self._rejected}"
                )
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ [TIMEOUT] No decision after {timeout}s - will use default behavior")
            
        except asyncio.CancelledError:
            logger.info("🚫 [ANNOUNCEMENT] Cancelled externally")
            raise
        
        except Exception as e:
            logger.exception(f"❌ [ANNOUNCEMENT] Error: {e}")
        
        finally:
            logger.info(
                f"🏁 [ANNOUNCEMENT] Session ending - "
                f"accepted={self._accepted}, rejected={self._rejected}, "
                f"decision_event={self._decision_event.is_set()}"
            )
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
        loop_count = 0
        while self._running and not self._accepted and not self._rejected:
            loop_count += 1
            try:
                # Verificar se WebSocket ainda conectado
                if self._is_ws_closed():
                    logger.warning(f"🔌 [LOOP {loop_count}] OpenAI WebSocket closed unexpectedly")
                    break
                
                msg = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
                event = json.loads(msg)
                await self._handle_event(event)
                
                # Verificar se decision_event foi setado
                if self._decision_event.is_set():
                    break
                
            except asyncio.TimeoutError:
                # Log periódico a cada 5 segundos de espera
                if loop_count % 5 == 0:
                    logger.debug(f"⏳ [LOOP {loop_count}] Still waiting for decision...")
                
                # Verificar se A-leg (cliente) desligou primeiro (mais crítico)
                if self._a_leg_hangup_event and self._a_leg_hangup_event.is_set():
                    logger.warning(f"🚨 [LOOP {loop_count}] A-leg hangup detected - client disconnected, aborting announcement")
                    self._rejected = True
                    self._rejection_message = "Cliente desligou"
                    break
                
                # Verificar se B-leg ainda existe
                try:
                    b_exists = await asyncio.wait_for(
                        self.esl.uuid_exists(self.b_leg_uuid),
                        timeout=1.0
                    )
                    if not b_exists:
                        logger.info(f"📞 [LOOP {loop_count}] B-leg hangup detected - attendant disconnected")
                        self._rejected = True
                        self._rejection_message = "Atendente desligou"
                        break
                except Exception as e:
                    logger.debug(f"⚠️ [LOOP {loop_count}] B-leg check failed: {e}")
    
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
        
        # Timeout de 5 segundos para conexão (8+ segundos é inaceitável)
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=10,
                    open_timeout=5,  # Timeout para handshake
                ),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.error("🔌 OpenAI connection timeout (5s) - rede pode estar lenta")
            raise RuntimeError("OpenAI connection timeout - network may be slow")
        
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
        # Configuração da sessão OpenAI Realtime GA
        # Ref: Context7 - session.update audio transcription
        #
        # IMPORTANTE: audio.input.transcription é OBRIGATÓRIO para receber
        # eventos conversation.item.input_audio_transcription.completed
        # Sem isso, transcript será null e all_transcripts ficará vazio.
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
                            # 0.5 padrão, balanceado entre sensibilidade e ruído
                            "threshold": 0.5,
                            # prefix_padding_ms: buffer antes de detectar início de fala
                            # 300ms é o padrão da API
                            "prefix_padding_ms": 300,
                            # silence_duration_ms: quanto silêncio antes de considerar fim de turno
                            # 500ms é o padrão, permite respostas mais rápidas
                            "silence_duration_ms": 500,
                            # create_response: gerar resposta automaticamente ao fim do turno
                            "create_response": True,
                            # interrupt_response: permitir barge-in (atendente interrompe IA)
                            # Ref: Context7 - realtime-vad best practices
                            "interrupt_response": True
                        },
                        # Transcrição do input - OBRIGATÓRIO para receber transcripts do atendente
                        # Ref: Context7 - session.update audio transcription
                        # NOTA: NÃO incluir "language" - deixar auto-detectar
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
        
        logger.info(f"📤 Sending session.update with VAD, transcription and tools")
        logger.debug(f"Session config: {json.dumps(config)[:1000]}")
        
        await self._ws.send(json.dumps(config))
        
        # Aguardar confirmação
        try:
            msg = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            event = json.loads(msg)
            if event.get("type") == "session.updated":
                # Log detalhado da configuração aplicada
                session = event.get("session", {})
                audio = session.get("audio", {})
                input_cfg = audio.get("input", {})
                turn_detection = input_cfg.get("turn_detection", {})
                transcription = input_cfg.get("transcription")
                
                # Verificar se transcrição foi aplicada
                transcription_status = "✓" if transcription else "✗ NULL"
                transcription_model = transcription.get("model", "?") if transcription else "none"
                
                logger.info(
                    f"✅ Session configured: "
                    f"VAD={turn_detection.get('type', 'none')}, "
                    f"threshold={turn_detection.get('threshold', '?')}, "
                    f"create_response={turn_detection.get('create_response', '?')}, "
                    f"transcription={transcription_status} ({transcription_model}), "
                    f"tools={len(config.get('session', {}).get('tools', []))}"
                )
                
                # ALERTA se transcrição não foi aplicada
                if not transcription:
                    logger.error(
                        "❌ [CRITICAL] Transcription NOT configured! "
                        "Attendant speech will not be transcribed. "
                        "all_transcripts will be empty."
                    )
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
        
        FLUXO ROBUSTO:
        1. Verificar se B-leg está em estado ACTIVE (pode receber audio stream)
        2. Iniciar servidor WebSocket
        3. Enviar uuid_audio_stream com retry
        4. Aguardar conexão com timeout por tentativa
        """
        try:
            bind_host = os.getenv("REALTIME_BLEG_STREAM_BIND", "0.0.0.0")
            connect_host = os.getenv("REALTIME_BLEG_STREAM_HOST", "127.0.0.1")
            bleg_port_str = os.getenv("REALTIME_BLEG_STREAM_PORT", "")
            base_port = int(bleg_port_str) if bleg_port_str else 0
            
            logger.info(f"🔊 Audio stream config: bind={bind_host}, connect={connect_host}, port={bleg_port_str or 'random'}")
            
            # =================================================================
            # STEP 0: Verificar estado do B-leg ANTES de iniciar
            # O uuid_audio_stream só funciona em canais ACTIVE (answered)
            # =================================================================
            try:
                # Verificar se canal existe E está em estado correto
                state_response = await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_getvar {self.b_leg_uuid} state"),
                    timeout=2.0
                )
                state_str = str(state_response).strip().lower() if state_response else ""
                logger.info(f"🔍 B-leg state: {state_str}")
                
                # Estados válidos para audio stream: CS_EXECUTE, CS_ACTIVE
                # NOTA: "parked" é o estado do app, não do channel
                if "-err" in state_str or "no such channel" in state_str:
                    logger.warning(f"🔍 B-leg não existe ou estado inválido: {state_str}")
                    return
                    
            except asyncio.TimeoutError:
                logger.debug("🔍 B-leg state check timeout - continuing")
            except Exception as e:
                logger.debug(f"🔍 B-leg state check error: {e} - continuing")
            
            # =================================================================
            # STEP 1: Iniciar servidor WebSocket
            # =================================================================
            if base_port == 0:
                ports_to_try = [0]
            else:
                ports_to_try = list(range(base_port, base_port + 10))
            
            async def ws_handler_with_logging(websocket):
                try:
                    # DEBUG detalhado da conexão
                    remote = getattr(websocket, 'remote_address', 'unknown')
                    local = getattr(websocket, 'local_address', 'unknown')
                    path = getattr(websocket, 'path', 'unknown')
                    logger.info(f"🔌 [WS HANDLER] ======================================")
                    logger.info(f"🔌 [WS HANDLER] NEW CONNECTION INCOMING!")
                    logger.info(f"🔌 [WS HANDLER] Remote: {remote}")
                    logger.info(f"🔌 [WS HANDLER] Local: {local}")
                    logger.info(f"🔌 [WS HANDLER] Path: {path}")
                    logger.info(f"🔌 [WS HANDLER] WebSocket state: {websocket.state if hasattr(websocket, 'state') else 'N/A'}")
                    logger.info(f"🔌 [WS HANDLER] ======================================")
                    await self._handle_fs_ws(websocket)
                except Exception as e:
                    logger.error(f"🔌 [WS HANDLER] ERROR: {type(e).__name__}: {e}")
                    import traceback
                    logger.error(f"🔌 [WS HANDLER] Traceback: {traceback.format_exc()}")
                    raise
            
            for port in ports_to_try:
                try:
                    logger.debug(f"Trying audio WS on {bind_host}:{port or 'random'}...")
                    self._audio_ws_server = await websockets.serve(
                        ws_handler_with_logging,
                        bind_host,
                        port,
                        max_size=None,
                        origins=None,
                    )
                    break
                except OSError as e:
                    if port == ports_to_try[-1]:
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
            
            # DEBUG: Verificar estado do socket
            for sock in self._audio_ws_server.sockets:
                sock_info = sock.getsockname()
                logger.info(f"🔌 [WS DEBUG] Socket bound to: {sock_info}")
                logger.info(f"🔌 [WS DEBUG] Socket fileno: {sock.fileno()}")
                logger.info(f"🔌 [WS DEBUG] Socket family: {sock.family}")
            
            logger.info(f"🔊 Audio WS ready: {ws_url}")
            logger.info(f"🔊 WS Server listening on {bind_host}:{self._audio_ws_port}")
            logger.info(f"🔊 FreeSWITCH (on HOST) will connect to: {ws_url}")
            
            # DEBUG: Tentar conectar ao próprio servidor para verificar se está funcionando
            import socket
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(1.0)
            try:
                test_result = test_sock.connect_ex(('127.0.0.1', self._audio_ws_port))
                if test_result == 0:
                    logger.info(f"🔌 [WS DEBUG] ✅ Self-test: Port {self._audio_ws_port} is OPEN and accepting connections")
                else:
                    logger.warning(f"🔌 [WS DEBUG] ❌ Self-test: Port {self._audio_ws_port} returned error {test_result}")
            except Exception as e:
                logger.warning(f"🔌 [WS DEBUG] Self-test failed: {e}")
            finally:
                test_sock.close()
            
            # Aguardar servidor estabilizar
            await asyncio.sleep(0.3)
            
            # =================================================================
            # STEP 2: Verificar conexão ESL
            # =================================================================
            logger.info(f"🔌 [ESL DEBUG] ESL object type: {type(self.esl).__name__}")
            logger.info(f"🔌 [ESL DEBUG] ESL object id: {id(self.esl)}")
            
            is_connected = getattr(self.esl, '_connected', False) or getattr(self.esl, 'connected', False)
            logger.info(f"🔌 [ESL DEBUG] is_connected: {is_connected}")
            
            if not is_connected:
                logger.warning("🔌 ESL disconnected, attempting reconnect...")
                try:
                    await asyncio.wait_for(self.esl.connect(), timeout=3.0)
                    logger.info("🔌 ESL reconnected successfully")
                except Exception as e:
                    logger.error(f"🔌 ESL reconnect failed: {e}")
                    return
            
            # Verificação rápida do B-leg
            try:
                exists_response = await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_exists {self.b_leg_uuid}"),
                    timeout=1.0
                )
                if "true" not in (exists_response or "").lower():
                    logger.warning(f"🔍 B-leg não existe mais: {exists_response}")
                    return
                logger.debug(f"🔍 B-leg exists: OK")
            except asyncio.TimeoutError:
                logger.debug("🔍 B-leg check timeout - continuing anyway")
            except Exception as e:
                logger.debug(f"🔍 B-leg check error: {e} - continuing anyway")
            
            # =================================================================
            # STEP 3: Iniciar mod_audio_stream com retry ROBUSTO
            # 
            # PROBLEMA: O comando retorna OK mas o FreeSWITCH pode não conectar.
            # SOLUÇÃO: 
            # - 3 tentativas totais
            # - Timeout curto (2s) por tentativa de conexão
            # - Parar e reiniciar stream entre tentativas
            # =================================================================
            cmd = f"uuid_audio_stream {self.b_leg_uuid} start {ws_url} mono 16k"
            logger.info(f"🔊 [CMD DEBUG] Full command: {cmd}")
            logger.info(f"🔊 [CMD DEBUG] B-leg UUID: {self.b_leg_uuid}")
            logger.info(f"🔊 [CMD DEBUG] WebSocket URL: {ws_url}")
            logger.info(f"🔊 [CMD DEBUG] Sample rate: 16k mono")
            
            stream_connected = False
            max_attempts = 3
            
            for attempt in range(max_attempts):
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
                    
                    # Se não é a primeira tentativa, parar stream anterior primeiro
                    if attempt > 0:
                        logger.info(f"🔄 [Attempt {attempt+1}] Stopping previous stream...")
                        try:
                            await asyncio.wait_for(
                                self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop"),
                                timeout=2.0
                            )
                            self._fs_connected.clear()  # Reset evento de conexão
                            await asyncio.sleep(0.2)
                        except Exception:
                            pass
                    
                    # Enviar comando uuid_audio_stream
                    logger.info(f"🔊 [Attempt {attempt+1}] Sending ESL command...")
                    response = await asyncio.wait_for(
                        self.esl.execute_api(cmd),
                        timeout=3.0
                    )
                    
                    response_str = str(response).strip() if response else ""
                    logger.info(f"🔊 [Attempt {attempt+1}] ESL response type: {type(response).__name__}")
                    logger.info(f"🔊 [Attempt {attempt+1}] ESL response raw: '{response_str}'")
                    
                    if "-ERR" in response_str:
                        logger.error(f"❌ [Attempt {attempt+1}] FreeSWITCH error: {response_str}")
                        # DEBUG: Verificar se mod_audio_stream está carregado
                        try:
                            mod_check = await self.esl.execute_api("module_exists mod_audio_stream")
                            logger.info(f"🔊 [Attempt {attempt+1}] mod_audio_stream exists: {mod_check}")
                        except Exception as e:
                            logger.debug(f"🔊 [Attempt {attempt+1}] mod_audio_stream check failed: {e}")
                        await asyncio.sleep(0.5)
                        continue
                    
                    logger.info(f"🔊 [Attempt {attempt+1}] Audio stream command sent successfully")
                    
                    # Aguardar conexão do FreeSWITCH com timeout curto
                    connection_timeout = 2.0 if attempt < max_attempts - 1 else 3.0
                    try:
                        await asyncio.wait_for(self._fs_connected.wait(), timeout=connection_timeout)
                        logger.info(f"✅ [Attempt {attempt+1}] Audio stream connected (FULL-DUPLEX)")
                        stream_connected = True
                        break
                    except asyncio.TimeoutError:
                        logger.warning(f"⚠️ [Attempt {attempt+1}] Connection timeout ({connection_timeout}s)")
                        # Continuar para próxima tentativa
                        
                except asyncio.TimeoutError:
                    logger.error(f"❌ [Attempt {attempt+1}] ESL command timeout")
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.error(f"❌ [Attempt {attempt+1}] ESL command failed: {e}")
                    if hasattr(self.esl, '_connected'):
                        self.esl._connected = False
                    await asyncio.sleep(0.3)
            
            if not stream_connected:
                logger.warning(f"⚠️ Audio stream did not connect after {max_attempts} attempts - TTS fallback mode")
            
        except Exception as e:
            logger.error(f"Audio stream init failed: {e}")
    
    async def _send_initial_message(self) -> None:
        """
        Envia mensagem inicial de anúncio.
        
        IMPORTANTE: Usa response.create com instructions específicas para
        garantir que a IA fale EXATAMENTE a mensagem, sem elaborar.
        Isso evita problemas de:
        1. IA inventando texto adicional
        2. Respostas muito longas
        3. Interrupção por barge-in durante elaboração
        """
        if not self._ws:
            return
        
        # Usar response.create com instructions específicas
        # Isso faz a IA falar EXATAMENTE a mensagem, sem elaborar
        await self._ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "instructions": (
                    f"Diga EXATAMENTE esta frase, de forma clara e natural: "
                    f"\"{self.initial_message}\". "
                    f"Não adicione nada antes ou depois."
                )
            }
        }))
        
        # Log completo da mensagem (importante para debug)
        logger.info(f"Initial message sent: {self.initial_message}")
    
    async def _send_courtesy_response(self) -> None:
        """
        Envia resposta de cortesia quando o atendente recusa a chamada.
        
        Faz a IA dizer algo como "OK, obrigado" antes de desconectar,
        tornando a interação mais natural e educada.
        
        PRIORIDADE:
        1. Usa mensagem customizada do banco de dados (self.courtesy_message) se disponível
        2. Usa mensagem padrão hardcoded como fallback
        """
        if not self._ws or self._is_ws_closed():
            logger.debug("Cannot send courtesy response - WebSocket closed")
            return
        
        try:
            # PRIORIDADE: Usar mensagem do banco de dados se disponível
            if self.courtesy_message:
                courtesy_text = self.courtesy_message
                logger.info("Using custom courtesy message from database")
            else:
                # FALLBACK: Mensagem padrão
                courtesy_text = "OK, obrigado. Até logo."
            
            # Instrução clara e curta para a IA
            courtesy_instruction = (
                f"[SISTEMA] O atendente recusou a chamada. "
                f"Diga apenas: '{courtesy_text}' e encerre."
            )
            
            logger.info("💬 Sending courtesy response to attendant...")
            
            # PASSO 1: Cancelar qualquer resposta em andamento para evitar
            # receber o response.done da resposta anterior
            try:
                if self._response_active or self._response_audio_generating:
                    await self._ws.send(json.dumps({"type": "response.cancel"}))
                    # Aguardar mais tempo para o cancel ser processado
                    # O OpenAI pode levar até 500ms para processar o cancel
                    await asyncio.sleep(0.3)
                else:
                    logger.debug("⏳ [COURTESY] Sem resposta ativa, cancel não enviado")
                
                # Consumir eventos pendentes até encontrar um timeout
                # Usar timeout maior (100ms) para garantir que não há mais eventos
                drain_count = 0
                while drain_count < 20:  # Aumentar limite
                    try:
                        msg = await asyncio.wait_for(self._ws.recv(), timeout=0.1)
                        drain_count += 1
                        # Log do tipo de evento drenado para diagnóstico
                        try:
                            event = json.loads(msg)
                            etype = event.get("type", "unknown")
                            logger.debug(f"Drained event: {etype}")
                        except Exception:
                            pass
                    except asyncio.TimeoutError:
                        break  # Sem mais eventos pendentes
                if drain_count > 0:
                    logger.debug(f"Drained {drain_count} pending events before courtesy")
                
                # Aguardar um pouco mais para garantir que o canal está limpo
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.debug(f"Could not cancel previous response: {e}")
            
            # Se ainda há resposta ativa, não solicitar nova para evitar
            # "conversation_already_has_active_response" e sobreposição de fala.
            if self._response_active or self._response_audio_generating:
                logger.warning(
                    "⚠️ [COURTESY] Resposta ativa ainda em andamento - cortesia ignorada para evitar overlap"
                )
                return
            
            # PASSO 2: Enviar instrução de cortesia
            await self._ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": courtesy_instruction}]
                }
            }))
            
            # PASSO 3: Solicitar resposta
            await self._ws.send(json.dumps({"type": "response.create"}))
            
            # PASSO 4: Aguardar a IA gerar e reproduzir o áudio de cortesia
            # Timeout mais longo para garantir que a IA tenha tempo
            max_wait = 6.0  # segundos (aumentado)
            wait_interval = 0.1
            waited = 0.0
            audio_received = False
            response_started = False
            response_done_received = False
            audio_bytes_total = 0
            
            logger.debug("⏳ [COURTESY] Starting to wait for audio...")
            
            while waited < max_wait:
                try:
                    msg = await asyncio.wait_for(self._ws.recv(), timeout=wait_interval)
                    event = json.loads(msg)
                    etype = event.get("type", "")
                    
                    # Marcar quando resposta começa
                    if etype == "response.created":
                        response_started = True
                        logger.debug("⏳ [COURTESY] Response created")
                    
                    # Processar áudio de resposta
                    if etype in ("response.audio.delta", "response.output_audio.delta"):
                        audio_b64 = event.get("delta", "")
                        if audio_b64:
                            audio_bytes = base64.b64decode(audio_b64)
                            audio_bytes_total += len(audio_bytes)
                            # Usar o método correto para enfileirar áudio
                            await self._enqueue_audio_to_freeswitch(audio_bytes)
                            audio_received = True
                    
                    # Se resposta terminou
                    if etype == "response.done":
                        response_done_received = True
                        if audio_received:
                            logger.info(f"✅ Courtesy response completed (with audio: {audio_bytes_total}b)")
                        else:
                            # Se não recebeu áudio mas resposta terminou,
                            # pode ser um problema de timing - verificar se há erro
                            response_data = event.get("response", {})
                            status = response_data.get("status", "unknown")
                            logger.warning(f"⚠️ Courtesy response.done but NO AUDIO (status={status})")
                            
                            # Tentar reenviar a cortesia
                            if not audio_received and response_started:
                                logger.info("🔄 [COURTESY] Tentando reenviar cortesia...")
                                try:
                                    await self._ws.send(json.dumps({
                                        "type": "response.create",
                                        "response": {
                                            "instructions": "Diga apenas: 'OK, obrigado. Até logo.'"
                                        }
                                    }))
                                    # Continuar o loop para receber o áudio
                                    response_done_received = False
                                    continue
                                except Exception:
                                    pass
                        break
                    
                    # Tratar erros
                    if etype == "error":
                        error = event.get("error", {})
                        error_code = error.get("code", "unknown")
                        if error_code != "response_cancel_not_active":
                            logger.warning(f"⚠️ [COURTESY] OpenAI error: {error}")
                    
                except asyncio.TimeoutError:
                    waited += wait_interval
                    # Se já recebemos áudio e não veio mais por 500ms, considerar completo
                    if audio_received and waited > 0.5:
                        logger.debug(f"⏳ [COURTESY] Audio timeout after {audio_bytes_total}b")
                    continue
                except Exception as e:
                    logger.debug(f"Error receiving courtesy event: {e}")
                    break
            
            if waited >= max_wait:
                logger.warning(f"⚠️ Courtesy response timeout after {max_wait}s (audio_received={audio_received})")
            
            # PASSO 5: Aguardar áudio terminar de ser reproduzido
            # Usa lógica robusta de 3 fases para garantir que a cortesia seja ouvida
            await self._wait_for_audio_complete(
                context="courtesy",
                max_wait=5.0
            )
            
        except Exception as e:
            logger.warning(f"Could not send courtesy response: {e}")
    
    async def _handle_event(self, event: dict) -> None:
        """Processa evento do OpenAI Realtime."""
        etype = event.get("type", "")
        
        # Lista de eventos conhecidos (para logging de eventos desconhecidos)
        # Ref: Context7 /websites/platform_openai - realtime server events
        KNOWN_EVENTS = {
            # Session lifecycle
            "session.created", "session.updated",
            # Response lifecycle
            "response.created", "response.done",
            "response.output_item.added", "response.output_item.done",
            "response.content_part.added", "response.content_part.done",
            # Audio output (formatos antigo e novo)
            "response.audio.delta", "response.output_audio.delta",
            "response.audio.done", "response.output_audio.done",
            # Transcrição do assistente
            "response.audio_transcript.delta", "response.output_audio_transcript.delta",
            "response.audio_transcript.done", "response.output_audio_transcript.done",
            # Function calls
            "response.function_call_arguments.delta", "response.function_call_arguments.done",
            # Transcrição do usuário (STT)
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.failed",
            "conversation.item.input_audio_transcription.delta",
            "conversation.item.added", "conversation.item.created", "conversation.item.done",
            # VAD e input buffer
            "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
            "input_audio_buffer.committed", "input_audio_buffer.cleared",
            "input_audio_buffer.timeout_triggered",
            # Outros
            "error", "rate_limits.updated",
        }
        
        # Log eventos desconhecidos para diagnóstico
        if etype and etype not in KNOWN_EVENTS:
            logger.warning(f"🔍 [UNKNOWN_EVENT] {etype}: {json.dumps(event)[:300]}")
        
        # VAD: Detectou início de fala do atendente
        if etype == "input_audio_buffer.speech_started":
            item_id = event.get("item_id", "")
            audio_start = event.get("audio_start_ms", 0)
            logger.info(f"🎙️ [VAD] Atendente começou a falar (item={item_id}, audio_start={audio_start}ms)")
        
        # VAD: Detectou fim de fala do atendente
        if etype == "input_audio_buffer.speech_stopped":
            item_id = event.get("item_id", "")
            audio_end = event.get("audio_end_ms", 0)
            logger.info(f"🎙️ [VAD] Atendente parou de falar (item={item_id}, audio_end={audio_end}ms)")
        
        # Transcrição falhou
        if etype == "conversation.item.input_audio_transcription.failed":
            error = event.get("error", {})
            item_id = event.get("item_id", "")
            logger.error(f"❌ [TRANSCRIPTION_FAILED] item={item_id}, error={error}")
        
        if etype == "response.created":
            self._response_active = True

        # Áudio de resposta - enviar para FreeSWITCH
        if etype in ("response.audio.delta", "response.output_audio.delta"):
            audio_b64 = event.get("delta", "")
            if audio_b64:
                audio_bytes = base64.b64decode(audio_b64)
                
                # Marcar que há áudio sendo gerado
                self._audio_playback_done.clear()
                self._response_audio_generating = True
                
                if self._fs_ws:
                    # O tracking de bytes é feito em _enqueue_audio_to_freeswitch
                    # baseado nos bytes EFETIVAMENTE enfileirados
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
            # Armazenar para verificação de segurança em accept_transfer
            self._last_human_transcript = human_transcript
            self._all_human_transcripts.append(human_transcript)
            # Usar lock para proteger contra race condition com function calls
            await self._check_human_decision_safe(human_transcript)
        
        # Transcrição do assistente
        elif etype in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            delta = event.get("delta", "")
            self._transcript += delta
        
        # Áudio da resposta TERMINADO de ser gerado pelo OpenAI
        # IMPORTANTE: Isso significa que o OpenAI terminou de GERAR, mas ainda precisamos
        # enviar todo o áudio para o FreeSWITCH antes de processar decisões
        elif etype in ("response.audio.done", "response.output_audio.done"):
            # Marcar que OpenAI terminou de GERAR
            self._response_audio_generating = False
            
            # Incluir bytes do warmup buffer que ainda não foram enfileirados
            warmup_buffered = self._fs_audio_buffer.buffered_bytes
            total_pending = self._pending_audio_bytes + warmup_buffered
            
            # Calcular duração estimada
            audio_duration_ms = total_pending / 16.0  # L16 @ 8kHz = 16 bytes/ms
            
            logger.info(
                f"🔊 Response audio DONE (OpenAI finished generating): "
                f"pending={self._pending_audio_bytes}b, warmup={warmup_buffered}b, "
                f"total={total_pending}b (~{audio_duration_ms:.0f}ms to play)"
            )
        
        # Resposta completa (texto + áudio + function calls)
        elif etype == "response.done":
            self._response_active = False
            await self._flush_audio_buffer(force=True)
            await self._check_assistant_decision()
        
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
        
        IMPORTANTE: Aguarda o áudio terminar de ser reproduzido ANTES de sinalizar
        a decisão. Isso evita cortes, robotização e picotes no final da fala da IA.
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
                # =========================================================================
                # VERIFICAÇÃO DE SEGURANÇA: Checar TODOS os transcripts por negação
                # Isso previne erros da IA que chama accept_transfer quando deveria rejeitar
                # =========================================================================
                all_transcripts = getattr(self, '_all_human_transcripts', [])
                last_transcript = getattr(self, '_last_human_transcript', '')
                
                # Combinar todos os transcripts para verificação
                combined_transcript = ' '.join(all_transcripts).lower().strip()
                
                logger.info(f"🔍 Safety check: all transcripts = {all_transcripts}")
                logger.info(f"🔍 Safety check: combined = '{combined_transcript}'")
                
                rejection_indicators = [
                    'não', 'nao', 'agora não', 'não posso', 'ocupado',
                    'depois', 'mais tarde', 'não dá', 'não quero',
                    'não vou', 'não tenho', 'não vai dar'
                ]
                
                # Verificar se contém indicadores de recusa
                is_rejection = False
                matched_indicator = None
                
                if combined_transcript:
                    # Verificar cada indicador
                    for indicator in rejection_indicators:
                        if indicator in combined_transcript:
                            is_rejection = True
                            matched_indicator = indicator
                            logger.warning(f"⚠️ Safety check: '{indicator}' found in transcripts")
                            break
                    
                    # Verificar "não" como palavra isolada no início de qualquer transcript
                    if not is_rejection:
                        for transcript in all_transcripts:
                            words = transcript.lower().strip().split()
                            if words and words[0].rstrip(".,!?") in ['não', 'nao']:
                                is_rejection = True
                                matched_indicator = f"'{words[0]}' as first word"
                                logger.warning(f"⚠️ Safety check: 'não' as first word in '{transcript}'")
                                break
                
                if is_rejection:
                    # Converter accept_transfer para reject_transfer
                    self._rejection_message = f"Atendente disse não ({matched_indicator})"
                    logger.info(f"🔄 Function call OVERRIDDEN: accept→reject (matched: {matched_indicator})")
                    
                    await self._send_courtesy_response()
                    self._rejected = True
                else:
                    self._accepted = True
                    logger.info(f"✅ Function call: ACCEPTED (no rejection indicators in '{combined_transcript}')")
                
            elif function_name == "reject_transfer":
                # =========================================================================
                # VERIFICAÇÃO DE SEGURANÇA: Checar se foi apenas saudação mal interpretada
                # Saudações/cumprimentos NÃO devem ser interpretados como rejeição
                # =========================================================================
                all_transcripts = getattr(self, '_all_human_transcripts', [])
                combined_transcript = ' '.join(all_transcripts).lower().strip()
                
                # IMPORTANTE: Normalizar removendo pontuação para comparação
                # "Bom dia." deve ser tratado igual a "bom dia"
                import re
                combined_clean = re.sub(r'[.!?,;:\'"]+', '', combined_transcript).strip()
                
                # Lista de saudações/cumprimentos GENUÍNOS que NÃO são rejeição
                greeting_patterns = [
                    "alô", "alo", "oi", "olá", "ola", "fala", "pois não", "pois nao",
                    "bom dia", "boa tarde", "boa noite", "tudo bem", "como vai",
                    "fala aí", "fala ai", "e aí", "e ai", "opa", "beleza",
                    "pode falar", "estou ouvindo", "ouvindo", "presente",
                    "sim", "diga", "fale", "pronto", "quem"
                ]
                
                # Expressões ambíguas no Brasil (irônicas/sarcásticas) - NÃO são recusa explícita
                # Quando ouvir isso, devemos PERGUNTAR de novo, não rejeitar automaticamente
                ambiguous_patterns = [
                    "meu querido", "minha querida", "meu amigo", "minha amiga",
                    "querido", "querida", "amigo", "amiga"
                ]
                
                # Verificar se é expressão ambígua (irônica) PRIMEIRO
                # Isso tem prioridade porque "oi meu querido" ainda é ambíguo
                is_ambiguous = False
                for pattern in ambiguous_patterns:
                    if pattern in combined_clean:
                        is_ambiguous = True
                        logger.warning(f"⚠️ Safety check: reject_transfer called but transcript is ambiguous/ironic: '{combined_transcript}'")
                        break
                
                # Verificar se é APENAS saudação genuína (sem expressão ambígua)
                # Usar combined_clean (sem pontuação) para comparação
                is_only_greeting = False
                if not is_ambiguous:
                    for pattern in greeting_patterns:
                        # Verificar match exato ou como parte de frase
                        if (combined_clean == pattern or 
                            combined_clean.startswith(pattern + " ") or 
                            combined_clean.endswith(" " + pattern) or
                            f" {pattern} " in f" {combined_clean} "):
                            is_only_greeting = True
                            logger.warning(f"⚠️ Safety check: reject_transfer called but transcript looks like greeting: '{combined_transcript}' (clean: '{combined_clean}')")
                            break
                
                logger.info(f"🔍 Safety check (reject): raw='{combined_transcript}', clean='{combined_clean}', is_greeting={is_only_greeting}, is_ambiguous={is_ambiguous}")
                
                # Verificar se há recusa EXPLÍCITA no transcript
                rejection_indicators = [
                    "não", "nao", "agora não", "agora nao", "não posso", "nao posso",
                    "ocupado", "ocupada", "depois", "mais tarde", "não dá", "nao da",
                    "não vai dar", "nao vai dar", "não consigo", "nao consigo",
                    "recusar", "recuso", "não atendo", "nao atendo"
                ]
                has_explicit_reject = any(indicator in combined_clean for indicator in rejection_indicators)
                
                if not has_explicit_reject:
                    logger.warning(f"⚠️ Safety check: reject_transfer sem recusa explícita no transcript")
                
                should_ask_again = (is_only_greeting or is_ambiguous or not has_explicit_reject)
                if should_ask_again:
                    if self._reject_retry_count < 1:
                        self._reject_retry_count += 1
                        reason = "greeting/ambiguous" if (is_only_greeting or is_ambiguous) else "unclear_reject"
                        logger.info(f"🔄 Function call IGNORED: reject→ask_again ({reason})")
                        if call_id:
                            status = "ignored_ambiguous" if (is_only_greeting or is_ambiguous) else "ignored_unclear"
                            await self._send_function_output(call_id, {"status": status})
                        
                        try:
                            await self._ws.send(json.dumps({
                                "type": "response.create",
                                "response": {
                                    "instructions": (
                                        "O atendente não deu uma resposta clara. "
                                        "Pergunte diretamente: 'Você pode atender essa ligação agora ou prefere que eu anote o recado?'"
                                    )
                                }
                            }))
                            logger.info("🔄 Asked attendant again after ambiguous response")
                        except Exception as e:
                            logger.debug(f"Could not ask again: {e}")
                        return
                    else:
                        logger.warning("⚠️ Safety check: limite de re-tentativas atingido, aceitando rejeição")
                    
                    return  # Sair sem marcar decisão
                
                # Extrair motivo se fornecido
                try:
                    args = json.loads(arguments) if isinstance(arguments, str) else arguments
                    self._rejection_message = args.get("reason", "Recusado pelo atendente")
                except Exception:
                    self._rejection_message = "Recusado pelo atendente"
                logger.info(f"❌ Function call: REJECTED - {self._rejection_message}")
                
                # Enviar resposta de cortesia ANTES de marcar como rejeitado
                # Isso permite a IA falar "OK, obrigado" antes de desconectar
                await self._send_courtesy_response()
                
                self._rejected = True
            
            # Enviar output da function (obrigatório)
            if call_id:
                await self._send_function_output(call_id, {"status": "ok"})
            
            # =========================================================================
            # AGUARDAR ÁUDIO TERMINAR (DINÂMICO)
            # A IA pode estar falando algo como "Ok, vou conectar vocês" junto com
            # o accept_transfer. Precisamos esperar ela terminar de falar antes de
            # sinalizar a decisão, caso contrário o áudio será cortado.
            # 
            # Usa lógica robusta de 3 fases:
            # 1. Esperar bytes chegarem
            # 2. Esperar OpenAI terminar de GERAR
            # 3. Calcular tempo de reprodução restante
            # =========================================================================
            await self._wait_for_audio_complete(
                context="function_call",
                max_wait=10.0
            )
            
            logger.info("✅ Signaling decision after audio completed")
            # Sinalizar que decisão foi tomada (após áudio terminar + margem)
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
                "tá bom", "tá bem", "tudo bem", "ta bom", "ta bem",
                "beleza", "aceito", "claro", "certo", "posso sim",
                "manda", "passa aí", "passa ai", "conecta",
                "vou atender", "pode colocar", "coloca na linha",
            ]
            
            # Patterns genéricos que precisam ser palavra isolada ou início de frase
            accept_generic = ["sim", "ok", "pode", "posso", "beleza", "certo", "claro"]
            
            # Patterns de RECUSA - ordenados por especificidade
            # IMPORTANTE: "não" isolado AGORA é considerado recusa
            # Se o atendente diz apenas "não", é recusa clara
            reject_patterns = [
                "não posso", "não dá", "não quero", "não tenho tempo",
                "estou ocupado", "ocupado", "em reunião",
                "depois", "mais tarde", "agora não",
                "recuso", "não aceito", "não vou atender",
                "não vai dar", "não tenho como", "não tem como",
            ]
            
            # "não" isolado ou como primeira palavra = recusa
            reject_generic = ["não", "nao"]
            
            # Verificar patterns específicos de aceite
            for pattern in accept_patterns:
                if pattern in text_lower:
                    self._accepted = True
                    self._skip_audio_flush = True  # 🚀 Não fazer flush - bridge imediato
                    logger.info(f"Human ACCEPTED: matched '{pattern}' - skipping audio flush")
                    self._decision_event.set()
                    return
            
            # Verificar patterns genéricos de aceite (palavra isolada ou início)
            words = text_lower.split()
            if words:
                first_word = words[0].rstrip(".,!?")
                if first_word in accept_generic or (len(words) == 1 and first_word in accept_generic):
                    self._accepted = True
                    self._skip_audio_flush = True  # 🚀 Não fazer flush - bridge imediato
                    logger.info(f"Human ACCEPTED: generic match '{first_word}' - skipping audio flush")
                    self._decision_event.set()
                    return
            
            # Verificar patterns de recusa
            for pattern in reject_patterns:
                if pattern in text_lower:
                    self._rejection_message = human_text
                    logger.info(f"Human REJECTED: matched '{pattern}'")
                    
                    # Enviar resposta de cortesia ANTES de marcar como rejeitado
                    # Isso permite a IA falar "OK, obrigado" antes de desconectar
                    await self._send_courtesy_response()
                    
                    self._rejected = True
                    self._decision_event.set()
                    return
            
            # Verificar "não" como primeira palavra ou isolado = recusa
            if words:
                first_word = words[0].rstrip(".,!?")
                # "não" ou "nao" como primeira palavra é recusa clara
                if first_word in reject_generic:
                    self._rejection_message = human_text
                    logger.info(f"Human REJECTED: 'não' detected as first word")
                    
                    await self._send_courtesy_response()
                    
                    self._rejected = True
                    self._decision_event.set()
                    return
    
    async def _check_assistant_decision(self) -> None:
        """Verifica decisão na transcrição do assistente (fallback)."""
        text = self._transcript.upper()
        
        if "ACEITO" in text and not self._rejected:
            self._accepted = True
            logger.info("Assistant indicated: ACCEPTED")
            self._decision_event.set()
        
        elif "RECUSADO" in text and not self._accepted:
            parts = self._transcript.split("RECUSADO:")
            if len(parts) > 1:
                self._rejection_message = parts[1].strip()[:200]
            logger.info(f"Assistant indicated: REJECTED")
            
            # Enviar resposta de cortesia antes de marcar como rejeitado
            await self._send_courtesy_response()
            
            self._rejected = True
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
        
        # Log detalhado do motivo do cleanup
        logger.info(
            f"🧹 [CLEANUP] Starting announcement session cleanup - "
            f"accepted={self._accepted}, rejected={self._rejected}, "
            f"running={self._running}, port={self._audio_ws_port}"
        )
        
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
        
        # 4. Fechar servidor WebSocket (porta 8086)
        if self._audio_ws_server:
            logger.info(f"🧹 [CLEANUP] Closing WebSocket server on port {self._audio_ws_port}")
            self._audio_ws_server.close()
            try:
                await asyncio.wait_for(
                    self._audio_ws_server.wait_closed(),
                    timeout=2.0
                )
                logger.info(f"🧹 [CLEANUP] WebSocket server closed (port {self._audio_ws_port})")
            except (Exception, asyncio.TimeoutError) as e:
                logger.warning(f"🧹 [CLEANUP] WebSocket server close timeout/error: {e}")
            self._audio_ws_server = None
        
        # 5. Fechar WebSocket do OpenAI (com timeout curto!)
        if self._ws:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=1.0)
            except (Exception, asyncio.TimeoutError) as e:
                logger.debug(f"🧹 [CLEANUP] OpenAI WS close: {type(e).__name__}")
            self._ws = None
        
        # 6. Parar stream no B-leg (verificar se ainda existe)
        try:
            b_exists = await asyncio.wait_for(
                self.esl.uuid_exists(self.b_leg_uuid),
                timeout=0.5
            )
            if b_exists:
                await asyncio.wait_for(
                    self.esl.execute_api(f"uuid_audio_stream {self.b_leg_uuid} stop"),
                    timeout=0.5
                )
        except (Exception, asyncio.TimeoutError):
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
        """Enfileira áudio do OpenAI para o FreeSWITCH.
        
        IMPORTANTE: mod_audio_stream playback espera L16 @ 8kHz!
        Fluxo: OpenAI 24kHz -> 8kHz (direto, sem cadeia)
        
        TRACKING DINÂMICO: Contamos bytes EFETIVAMENTE enfileirados,
        não os bytes recebidos do OpenAI. Isso é preciso porque:
        1. O resampler pode alterar a quantidade de bytes
        2. O AudioBuffer acumula bytes durante warmup
        """
        if not audio_bytes:
            return
        
        # Resample direto: 24kHz -> 8kHz
        # Evita artefatos de resampling em cadeia (24->16->8)
        try:
            audio_8k = self._resampler_out_8k.process(audio_bytes)
        except Exception:
            audio_8k = audio_bytes
        
        # Buffer de warmup para playback suave
        # NOTA: Durante warmup, add() retorna bytes vazios e acumula internamente
        audio_to_enqueue = self._fs_audio_buffer.add(audio_8k)
        
        if not audio_to_enqueue:
            # Ainda em warmup - bytes estão sendo acumulados no buffer interno
            # _pending_audio_bytes conta apenas bytes NA FILA (queue)
            # O warmup buffer é consultado separadamente via buffered_bytes
            logger.debug(f"🔊 Warmup buffering: {self._fs_audio_buffer.buffered_bytes} bytes")
            return
        
        # L16 @ 8kHz, 20ms = 160 samples * 2 bytes = 320 bytes per chunk
        chunk_size = 320
        bytes_enqueued = 0
        for i in range(0, len(audio_to_enqueue), chunk_size):
            chunk = audio_to_enqueue[i:i + chunk_size]
            try:
                await self._fs_audio_queue.put(chunk)
                bytes_enqueued += len(chunk)
            except Exception:
                break
        
        # TRACKING DINÂMICO: _pending_audio_bytes conta APENAS bytes na fila (queue)
        # O warmup buffer é contabilizado separadamente em buffered_bytes
        # Isso evita contagem dupla quando o warmup termina
        self._pending_audio_bytes += bytes_enqueued
        
        if bytes_enqueued > 0:
            warmup_buffered = self._fs_audio_buffer.buffered_bytes
            total_pending = self._pending_audio_bytes + warmup_buffered
            logger.debug(
                f"🔊 Audio enqueued: {bytes_enqueued}b to FS @ 8kHz "
                f"(queue: {self._pending_audio_bytes}b, warmup: {warmup_buffered}b, total: {total_pending}b)"
            )
    
    async def _fs_sender_loop(self) -> None:
        """Envia áudio para o FreeSWITCH com rate limiting.
        
        IMPORTANTE: mod_audio_stream espera JSON com formato:
        {"type": "streamAudio", "data": {"audioData": "<base64>", "audioDataType": "raw"}}
        
        Mensagens binárias são IGNORADAS pelo mod_audio_stream!
        
        Rate limiting: envia na velocidade de tempo real para evitar buffer overrun.
        """
        if not self._fs_ws:
            logger.warning("🔊 _fs_sender_loop: No FS WebSocket!")
            return
        
        total_bytes_sent = 0
        chunks_sent = 0
        batch_buffer = bytearray()
        last_send_time = 0.0
        
        # Configuração de batching para suavizar playback
        # 320 bytes = 20ms @ 8kHz L16
        # Batch de 5 chunks = 100ms = 1600 bytes
        batch_bytes = 1600
        batch_duration_ms = 100.0
        
        try:
            # Enviar mensagem de configuração inicial (opcional, para compatibilidade)
            if not self._fs_rawaudio_sent:
                config_msg = json.dumps({
                    "type": "rawAudio",
                    "data": {"sampleRate": 8000}
                })
                await self._fs_ws.send(config_msg)
                self._fs_rawaudio_sent = True
                logger.info("🔊 FS sender: rawAudio config sent (8kHz L16)")
            
            while self._running and self._fs_ws:
                try:
                    # Timeout para evitar bloqueio indefinido
                    chunk = await asyncio.wait_for(
                        self._fs_audio_queue.get(),
                        timeout=0.5
                    )
                    
                    # Acumular no batch buffer
                    batch_buffer.extend(chunk)
                    
                    # Enviar batch quando atingir tamanho alvo
                    if len(batch_buffer) >= batch_bytes:
                        # Rate limit: esperar tempo real antes de enviar próximo batch
                        if last_send_time > 0:
                            now = time.time()
                            elapsed_ms = (now - last_send_time) * 1000
                            if elapsed_ms < batch_duration_ms:
                                wait_ms = batch_duration_ms - elapsed_ms
                                await asyncio.sleep(wait_ms / 1000.0)
                        
                        # CORREÇÃO: mod_audio_stream espera JSON, não binary frames!
                        batch_size = len(batch_buffer)
                        audio_msg = json.dumps({
                            "type": "streamAudio",
                            "data": {
                                "audioData": base64.b64encode(bytes(batch_buffer)).decode("utf-8"),
                                "audioDataType": "raw"
                            }
                        })
                        await self._fs_ws.send(audio_msg)
                        
                        total_bytes_sent += batch_size
                        chunks_sent += 1
                        last_send_time = time.time()
                        batch_buffer.clear()
                        
                        # TRACKING DINÂMICO: Atualizar bytes pendentes
                        self._pending_audio_bytes = max(0, self._pending_audio_bytes - batch_size)
                        
                        # Verificar se todo áudio foi enviado (fila + warmup buffer)
                        warmup_remaining = self._fs_audio_buffer.buffered_bytes
                        if self._pending_audio_bytes == 0 and self._fs_audio_queue.empty() and warmup_remaining == 0:
                            self._audio_playback_done.set()
                    
                    # Log a cada 10 batches (~1 segundo de áudio)
                    if chunks_sent > 0 and chunks_sent % 10 == 0:
                        logger.debug(f"🔊 FS sender: {chunks_sent} batches sent ({total_bytes_sent} bytes total)")
                        
                except asyncio.TimeoutError:
                    # Timeout - enviar batch parcial se houver dados
                    if batch_buffer and self._fs_ws:
                        partial_size = len(batch_buffer)
                        audio_msg = json.dumps({
                            "type": "streamAudio",
                            "data": {
                                "audioData": base64.b64encode(bytes(batch_buffer)).decode("utf-8"),
                                "audioDataType": "raw"
                            }
                        })
                        await self._fs_ws.send(audio_msg)
                        total_bytes_sent += partial_size
                        chunks_sent += 1
                        last_send_time = time.time()
                        batch_buffer.clear()
                        
                        # TRACKING DINÂMICO: Atualizar bytes pendentes
                        self._pending_audio_bytes = max(0, self._pending_audio_bytes - partial_size)
                    
                    # Se não há mais áudio pendente (fila + warmup), sinalizar
                    warmup_remaining = self._fs_audio_buffer.buffered_bytes
                    if self._pending_audio_bytes == 0 and self._fs_audio_queue.empty() and warmup_remaining == 0:
                        self._audio_playback_done.set()
                    continue
                    
        except asyncio.CancelledError:
            logger.debug(f"🔊 FS sender: cancelled after {chunks_sent} batches ({total_bytes_sent} bytes)")
        except Exception as e:
            logger.debug(f"🔊 FS sender loop ended: {e} (sent {chunks_sent} batches, {total_bytes_sent} bytes)")
        finally:
            # 🚀 SKIP FLUSH: Se aceitou via pattern matching, não enviar áudio residual
            # Isso evita que a IA fale "vou anotar recado" enquanto faz o bridge
            if self._skip_audio_flush:
                logger.info(f"🚀 FS sender: SKIPPING flush (accepted via pattern match)")
                # Limpar fila sem enviar
                while not self._fs_audio_queue.empty():
                    try:
                        self._fs_audio_queue.get_nowait()
                    except Exception:
                        break
                self._pending_audio_bytes = 0
                self._audio_playback_done.set()
            else:
                # FLUSH: Enviar áudio restante para evitar cortes no final das frases
                try:
                    flush_buffer = bytearray()
                    
                    # 1. Adicionar batch_buffer local
                    if batch_buffer:
                        flush_buffer.extend(batch_buffer)
                    
                    # 2. Drenar a fila restante
                    while not self._fs_audio_queue.empty():
                        try:
                            chunk = self._fs_audio_queue.get_nowait()
                            flush_buffer.extend(chunk)
                        except Exception:
                            break
                    
                    # 3. Flush do AudioBuffer (áudio pendente de warmup)
                    remaining = self._fs_audio_buffer.flush()
                    if remaining:
                        flush_buffer.extend(remaining)
                    
                    # 4. Enviar tudo de uma vez
                    if flush_buffer and self._fs_ws:
                        flush_bytes = len(flush_buffer)
                        audio_msg = json.dumps({
                            "type": "streamAudio",
                            "data": {
                                "audioData": base64.b64encode(bytes(flush_buffer)).decode("utf-8"),
                                "audioDataType": "raw"
                            }
                        })
                        await self._fs_ws.send(audio_msg)
                        total_bytes_sent += flush_bytes
                        
                        # TRACKING DINÂMICO: Todo áudio foi enviado
                        self._pending_audio_bytes = 0
                        
                        # TAIL BUFFER DINÂMICO: Aguardar tempo proporcional ao áudio restante
                        # L16 @ 8kHz = 16 bytes/ms
                        # 
                        # MARGEM CRÍTICA: O áudio foi enviado para o FreeSWITCH via WebSocket,
                        # mas ainda precisa:
                        # 1. Ser processado pelo FreeSWITCH (~50ms)
                        # 2. Passar pelo buffer de jitter (~100ms)
                        # 3. Ser transmitido pela rede até o telefone (~100-200ms)
                        #
                        # Margem total: 500ms para garantir que o áudio seja reproduzido
                        # antes de qualquer ação que possa interromper a chamada.
                        tail_duration_ms = (flush_bytes / 16.0) + 500
                        logger.info(
                            f"🔊 FS sender: flushed {flush_bytes} bytes, "
                            f"waiting {tail_duration_ms:.0f}ms (dynamic tail buffer with 500ms margin)"
                        )
                        await asyncio.sleep(tail_duration_ms / 1000.0)
                    
                    # Sinalizar que todo áudio foi reproduzido
                    self._audio_playback_done.set()
                        
                except Exception as flush_err:
                    logger.debug(f"🔊 FS sender: flush error: {flush_err}")
            
            # Calcular e logar duração total do áudio
            # L16 @ 8kHz = 16 bytes/ms
            if total_bytes_sent > 0:
                total_duration_ms = total_bytes_sent / 16.0
                logger.info(
                    f"🔊 FS sender: TOTAL {total_bytes_sent} bytes ({total_duration_ms:.0f}ms) in {chunks_sent} batches"
                )
            else:
                logger.warning("🔊 FS sender: NO audio was sent to FreeSWITCH!")
