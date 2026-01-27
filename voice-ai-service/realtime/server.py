"""
Realtime WebSocket Server - Bridge FreeSWITCH ↔ AI Providers

Referências:
- .context/docs/architecture.md: voice-ai-realtime:8085 (WebSocket)
- .context/docs/data-flow.md: ws://localhost:8085/stream/{uuid}
- .context/agents/devops-specialist.md: Porta 8085
- openspec/changes/voice-ai-realtime/design.md: Decision 2 (Protocol)
"""

import asyncio
import contextlib
import base64
import json
import logging
import os
import time
from typing import Dict, List, Optional

import websockets
from websockets.asyncio.server import ServerConnection, serve

from .session import RealtimeSessionConfig
from .session_manager import get_session_manager
from .utils.metrics import get_metrics
from .utils.audio_pacing import AudioPacer, create_pcmu_pacer, create_l16_pacer
from .config_loader import (
    get_config_loader,
    build_transfer_context,
    build_transfer_tools_schema,
    validate_transfer_config,
)
from .handlers.time_condition_checker import (
    get_time_condition_checker,
    TimeConditionStatus,
)

logger = logging.getLogger(__name__)


def _parse_bool(value, default: bool = True) -> bool:
    """
    Converte valor para booleano de forma segura.
    
    FusionPBX pode salvar booleanos como 'true'/'false' strings.
    PostgreSQL retorna bool nativo via asyncpg.
    
    Args:
        value: Valor a converter (bool, str, int, None)
        default: Valor padrão se None
        
    Returns:
        bool
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes', 't')
    return default


def _parse_max_tokens(value, default: int = 4096) -> Optional[int]:
    """
    Converte max_response_output_tokens para int ou None (infinito).
    
    OpenAI Realtime aceita:
    - Número inteiro (ex: 4096)
    - "inf" para tokens ilimitados (passa como None na API)
    
    Args:
        value: Valor a converter (str, int, None)
        default: Valor padrão se inválido
        
    Returns:
        int ou None (para infinito)
    """
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value_lower = value.strip().lower()
        if value_lower in ('inf', 'infinite', 'infinity', 'none', ''):
            return None  # OpenAI interpreta None como infinito
        try:
            return int(value_lower)
        except ValueError:
            return default
    return default


def _parse_guardrails_topics(value) -> Optional[List[str]]:
    """
    Converte texto de tópicos proibidos em lista.
    
    No frontend, tópicos são separados por newline.
    Ex: "política\nreligião\nconcorrentes" -> ["política", "religião", "concorrentes"]
    
    Args:
        value: Texto com tópicos (um por linha) ou None
        
    Returns:
        Lista de tópicos ou None
    """
    if value is None or value == "":
        return None
    
    if isinstance(value, list):
        return [str(t).strip() for t in value if str(t).strip()]
    
    if isinstance(value, str):
        # Tópicos separados por newline (formato do frontend)
        topics = [t.strip() for t in value.split("\n") if t.strip()]
        return topics if topics else None
    
    return None


def _parse_business_info(value) -> Dict[str, str]:
    """
    Converte business_info do banco para dict.
    
    O banco PostgreSQL retorna JSONB como:
    - dict (asyncpg nativo)
    - str JSON (alguns drivers)
    
    Args:
        value: JSONB do banco (dict, str, ou None)
        
    Returns:
        Dict com informações da empresa
    """
    if value is None:
        return {}
    
    if isinstance(value, dict):
        return value
    
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    
    return {}


# 20ms @ 16kHz PCM16 mono:
# 16000 samples/sec * 2 bytes/sample = 32000 bytes/sec
# 20ms => 640 bytes
PCM16_16K_CHUNK_BYTES = 640
PCM16_CHUNK_MS = 20

# Warmup: acumular N chunks antes de começar a enviar (evita stuttering inicial)
# Ref: os11k/freeswitch-elevenlabs-bridge usa 10 chunks (200ms)

# Fallback streamAudio (base64) usa frames maiores para reduzir overhead de arquivos
# IMPORTANTE: Frames muito pequenos causam áudio picotado (gaps entre playbacks)
# Recomendado: 1000ms+ para evitar stuttering
STREAMAUDIO_FRAME_MS = int(os.getenv("FS_STREAMAUDIO_FRAME_MS", "1000"))
STREAMAUDIO_FRAME_BYTES = PCM16_16K_CHUNK_BYTES * max(1, STREAMAUDIO_FRAME_MS // 20)


class RealtimeServer:
    """
    WebSocket server para bridge FreeSWITCH ↔ AI.
    
    URL Pattern: ws://bridge:8085/stream/{domain_uuid}/{call_uuid}
    
    Conforme openspec/changes/voice-ai-realtime/design.md (Decision 2).
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8085,
        db_pool=None,
    ):
        self.host = host
        self.port = port
        self.db_pool = db_pool
        self._server = None
        self._running = False
    
    async def start(self) -> None:
        """Inicia o servidor WebSocket."""
        self._running = True
        
        # Pré-inicializar pool do banco para evitar delay na primeira chamada
        try:
            from services.database import db
            pool = await db.get_pool()
            logger.info(f"Database pool pre-initialized (min={pool.get_min_size()}, max={pool.get_max_size()})")
        except Exception as e:
            logger.warning(f"Failed to pre-initialize database pool: {e}")
        
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        )
        
        logger.info(f"Realtime WebSocket server started on ws://{self.host}:{self.port}")
    
    async def stop(self) -> None:
        """Para o servidor."""
        self._running = False
        
        # Parar todas as sessões
        manager = get_session_manager()
        await manager.stop_all_sessions("server_shutdown")
        
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        
        logger.info("Realtime WebSocket server stopped")
    
    async def serve_forever(self) -> None:
        """Executa o servidor indefinidamente."""
        await self.start()
        
        try:
            await asyncio.Future()  # Run forever
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
    
    async def _handle_connection(self, websocket: ServerConnection) -> None:
        """
        Handler para novas conexões WebSocket do FreeSWITCH.
        
        URL esperada: /stream/{domain_uuid}/{call_uuid}
        """
        path = websocket.request.path if hasattr(websocket, 'request') else ""
        
        # Health Check Endpoint
        if path == "/health":
            # Aceita handshake e fecha imediatamente com código normal (1000)
            await websocket.close(1000, "OK")
            return
        
        # Parsear path: /stream/{secretary_uuid}/{call_uuid}/{caller_id}
        # caller_id é opcional para compatibilidade com versões antigas
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "stream":
            logger.warning(f"Invalid path: {path}")
            await websocket.close(1008, "Invalid path")
            return
        
        secretary_uuid = parts[1]
        call_uuid = parts[2]
        caller_id = parts[3] if len(parts) > 3 else "unknown"
        
        # Log estruturado conforme backend-specialist.md
        logger.info("WebSocket connection received", extra={
            "secretary_uuid": secretary_uuid,
            "call_uuid": call_uuid,
            "caller_id": caller_id,
            "path": path,
        })
        
        try:
            await self._handle_session(websocket, secretary_uuid, call_uuid, caller_id)
        except Exception as e:
            logger.error(f"Session error: {e}", extra={
                "secretary_uuid": secretary_uuid,
                "call_uuid": call_uuid,
            })
        finally:
            await websocket.close()
    
    async def _handle_session(
        self,
        websocket: ServerConnection,
        secretary_uuid: str,
        call_uuid: str,
        caller_id: str,
    ) -> None:
        """Gerencia uma sessão de chamada."""
        manager = get_session_manager()
        metrics = get_metrics()
        session = None
        cleanup_playback = None

        # Reusar sessão se já existir (reconexão do WS durante transfer)
        existing = manager.get_session(call_uuid)
        if existing and existing.is_active:
            logger.info("WS reconectado - reutilizando sessão", extra={
                "call_uuid": call_uuid,
                "secretary_uuid": secretary_uuid,
            })
            session = existing
            send_audio, send_audio_pcmu, clear_playback, flush_audio, cleanup_playback, pcmu_passthrough = self._build_audio_handlers(websocket, call_uuid)
            session.update_audio_handlers(
                on_audio_output=send_audio,
                on_audio_output_pcmu=send_audio_pcmu,
                on_barge_in=clear_playback,
                on_transfer=clear_playback,
                on_audio_done=flush_audio,
            )
            # Atualizar flag de passthrough na sessão
            session._pcmu_passthrough_enabled = pcmu_passthrough
        else:
            # Criar sessão imediatamente (mod_audio_stream não envia metadata)
            # caller_id agora é recebido via URL
            try:
                session, cleanup_playback = await self._create_session_from_db(
                    secretary_uuid=secretary_uuid,
                    call_uuid=call_uuid,
                    caller_id=caller_id,
                    websocket=websocket,
                )
                logger.info("Session created", extra={
                    "secretary_uuid": secretary_uuid,
                    "call_uuid": call_uuid,
                    "session_active": session.is_active if session else False,
                })
            except Exception as e:
                logger.error(f"Failed to create session: {e}", extra={
                    "secretary_uuid": secretary_uuid,
                    "call_uuid": call_uuid,
                })
                await websocket.close(1011, f"Session creation failed: {e}")
                return
        
        # Log para debug - início do loop de mensagens
        logger.info("Starting message loop, waiting for audio from FreeSWITCH...", extra={
            "call_uuid": call_uuid,
            "session_active": session.is_active if session else False,
            "provider": session.config.provider_name if session else "none",
        })
        
        message_count = 0
        audio_bytes_total = 0
        last_message_time = asyncio.get_event_loop().time()
        
        try:
            # Verificar estado do WebSocket antes de entrar no loop
            # Nota: websockets >= 12.0 usa close_code ao invés de closed
            ws_closed = getattr(websocket, 'closed', None) or getattr(websocket, 'close_code', None) is not None
            if ws_closed:
                logger.error("WebSocket already closed before message loop!", extra={"call_uuid": call_uuid})
                return
            
            logger.debug(f"WebSocket ready for messages", extra={"call_uuid": call_uuid})
            
            async for message in websocket:
                message_count += 1
                
                # Log apenas no início e a cada 500 mensagens (reduzir ruído)
                if message_count == 1 or message_count % 500 == 0:
                    logger.debug(f"📥 [WS] Messages received: {message_count}", extra={
                        "call_uuid": call_uuid,
                    })
                
                # Processar mensagens
                if isinstance(message, bytes):
                    audio_bytes_total += len(message)
                    try:
                        metrics.record_audio(call_uuid, "in", len(message))
                    except Exception:
                        pass
                    # Áudio binário do FreeSWITCH
                    if session and session.is_active:
                        await session.handle_audio_input(message)
                    else:
                        # DEBUG: Áudio após sessão encerrar é normal durante hangup
                        logger.debug("Received audio but session is not active", extra={
                            "call_uuid": call_uuid,
                            "session_active": session.is_active if session else False,
                        })
                
                elif isinstance(message, str):
                    # Comando de texto (metadata ou comandos)
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type")
                        
                        if msg_type == "metadata":
                            # Metadata recebida após criação da sessão
                            caller_id = data.get("caller_id", caller_id)
                            logger.info("Metadata received", extra={
                                "call_uuid": call_uuid,
                                "caller_id": caller_id,
                            })
                        
                        elif msg_type == "dtmf":
                            logger.debug(f"DTMF: {data.get('digit')}", extra={"call_uuid": call_uuid})
                        
                        elif msg_type == "hangup":
                            logger.info("Hangup received", extra={"call_uuid": call_uuid})
                            if session:
                                await session.stop("hangup")
                            break
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON message: {message[:100]}", extra={"call_uuid": call_uuid})
        
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"WebSocket closed: {e}", extra={"call_uuid": call_uuid})
        
        finally:
            # Log de estatísticas finais
            logger.info(f"Session ended - Stats: {message_count} messages, {audio_bytes_total} audio bytes", extra={
                "call_uuid": call_uuid,
                "message_count": message_count,
                "audio_bytes_total": audio_bytes_total,
            })

            if cleanup_playback:
                await cleanup_playback()
            
            if session and session.is_active:
                if getattr(session, "in_transfer", False):
                    logger.warning(
                        "WebSocket closed during transfer/handoff - mantendo sessão ativa",
                        extra={"call_uuid": call_uuid}
                    )
                else:
                    await session.stop("connection_closed")

    def _build_audio_handlers(
        self,
        websocket: ServerConnection,
        call_uuid: str,
    ):
        """
        Cria handlers de áudio ligados a uma conexão WS específica.
        Usado tanto na criação da sessão quanto em reconexões.
        """
        audio_out_queue: asyncio.Queue[Optional[tuple[int, bytes]]] = asyncio.Queue()
        pending = bytearray()
        sender_task: Optional[asyncio.Task] = None
        cleanup_started = False
        format_sent = False
        playback_generation = 0
        playback_lock = asyncio.Lock()
        playback_mode = os.getenv("FS_PLAYBACK_MODE", "rawAudio").lower()
        allow_streamaudio_fallback = os.getenv("FS_STREAMAUDIO_FALLBACK", "true").lower() in ("1", "true", "yes")

        # Determinar sample rate e chunk size para OUTPUT
        fs_sample_rate = 8000
        fs_chunk_size = 320   # L16: 8000 samples/s * 0.020s * 2 bytes/sample
        logger.info(
            f"Audio output format: L16 PCM @ {fs_sample_rate}Hz, {fs_chunk_size}B/chunk",
            extra={"call_uuid": call_uuid}
        )

        # Forçar streamAudio para compatibilidade
        if playback_mode not in ("rawaudio", "streamaudio"):
            playback_mode = "streamaudio"
        playback_mode = "streamaudio"

        # Calcular tamanho do frame streamAudio
        streamaudio_frame_bytes = int(fs_sample_rate * 2 * STREAMAUDIO_FRAME_MS / 1000)
        logger.info(
            f"Playback mode: {playback_mode}, frame_size: {streamaudio_frame_bytes}B ({STREAMAUDIO_FRAME_MS}ms)",
            extra={"call_uuid": call_uuid}
        )

        async def _send_rawaudio_header() -> bool:
            nonlocal format_sent
            if format_sent:
                return True
            try:
                format_msg = json.dumps({
                    "type": "rawAudio",
                    "data": {"sampleRate": fs_sample_rate}
                })
                await websocket.send(format_msg)
                format_sent = True
                logger.info(
                    f"Audio format sent to FreeSWITCH (rawAudio @ {fs_sample_rate}Hz)",
                    extra={"call_uuid": call_uuid}
                )
                return True
            except Exception as e:
                logger.warning(f"Failed to send rawAudio header: {e}", extra={"call_uuid": call_uuid})
                return False

        _metrics = get_metrics()

        async def _send_streamaudio_chunk(chunk_bytes: bytes) -> None:
            payload = json.dumps({
                "type": "streamAudio",
                "data": {
                    "audioDataType": "raw",
                    "sampleRate": fs_sample_rate,
                    "audioData": base64.b64encode(chunk_bytes).decode("utf-8"),
                }
            })
            await websocket.send(payload)
            try:
                _metrics.record_audio(call_uuid, "out", len(chunk_bytes))
            except Exception:
                pass

        async def _send_streamaudio_pcmu_chunk(chunk_bytes: bytes) -> None:
            """NETPLAY v2.7: Envia PCMU diretamente sem conversão L16→PCMU.
            
            Elimina a cadeia de conversões G.711→L16→G.711 que causa robotização.
            mod_audio_stream detecta 'streamAudioPCMU' e escreve direto no canal.
            """
            payload = json.dumps({
                "type": "streamAudioPCMU",
                "data": {
                    "audioData": base64.b64encode(chunk_bytes).decode("utf-8"),
                }
            })
            await websocket.send(payload)
            try:
                _metrics.record_audio(call_uuid, "out", len(chunk_bytes))
            except Exception:
                pass

        async def _send_stop_audio() -> None:
            try:
                await websocket.send(json.dumps({"type": "stopAudio"}))
                logger.info("StopAudio sent to FreeSWITCH (barge-in)", extra={"call_uuid": call_uuid})
            except Exception as e:
                logger.warning(f"Failed to send stopAudio: {e}", extra={"call_uuid": call_uuid})

        async def _sender_loop_rawaudio() -> None:
            """Sender loop para L16 PCM - com pacing baseado em lead tracking.
            
            NETPLAY v2.10.5 (2026-01-27): Implementado AudioPacer para controle de timing
            
            Problema anterior (v2.10.0-v2.10.4):
            - Envio sem pacing causava bursts de pacotes
            - Bursts causavam "concealed samples" no cliente WebRTC
            - Concealed samples causam robotização do áudio
            
            Solução (inspirada em xtts-stream e dograh):
            - AudioPacer baseado em "lead tracking"
            - Calcula quanto estamos "à frente" do clock real
            - Só espera quando necessário para manter ritmo constante
            - Evita tanto bursts quanto delays excessivos
            
            Ref: realtime/utils/audio_pacing.py
            """
            nonlocal playback_mode
            
            # Criar pacer para L16 @ 8kHz (2 bytes/sample)
            # target_lead_ms=60 permite 60ms de buffer antes de esperar
            pacer = create_l16_pacer(sample_rate=8000, target_lead_ms=60.0)
            
            try:
                last_health_update = 0.0
                chunks_sent = 0
                batch_buffer = bytearray()
                warmup_complete = False

                # 8kHz L16 = 16 bytes/ms
                # Warmup: 640B = 40ms (mínimo para evitar micro-chunks)
                # Batch: 320B = 20ms (1 frame L16)
                warmup_bytes = 640   # 40ms @ 8kHz L16 (2 frames)
                batch_bytes = 320    # 20ms @ 8kHz L16 (1 frame)

                while True:
                    item = await audio_out_queue.get()
                    if item is None:
                        if batch_buffer:
                            await pacer.pace(len(batch_buffer))
                            await _send_streamaudio_chunk(bytes(batch_buffer))
                            pacer.on_sent(len(batch_buffer))
                        pacer.stop()
                        return

                    if isinstance(item[0], str) and item[0] == "STOP":
                        batch_buffer.clear()
                        warmup_complete = False
                        pacer.reset()  # Reset pacer para nova resposta
                        await _send_stop_audio()
                        continue

                    if isinstance(item[0], str) and item[0] == "FLUSH":
                        if batch_buffer:
                            remaining_bytes = len(batch_buffer)
                            await pacer.pace(remaining_bytes)
                            await _send_streamaudio_chunk(bytes(batch_buffer))
                            pacer.on_sent(remaining_bytes)

                            remaining_duration_ms = (remaining_bytes / 16.0) + 50
                            logger.debug(
                                f"FLUSH: sent {remaining_bytes} bytes, waiting {remaining_duration_ms:.0f}ms tail buffer",
                                extra={"call_uuid": call_uuid}
                            )
                            await asyncio.sleep(remaining_duration_ms / 1000.0)
                            batch_buffer.clear()
                        continue

                    generation, chunk = item
                    if generation != playback_generation:
                        continue

                    batch_buffer.extend(chunk)

                    if not warmup_complete:
                        if len(batch_buffer) >= warmup_bytes:
                            warmup_complete = True
                            pacer.start()  # Iniciar pacer após warmup
                            logger.info(
                                f"Streaming warmup complete ({len(batch_buffer)} bytes), pacer started",
                                extra={"call_uuid": call_uuid}
                            )
                        else:
                            continue

                    # Enviar frames com pacing controlado
                    while len(batch_buffer) >= batch_bytes:
                        chunk_to_send = bytes(batch_buffer[:batch_bytes])
                        del batch_buffer[:batch_bytes]
                        
                        # Pace: espera se estivermos muito à frente do clock real
                        await pacer.pace(len(chunk_to_send))
                        await _send_streamaudio_chunk(chunk_to_send)
                        pacer.on_sent(len(chunk_to_send))
                        
                        chunks_sent += 1

                        if chunks_sent == 1:
                            logger.info("Streaming playback started (with pacer)", extra={"call_uuid": call_uuid})

                    now = time.time()
                    if now - last_health_update >= 1.0:
                        session_metrics = _metrics.get_session_metrics(call_uuid)
                        if session_metrics:
                            health_score = 100.0 - min(30.0, session_metrics.avg_latency_ms / 50.0)
                            _metrics.update_health_score(call_uuid, health_score)
                        last_health_update = now

            except asyncio.CancelledError:
                pacer.stop()
                logger.debug("Playback sender loop cancelled", extra={"call_uuid": call_uuid})
            except websockets.exceptions.ConnectionClosed:
                pacer.stop()
                logger.debug("WebSocket closed during audio playback", extra={"call_uuid": call_uuid})
            except Exception as e:
                pacer.stop()
                logger.error(
                    f"Error in FreeSWITCH playback sender loop: {e}",
                    exc_info=True,
                    extra={"call_uuid": call_uuid},
                )

        async def send_audio(audio_bytes: bytes):
            nonlocal sender_task
            try:
                if not audio_bytes:
                    return

                if sender_task is None:
                    sender_task = asyncio.create_task(_sender_loop_rawaudio())
                    logger.info(
                        f"FreeSWITCH playback sender started (mode={playback_mode})",
                        extra={"call_uuid": call_uuid}
                    )

                pending.extend(audio_bytes)

                while len(pending) >= fs_chunk_size:
                    chunk = bytes(pending[:fs_chunk_size])
                    del pending[:fs_chunk_size]
                    await audio_out_queue.put((playback_generation, chunk))

            except Exception as e:
                logger.error(
                    f"Error queueing audio for FreeSWITCH (rawAudio): {e}",
                    exc_info=True,
                    extra={"call_uuid": call_uuid},
                )

        # ========================================
        # NETPLAY v2.7: PCMU Passthrough
        # ========================================
        # Sender separado para PCMU direto (sem conversão L16→PCMU)
        # Elimina a cadeia G.711→L16→G.711 que causa robotização
        pcmu_out_queue: asyncio.Queue[Optional[tuple[int, bytes]]] = asyncio.Queue()
        pcmu_sender_task: Optional[asyncio.Task] = None
        pcmu_pending = bytearray()
        # NETPLAY v2.10: Desabilitado por padrão até confirmar que OpenAI suporta G.711 output
        # O áudio estava tocando em velocidade 0.5x, indicando sample rate mismatch
        # Se OpenAI ignora o formato e envia PCM16@24kHz, o passthrough causa áudio lento
        pcmu_passthrough_enabled = os.getenv("FS_PCMU_PASSTHROUGH", "false").lower() in ("1", "true", "yes")
        
        async def _sender_loop_pcmu() -> None:
            """Sender loop para PCMU passthrough - com pacing baseado em lead tracking.
            
            NETPLAY v2.10.5 (2026-01-27): Implementado AudioPacer para controle de timing
            
            Problema anterior (v2.10.0-v2.10.4):
            - Envio sem pacing causava bursts de pacotes
            - Bursts causavam "concealed samples" no cliente WebRTC
            - Concealed samples causam robotização do áudio
            
            Solução (inspirada em xtts-stream e dograh):
            - AudioPacer baseado em "lead tracking"
            - Calcula quanto estamos "à frente" do clock real
            - Só espera quando necessário para manter ritmo constante
            
            Ref: realtime/utils/audio_pacing.py
            """
            nonlocal playback_mode
            
            # Criar pacer para PCMU @ 8kHz (1 byte/sample)
            # target_lead_ms=60 permite 60ms de buffer antes de esperar
            pacer = create_pcmu_pacer(target_lead_ms=60.0)
            
            try:
                chunks_sent = 0
                batch_buffer = bytearray()
                warmup_complete = False
                
                # PCMU: 8 bytes/ms (8kHz * 1 byte/sample)
                # Warmup: 320B = 40ms (mínimo para evitar micro-chunks)
                # Batch: 160B = 20ms (1 frame PCMU)
                warmup_bytes = 320    # 40ms @ 8kHz PCMU (2 frames)
                batch_bytes = 160     # 20ms @ 8kHz PCMU (1 frame)
                
                while True:
                    item = await pcmu_out_queue.get()
                    if item is None:
                        if batch_buffer:
                            await pacer.pace(len(batch_buffer))
                            await _send_streamaudio_pcmu_chunk(bytes(batch_buffer))
                            pacer.on_sent(len(batch_buffer))
                        pacer.stop()
                        return
                    
                    if isinstance(item[0], str) and item[0] == "STOP":
                        batch_buffer.clear()
                        warmup_complete = False
                        pacer.reset()  # Reset pacer para nova resposta
                        await _send_stop_audio()
                        continue
                    
                    if isinstance(item[0], str) and item[0] == "FLUSH":
                        if batch_buffer:
                            await pacer.pace(len(batch_buffer))
                            await _send_streamaudio_pcmu_chunk(bytes(batch_buffer))
                            pacer.on_sent(len(batch_buffer))
                            batch_buffer.clear()
                        continue
                    
                    generation, chunk = item
                    if generation != playback_generation:
                        continue
                    
                    batch_buffer.extend(chunk)
                    
                    if not warmup_complete:
                        if len(batch_buffer) >= warmup_bytes:
                            warmup_complete = True
                            pacer.start()  # Iniciar pacer após warmup
                            logger.info(
                                f"PCMU passthrough warmup complete ({len(batch_buffer)} bytes), pacer started",
                                extra={"call_uuid": call_uuid}
                            )
                        else:
                            continue
                    
                    # Enviar frames com pacing controlado
                    while len(batch_buffer) >= batch_bytes:
                        chunk_to_send = bytes(batch_buffer[:batch_bytes])
                        del batch_buffer[:batch_bytes]
                        
                        # Pace: espera se estivermos muito à frente do clock real
                        await pacer.pace(len(chunk_to_send))
                        await _send_streamaudio_pcmu_chunk(chunk_to_send)
                        pacer.on_sent(len(chunk_to_send))
                        
                        chunks_sent += 1
                        
                        if chunks_sent == 1:
                            logger.info("PCMU passthrough playback started (with pacer)", extra={"call_uuid": call_uuid})
            
            except asyncio.CancelledError:
                pacer.stop()
                logger.debug("PCMU sender loop cancelled", extra={"call_uuid": call_uuid})
            except websockets.exceptions.ConnectionClosed:
                pacer.stop()
                logger.debug("WebSocket closed during PCMU playback", extra={"call_uuid": call_uuid})
            except Exception as e:
                pacer.stop()
                logger.error(
                    f"Error in PCMU passthrough sender loop: {e}",
                    exc_info=True,
                    extra={"call_uuid": call_uuid},
                )
        
        async def send_audio_pcmu(audio_bytes: bytes):
            """NETPLAY v2.7: Envia PCMU diretamente sem conversão.
            
            Usado quando OpenAI retorna G.711 e queremos passthrough para FreeSWITCH.
            """
            nonlocal pcmu_sender_task
            try:
                if not audio_bytes:
                    return
                
                if pcmu_sender_task is None:
                    pcmu_sender_task = asyncio.create_task(_sender_loop_pcmu())
                    logger.info(
                        "PCMU passthrough sender started",
                        extra={"call_uuid": call_uuid}
                    )
                
                pcmu_pending.extend(audio_bytes)
                
                # PCMU: 160 bytes = 20ms @ 8kHz
                pcmu_chunk_size = 160
                while len(pcmu_pending) >= pcmu_chunk_size:
                    chunk = bytes(pcmu_pending[:pcmu_chunk_size])
                    del pcmu_pending[:pcmu_chunk_size]
                    await pcmu_out_queue.put((playback_generation, chunk))
            
            except Exception as e:
                logger.error(
                    f"Error queueing PCMU audio: {e}",
                    exc_info=True,
                    extra={"call_uuid": call_uuid},
                )

        async def clear_playback(_: str) -> None:
            nonlocal playback_generation
            async with playback_lock:
                playback_generation += 1
                pending.clear()
                pcmu_pending.clear()
                try:
                    while True:
                        audio_out_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    while True:
                        pcmu_out_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                if sender_task is not None:
                    await audio_out_queue.put(("STOP", playback_generation))
                if pcmu_sender_task is not None:
                    await pcmu_out_queue.put(("STOP", playback_generation))

        async def flush_audio():
            await audio_out_queue.put(("FLUSH", playback_generation))
            if pcmu_sender_task is not None:
                await pcmu_out_queue.put(("FLUSH", playback_generation))

        async def cleanup_playback() -> None:
            nonlocal sender_task, pcmu_sender_task, cleanup_started
            if cleanup_started:
                return
            cleanup_started = True

            # Cleanup L16 sender
            if sender_task is not None:
                try:
                    await audio_out_queue.put(None)
                except Exception:
                    pass

                try:
                    await asyncio.wait_for(sender_task, timeout=1.0)
                except asyncio.TimeoutError:
                    sender_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender_task
                except Exception:
                    pass
                finally:
                    sender_task = None
            
            # Cleanup PCMU sender
            if pcmu_sender_task is not None:
                try:
                    await pcmu_out_queue.put(None)
                except Exception:
                    pass

                try:
                    await asyncio.wait_for(pcmu_sender_task, timeout=1.0)
                except asyncio.TimeoutError:
                    pcmu_sender_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pcmu_sender_task
                except Exception:
                    pass
                finally:
                    pcmu_sender_task = None

        return send_audio, send_audio_pcmu, clear_playback, flush_audio, cleanup_playback, pcmu_passthrough_enabled
    
    async def _create_session_from_db(
        self,
        secretary_uuid: str,
        call_uuid: str,
        caller_id: str,
        websocket: ServerConnection,
    ):
        """Cria sessão com configuração do banco."""
        from services.database import db
        
        pool = await db.get_pool()
        
        async with pool.acquire() as conn:
            # Buscar secretária diretamente pelo UUID (passado na URL)
            row = await conn.fetchrow(
                """
                SELECT 
                    s.voice_secretary_uuid as secretary_uuid,
                    s.domain_uuid,
                    s.secretary_name as name,
                    s.personality_prompt as system_prompt,
                    s.greeting_message as greeting,
                    s.farewell_message as farewell,
                    s.farewell_keywords as farewell_keywords,
                    p.provider_name,
                    p.config as provider_config,
                    s.extension,
                    s.max_turns,
                    s.transfer_extension,
                    s.language,
                    s.tts_voice_id,
                    s.company_name,
                    -- Fallback Configuration
                    COALESCE(s.fallback_action, 'ticket') as fallback_action,
                    s.fallback_user_id,
                    COALESCE(s.fallback_priority, 'medium') as fallback_priority,
                    COALESCE(s.fallback_notify_enabled, true) as fallback_notify_enabled,
                    -- Handoff OmniPlay fields
                    COALESCE(s.handoff_enabled, true) as handoff_enabled,
                    COALESCE(s.handoff_timeout, 30) as handoff_timeout,
                    COALESCE(s.handoff_keywords, 'atendente,humano,pessoa,operador') as handoff_keywords,
                    s.handoff_queue_id,
                    COALESCE(s.handoff_tool_fallback_enabled, true) as handoff_tool_fallback_enabled,
                    COALESCE(s.handoff_tool_timeout_seconds, 3) as handoff_tool_timeout_seconds,
                    COALESCE(s.fallback_ticket_enabled, true) as fallback_ticket_enabled,
                    COALESCE(s.presence_check_enabled, true) as presence_check_enabled,
                    s.omniplay_webhook_url,
                    s.omniplay_company_id,
                    -- Audio Configuration fields (defaults AUMENTADOS 2026-01-25)
                    COALESCE(s.audio_warmup_chunks, 30) as audio_warmup_chunks,
                    COALESCE(s.audio_warmup_ms, 600) as audio_warmup_ms,
                    COALESCE(s.audio_adaptive_warmup, true) as audio_adaptive_warmup,
                    COALESCE(s.jitter_buffer_min, 100) as jitter_buffer_min,
                    COALESCE(s.jitter_buffer_max, 300) as jitter_buffer_max,
                    COALESCE(s.jitter_buffer_step, 40) as jitter_buffer_step,
                    COALESCE(s.stream_buffer_size, 20) as stream_buffer_size,  -- 20ms default (NOT samples!)
                    -- Business Hours (Time Condition)
                    s.time_condition_uuid,
                    s.outside_hours_message,
                    -- Call Timeouts
                    COALESCE(s.idle_timeout_seconds, 30) as idle_timeout_seconds,
                    COALESCE(s.max_duration_seconds, 600) as max_duration_seconds,
                    -- Input Normalization
                    COALESCE(s.input_normalize_enabled, false) as input_normalize_enabled,
                    COALESCE(s.input_target_rms, 2000) as input_target_rms,
                    COALESCE(s.input_min_rms, 300) as input_min_rms,
                    COALESCE(s.input_max_gain, 3.0) as input_max_gain,
                    -- Call State logging/metrics
                    COALESCE(s.call_state_log_enabled, true) as call_state_log_enabled,
                    COALESCE(s.call_state_metrics_enabled, true) as call_state_metrics_enabled,
                    -- Unbridge behavior
                    COALESCE(s.unbridge_behavior, 'hangup') as unbridge_behavior,
                    s.unbridge_resume_message,
                    -- Hold return message (migration 032)
                    COALESCE(s.hold_return_message, 'Obrigado por aguardar.') as hold_return_message,
                    -- Silence Fallback
                    COALESCE(s.silence_fallback_enabled, false) as silence_fallback_enabled,
                    COALESCE(s.silence_fallback_seconds, 10) as silence_fallback_seconds,
                    COALESCE(s.silence_fallback_action, 'reprompt') as silence_fallback_action,
                    s.silence_fallback_prompt,
                    COALESCE(s.silence_fallback_max_retries, 2) as silence_fallback_max_retries,
                    -- VAD Configuration (migration 023)
                    -- high responde rápido, medium é balanceado, low é paciente
                    COALESCE(s.vad_type, 'semantic_vad') as vad_type,
                    COALESCE(s.vad_eagerness, 'high') as vad_eagerness,
                    -- Guardrails Configuration (migration 023)
                    COALESCE(s.guardrails_enabled, true) as guardrails_enabled,
                    s.guardrails_topics,
                    -- Announcement TTS Provider (migration 023)
                    COALESCE(s.announcement_tts_provider, 'elevenlabs') as announcement_tts_provider,
                    -- Push-to-talk tuning
                    s.ptt_rms_threshold,
                    s.ptt_hits,
                    -- Transfer Mode Configuration (migrations 013, 022)
                    COALESCE(s.transfer_announce_enabled, true) as transfer_announce_enabled,
                    COALESCE(s.transfer_realtime_enabled, false) as transfer_realtime_enabled,
                    s.transfer_realtime_prompt,
                    COALESCE(s.transfer_realtime_timeout, 15) as transfer_realtime_timeout,
                    -- Business Info (migration 031)
                    COALESCE(s.business_info, '{}'::jsonb) as business_info
                FROM v_voice_secretaries s
                LEFT JOIN v_voice_ai_providers p ON p.voice_ai_provider_uuid = s.realtime_provider_uuid
                WHERE s.voice_secretary_uuid = $1::uuid
                  AND s.enabled = true
                LIMIT 1
                """,
                secretary_uuid
            )
            
            if not row:
                raise ValueError(f"No secretary found with UUID {secretary_uuid}")
            
            # Extrair domain_uuid da row para uso posterior
            domain_uuid = str(row["domain_uuid"]) if row["domain_uuid"] else ""
            
            logger.info("Secretary found", extra={
                "domain_uuid": domain_uuid,
                "secretary_uuid": str(row["secretary_uuid"]),
                "secretary_name": row["name"],
                "extension": row["extension"],
                "provider": row["provider_name"],
            })
        
        # ========================================
        # Business Hours Check (Time Condition)
        # Ref: voice-ai-ivr/openspec/changes/intelligent-voice-handoff/tasks.md
        # ========================================
        time_condition_uuid = row.get("time_condition_uuid")
        
        if time_condition_uuid:
            try:
                time_checker = get_time_condition_checker()
                time_result = await time_checker.check(
                    domain_uuid=domain_uuid,
                    time_condition_uuid=str(time_condition_uuid)
                )
                
                logger.info("Business hours check", extra={
                    "call_uuid": call_uuid,
                    "domain_uuid": domain_uuid,
                    "time_condition_uuid": str(time_condition_uuid),
                    "is_open": time_result.is_open,
                    "status": time_result.status.value,
                    "message": time_result.message,
                })
                
                if not time_result.is_open:
                    # Fora do horário comercial
                    # Retornar None para sinalizar que deve criar ticket/callback
                    # O caller deve tratar isso apropriadamente
                    logger.warning(
                        "Call received outside business hours",
                        extra={
                            "call_uuid": call_uuid,
                            "domain_uuid": domain_uuid,
                            "secretary_name": row["name"],
                            "status": time_result.status.value,
                            "message": time_result.message,
                        }
                    )
                    
                    # Retornar configuração especial indicando fora do horário
                    # A sessão será criada mas com flag para executar fluxo de fora-do-horário
                    # Isso permite que o Voice AI informe o cliente e crie ticket
                    # ao invés de simplesmente recusar a chamada
                    
            except Exception as e:
                # Fail-open: em caso de erro, prosseguir normalmente
                logger.warning(
                    f"Error checking business hours, proceeding: {e}",
                    extra={
                        "call_uuid": call_uuid,
                        "domain_uuid": domain_uuid,
                    }
                )
                time_result = None
        else:
            time_result = None  # Sem restrição de horário
        
        # Configurar sessão (com overrides por provider/tenant)
        vad_threshold = float(os.getenv("REALTIME_VAD_THRESHOLD", "0.65"))
        silence_duration_ms = int(os.getenv("REALTIME_SILENCE_MS", "900"))
        prefix_padding_ms = int(os.getenv("REALTIME_PREFIX_PADDING_MS", "300"))
        max_response_output_tokens = _parse_max_tokens(os.getenv("REALTIME_MAX_OUTPUT_TOKENS", "4096"))
        # Voice: prioridade 1) banco (tts_voice_id), 2) env, 3) provider_config, 4) default
        voice = (row.get("tts_voice_id") or os.getenv("REALTIME_VOICE", "") or "").strip()
        # Language: prioridade 1) banco, 2) default
        language = row.get("language") or "pt-BR"
        fallback_providers_env = os.getenv("REALTIME_FALLBACK_PROVIDERS", "").strip()
        barge_in_enabled = os.getenv("REALTIME_BARGE_IN", "true").lower() in ("1", "true", "yes")
        tools = None

        # Provider config pode sobrescrever defaults
        provider_config_raw = row.get("provider_config")
        if isinstance(provider_config_raw, str):
            try:
                provider_config_raw = json.loads(provider_config_raw)
            except Exception:
                provider_config_raw = {}
        provider_config = provider_config_raw or {}

        if isinstance(provider_config, dict):
            vad_threshold = float(provider_config.get("vad_threshold", vad_threshold))
            silence_duration_ms = int(provider_config.get("silence_duration_ms", silence_duration_ms))
            prefix_padding_ms = int(provider_config.get("prefix_padding_ms", prefix_padding_ms))
            max_response_output_tokens = _parse_max_tokens(provider_config.get("max_response_output_tokens"), max_response_output_tokens or 4096)
            voice = str(provider_config.get("voice", voice or "alloy")).strip()
            barge_in_enabled = str(provider_config.get("barge_in_enabled", str(barge_in_enabled))).lower() in ("1", "true", "yes")
            fallback_providers_env = str(provider_config.get("fallback_providers", fallback_providers_env)).strip()
            tools_json = provider_config.get("tools_json")
            if tools_json:
                try:
                    tools = json.loads(tools_json) if isinstance(tools_json, str) else tools_json
                except Exception:
                    logger.warning("Invalid tools_json in provider_config", extra={"call_uuid": call_uuid})

        # Parse fallback providers
        fallback_providers = []
        if fallback_providers_env:
            try:
                if isinstance(fallback_providers_env, list):
                    fallback_providers = [str(p).strip() for p in fallback_providers_env if str(p).strip()]
                elif fallback_providers_env.startswith("["):
                    fallback_providers = [str(p).strip() for p in json.loads(fallback_providers_env) if str(p).strip()]
                else:
                    fallback_providers = [p.strip() for p in fallback_providers_env.split(",") if p.strip()]
            except Exception:
                logger.warning("Invalid fallback_providers format", extra={"call_uuid": call_uuid})

        # ========================================
        # Transfer Rules Integration
        # Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (5.1-5.2)
        # ========================================
        system_prompt_base = row["system_prompt"] or ""
        secretary_uuid = str(row["secretary_uuid"])
        
        # Carregar transfer_rules e construir contexto para o LLM
        config_loader = get_config_loader()
        transfer_context = ""
        
        if config_loader:
            try:
                transfer_rules = await config_loader.get_transfer_rules(
                    domain_uuid=domain_uuid,
                    secretary_uuid=secretary_uuid
                )
                
                if transfer_rules:
                    # Usar idioma da secretária configurado no banco
                    transfer_context = build_transfer_context(transfer_rules, language)
                    
                    # Adicionar tools de transfer se não existirem
                    if not tools:
                        tools = build_transfer_tools_schema()
                    else:
                        # Verificar se transfer_call já existe
                        tool_names = [t.get("function", {}).get("name") for t in tools if isinstance(t, dict)]
                        if "transfer_call" not in tool_names:
                            tools.extend(build_transfer_tools_schema())
                    
                    logger.info("Transfer rules injected into session", extra={
                        "domain_uuid": domain_uuid,
                        "secretary_uuid": secretary_uuid,
                        "rules_count": len(transfer_rules),
                        "call_uuid": call_uuid,
                    })
                    
            except Exception as e:
                logger.warning(f"Failed to load transfer rules: {e}", extra={
                    "domain_uuid": domain_uuid,
                    "secretary_uuid": secretary_uuid,
                    "call_uuid": call_uuid,
                })
        
        # ========================================
        # ADICIONAR FERRAMENTAS OBRIGATÓRIAS
        # ========================================
        # Importar definições de ferramentas
        from .session import (
            HANDOFF_FUNCTION_DEFINITION,
            END_CALL_FUNCTION_DEFINITION,
            HOLD_CALL_FUNCTION_DEFINITION,
            UNHOLD_CALL_FUNCTION_DEFINITION,
            CHECK_EXTENSION_FUNCTION_DEFINITION,
            LOOKUP_CUSTOMER_FUNCTION_DEFINITION,
            CHECK_APPOINTMENT_FUNCTION_DEFINITION,
            TAKE_MESSAGE_FUNCTION_DEFINITION,
        )
        
        # Inicializar tools se não existir
        if not tools:
            tools = []
        
        # Verificar nomes existentes
        tool_names = []
        for t in tools:
            if isinstance(t, dict):
                # Formato pode ser {"type": "function", "name": ...} ou {"function": {"name": ...}}
                name = t.get("name") or (t.get("function") or {}).get("name")
                if name:
                    tool_names.append(name)
        
        # Adicionar request_handoff se não existir
        if "request_handoff" not in tool_names:
            tools.append(HANDOFF_FUNCTION_DEFINITION)
        
        # Adicionar end_call se não existir
        if "end_call" not in tool_names:
            tools.append(END_CALL_FUNCTION_DEFINITION)
        
        # Adicionar take_message se não existir (OBRIGATÓRIO para recados)
        if "take_message" not in tool_names:
            tools.append(TAKE_MESSAGE_FUNCTION_DEFINITION)
        
        # ========================================
        # FERRAMENTAS DE CONTROLE DE CHAMADA
        # Disponíveis em todos os modos (usam ESL adapter)
        # Ref: openspec/changes/dual-mode-esl-websocket/
        # ========================================
        
        # Adicionar ferramentas de controle de chamada
        if "hold_call" not in tool_names:
            tools.append(HOLD_CALL_FUNCTION_DEFINITION)
        
        if "unhold_call" not in tool_names:
            tools.append(UNHOLD_CALL_FUNCTION_DEFINITION)
        
        if "check_extension_available" not in tool_names:
            tools.append(CHECK_EXTENSION_FUNCTION_DEFINITION)
        
        # Ferramentas opcionais via webhook OmniPlay
        if row.get("omniplay_webhook_url"):
            if "lookup_customer" not in tool_names:
                tools.append(LOOKUP_CUSTOMER_FUNCTION_DEFINITION)
            if "check_appointment" not in tool_names:
                tools.append(CHECK_APPOINTMENT_FUNCTION_DEFINITION)
        
        audio_mode = os.getenv("AUDIO_MODE", "websocket").lower()
        
        logger.info("Session tools configured", extra={
            "call_uuid": call_uuid,
            "audio_mode": audio_mode,
            "tool_count": len(tools),
            "tool_names": [t.get("name") or (t.get("function") or {}).get("name") for t in tools if isinstance(t, dict)],
        })
        
        # Combinar system_prompt base + transfer_context + business_info
        final_system_prompt = system_prompt_base
        if transfer_context:
            final_system_prompt = f"{system_prompt_base}\n{transfer_context}"
        
        # Incluir business_info no prompt para evitar chamadas desnecessárias à tool
        business_info = _parse_business_info(row.get("business_info"))
        if business_info:
            business_info_text = "\n\n# Informações da Empresa (USE DIRETAMENTE, NÃO PRECISA CHAMAR get_business_info)\n"
            for key, value in business_info.items():
                if value:
                    # Traduzir chaves para português
                    key_pt = {
                        "servicos": "Serviços",
                        "precos": "Preços/Planos",
                        "promocoes": "Promoções",
                        "horarios": "Horários",
                        "localizacao": "Endereço",
                        "contato": "Contato",
                        "sobre": "Sobre a empresa",
                        "geral": "Informações gerais",
                    }.get(key, key.title())
                    business_info_text += f"- {key_pt}: {value}\n"
            final_system_prompt = f"{final_system_prompt}\n{business_info_text}"

        # Parse handoff keywords from comma-separated string
        handoff_keywords_str = row.get("handoff_keywords") or "atendente,humano,pessoa,operador"
        handoff_keywords = [k.strip() for k in handoff_keywords_str.split(",") if k.strip()]
        
        # Parse farewell keywords from newline-separated string (configurável no frontend)
        # Cada região pode ter gírias diferentes (falou, valeu, flw, vlw, etc)
        farewell_keywords_str = row.get("farewell_keywords") or ""
        if farewell_keywords_str:
            # Keywords separadas por newline no frontend
            farewell_keywords = [k.strip().lower() for k in farewell_keywords_str.split("\n") if k.strip()]
        else:
            # Fallback para keywords padrão
            farewell_keywords = None  # Usará as keywords padrão no RealtimeSession
        
        # Validar configurações de transferência para detectar conflitos
        # Ref: voice-ai-ivr/docs/TRANSFER_SETTINGS_VS_RULES.md
        transfer_extension = row.get("transfer_extension") or "200"
        if config_loader and transfer_rules:
            config_warnings = validate_transfer_config(
                handoff_keywords=handoff_keywords,
                transfer_extension=transfer_extension,
                transfer_rules=transfer_rules,
                domain_uuid=domain_uuid,
                secretary_uuid=secretary_uuid,
            )
            if config_warnings:
                # Log individual warnings para facilitar debug
                for warning in config_warnings:
                    logger.warning(warning, extra={
                        "call_uuid": call_uuid,
                        "domain_uuid": domain_uuid,
                        "secretary_uuid": secretary_uuid,
                    })
        
        # Audio Configuration - extrair valores do banco ANTES de criar o config
        db_warmup_chunks = int(row.get("audio_warmup_chunks") or 30)  # AUMENTADO 2026-01-25
        db_warmup_ms = int(row.get("audio_warmup_ms") or 600)  # AUMENTADO 2026-01-25
        db_adaptive_warmup = _parse_bool(row.get("audio_adaptive_warmup"), default=True)
        db_jitter_min = int(row.get("jitter_buffer_min") or 100)
        db_jitter_max = int(row.get("jitter_buffer_max") or 300)
        db_jitter_step = int(row.get("jitter_buffer_step") or 40)
        db_stream_buffer = int(row.get("stream_buffer_size") or 20)  # 20ms default
        db_ptt_rms = row.get("ptt_rms_threshold")
        if db_ptt_rms is not None and int(db_ptt_rms) <= 0:
            db_ptt_rms = None
        db_ptt_hits = row.get("ptt_hits")
        if db_ptt_hits is not None and int(db_ptt_hits) <= 0:
            db_ptt_hits = None
        
        logger.info("Audio config from DB", extra={
            "call_uuid": call_uuid,
            "warmup_chunks": db_warmup_chunks,
            "warmup_ms": db_warmup_ms,
            "adaptive": db_adaptive_warmup,
            "jitter": f"{db_jitter_min}:{db_jitter_max}:{db_jitter_step}",
            "stream_buffer": db_stream_buffer,
        })
        
        config = RealtimeSessionConfig(
            domain_uuid=domain_uuid,
            call_uuid=call_uuid,
            caller_id=caller_id or "unknown",
            secretary_uuid=secretary_uuid,
            secretary_name=row["name"] or "Voice Secretary",
            company_name=row.get("company_name"),
            business_info=_parse_business_info(row.get("business_info")),
            provider_name=row["provider_name"] or "elevenlabs_conversational",
            system_prompt=final_system_prompt,
            greeting=row["greeting"],
            farewell=row["farewell"],
            farewell_keywords=farewell_keywords,
            vad_threshold=vad_threshold,
            silence_duration_ms=silence_duration_ms,
            prefix_padding_ms=prefix_padding_ms,
            max_response_output_tokens=max_response_output_tokens,
            voice=voice or "alloy",
            voice_id=row.get("tts_voice_id"),  # ElevenLabs voice_id para anúncios de transferência
            language=language,
            tools=tools,
            fallback_providers=fallback_providers,
            barge_in_enabled=barge_in_enabled,
            omniplay_webhook_url=row.get("omniplay_webhook_url"),
            # Handoff OmniPlay config
            handoff_enabled=_parse_bool(row.get("handoff_enabled"), default=True),
            handoff_timeout_ms=int(row.get("handoff_timeout", 30)) * 1000,  # seconds to ms
            handoff_keywords=handoff_keywords,
            handoff_max_ai_turns=int(row.get("max_turns", 20)),
            handoff_queue_id=row.get("handoff_queue_id"),
            handoff_tool_fallback_enabled=_parse_bool(row.get("handoff_tool_fallback_enabled"), default=True),
            handoff_tool_timeout_seconds=int(row.get("handoff_tool_timeout_seconds") or 3),
            omniplay_company_id=row.get("omniplay_company_id"),
            # Fallback Configuration (from database)
            fallback_ticket_enabled=_parse_bool(row.get("fallback_ticket_enabled"), default=True),
            fallback_action=row.get("fallback_action") or "ticket",
            fallback_user_id=row.get("fallback_user_id"),
            fallback_priority=row.get("fallback_priority") or "medium",
            fallback_notify_enabled=_parse_bool(row.get("fallback_notify_enabled"), default=True),
            presence_check_enabled=_parse_bool(row.get("presence_check_enabled"), default=True),
            # Audio Configuration
            audio_warmup_chunks=db_warmup_chunks,
            audio_warmup_ms=db_warmup_ms,
            audio_adaptive_warmup=db_adaptive_warmup,
            jitter_buffer_min=db_jitter_min,
            jitter_buffer_max=db_jitter_max,
            jitter_buffer_step=db_jitter_step,
            stream_buffer_size=db_stream_buffer,
            # Business Hours
            is_outside_business_hours=(
                time_result is not None and not time_result.is_open
            ),
            outside_hours_message=(
                row.get("outside_hours_message")
                or (time_result.message if time_result and not time_result.is_open else None)
                or "Estamos fora do horário de atendimento."
            ),
            # Call Timeouts (from database)
            idle_timeout_seconds=int(row.get("idle_timeout_seconds") or 30),
            max_duration_seconds=int(row.get("max_duration_seconds") or 600),
            # Input Normalization
            input_normalize_enabled=_parse_bool(row.get("input_normalize_enabled"), default=False),
            input_target_rms=int(row.get("input_target_rms") or 2000),
            input_min_rms=int(row.get("input_min_rms") or 300),
            input_max_gain=float(row.get("input_max_gain") or 3.0),
            # Call State logging/metrics
            call_state_log_enabled=_parse_bool(row.get("call_state_log_enabled"), default=True),
            call_state_metrics_enabled=_parse_bool(row.get("call_state_metrics_enabled"), default=True),
            # Unbridge behavior
            unbridge_behavior=row.get("unbridge_behavior") or "hangup",
            unbridge_resume_message=row.get("unbridge_resume_message"),
            # Hold return message
            hold_return_message=row.get("hold_return_message") or "Obrigado por aguardar.",
            # Silence Fallback
            silence_fallback_enabled=_parse_bool(row.get("silence_fallback_enabled"), default=False),
            silence_fallback_seconds=int(row.get("silence_fallback_seconds") or 10),
            silence_fallback_action=row.get("silence_fallback_action") or "reprompt",
            silence_fallback_prompt=row.get("silence_fallback_prompt"),
            silence_fallback_max_retries=int(row.get("silence_fallback_max_retries") or 2),
            # VAD Configuration (migration 023)
            vad_type=row.get("vad_type") or "semantic_vad",
            vad_eagerness=row.get("vad_eagerness") or "high",
            # Guardrails Configuration (migration 023)
            guardrails_enabled=_parse_bool(row.get("guardrails_enabled"), default=True),
            guardrails_topics=_parse_guardrails_topics(row.get("guardrails_topics")),
            # Transfer Mode Configuration (migrations 013, 022)
            transfer_announce_enabled=_parse_bool(row.get("transfer_announce_enabled"), default=True),
            transfer_realtime_enabled=_parse_bool(row.get("transfer_realtime_enabled"), default=False),
            transfer_realtime_prompt=row.get("transfer_realtime_prompt"),
            transfer_realtime_timeout=float(row.get("transfer_realtime_timeout") or 15),
            # Announcement TTS Provider (migration 023)
            announcement_tts_provider=row.get("announcement_tts_provider") or "elevenlabs",
            # Push-to-talk tuning
            ptt_rms_threshold=db_ptt_rms,
            ptt_hits=db_ptt_hits,
        )
        
        logger.debug("Session config created", extra={
            "domain_uuid": domain_uuid,
            "call_uuid": call_uuid,
            "secretary_uuid": config.secretary_uuid,
            "provider": config.provider_name,
        })
        
        # Handlers de áudio para este WebSocket
        send_audio, send_audio_pcmu, clear_playback, flush_audio, cleanup_playback, pcmu_passthrough = self._build_audio_handlers(websocket, call_uuid)
        
        # Criar sessão via manager
        manager = get_session_manager()
        session = await manager.create_session(
            config=config,
            on_audio_output=send_audio,
            on_audio_output_pcmu=send_audio_pcmu,
            on_barge_in=clear_playback,
            on_transfer=clear_playback,
            on_audio_done=flush_audio,
        )
        
        # Marcar flag de passthrough na sessão
        session._pcmu_passthrough_enabled = pcmu_passthrough
        
        return session, cleanup_playback


async def run_server(host: str = "0.0.0.0", port: int = 8085) -> None:
    """Função helper para rodar o servidor."""
    server = RealtimeServer(host=host, port=port)
    await server.serve_forever()


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8085
    asyncio.run(run_server(port=port))
