"""
Realtime WebSocket Server - Bridge FreeSWITCH ↔ AI Providers

Referências:
- .context/docs/architecture.md: voice-ai-realtime:8085 (WebSocket)
- .context/docs/data-flow.md: ws://localhost:8085/stream/{uuid}
- .context/agents/devops-specialist.md: Porta 8085
- openspec/changes/voice-ai-realtime/design.md: Decision 2 (Protocol)
"""

import asyncio
import base64
import json
import logging
import os
from typing import Optional

import websockets
from websockets.asyncio.server import ServerConnection, serve

from .session import RealtimeSessionConfig
from .session_manager import get_session_manager
from .utils.metrics import get_metrics

logger = logging.getLogger(__name__)

# 20ms @ 16kHz PCM16 mono:
# 16000 samples/sec * 2 bytes/sample = 32000 bytes/sec
# 20ms => 640 bytes
PCM16_16K_CHUNK_BYTES = 640
PCM16_CHUNK_MS = 20

# Warmup: acumular N chunks antes de começar a enviar (evita stuttering inicial)
# Ref: os11k/freeswitch-elevenlabs-bridge usa BUFFER_WARMUP_CHUNKS = 10 (200ms)
BUFFER_WARMUP_CHUNKS = int(os.getenv("FS_WARMUP_CHUNKS", "10"))

# Fallback streamAudio (base64) usa frames maiores para reduzir overhead de arquivos
STREAMAUDIO_FRAME_MS = int(os.getenv("FS_STREAMAUDIO_FRAME_MS", "200"))
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
        
        # Parsear path: /stream/{domain_uuid}/{call_uuid}
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "stream":
            logger.warning(f"Invalid path: {path}")
            await websocket.close(1008, "Invalid path")
            return
        
        domain_uuid = parts[1]
        call_uuid = parts[2]
        
        # Log estruturado conforme backend-specialist.md
        logger.info("WebSocket connection received", extra={
            "domain_uuid": domain_uuid,
            "call_uuid": call_uuid,
            "path": path,
        })
        
        try:
            await self._handle_session(websocket, domain_uuid, call_uuid)
        except Exception as e:
            logger.error(f"Session error: {e}", extra={
                "domain_uuid": domain_uuid,
                "call_uuid": call_uuid,
            })
        finally:
            await websocket.close()
    
    async def _handle_session(
        self,
        websocket: ServerConnection,
        domain_uuid: str,
        call_uuid: str,
    ) -> None:
        """Gerencia uma sessão de chamada."""
        manager = get_session_manager()
        metrics = get_metrics()
        session = None
        caller_id = ""
        
        # Criar sessão imediatamente (mod_audio_stream não envia metadata)
        # Buscar secretária pela extensão de destino (8000)
        try:
            session = await self._create_session_from_db(
                domain_uuid=domain_uuid,
                call_uuid=call_uuid,
                caller_id=caller_id,
                websocket=websocket,
            )
            logger.info("Session created", extra={
                "domain_uuid": domain_uuid,
                "call_uuid": call_uuid,
                "session_active": session.is_active if session else False,
            })
        except Exception as e:
            logger.error(f"Failed to create session: {e}", extra={
                "domain_uuid": domain_uuid,
                "call_uuid": call_uuid,
            })
            await websocket.close(1011, f"Session creation failed: {e}")
            return
        
        # Log para debug - início do loop de mensagens
        logger.info("Starting message loop, waiting for audio from FreeSWITCH...", extra={
            "call_uuid": call_uuid,
            "session_active": session.is_active if session else False,
        })
        
        message_count = 0
        audio_bytes_total = 0
        
        try:
            async for message in websocket:
                message_count += 1
                
                # Log a cada 100 mensagens para não poluir muito
                if message_count <= 5 or message_count % 100 == 0:
                    logger.info(f"Message #{message_count} received", extra={
                        "call_uuid": call_uuid,
                        "message_type": "bytes" if isinstance(message, bytes) else "str",
                        "message_size": len(message) if message else 0,
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
                        logger.warning("Received audio but session is not active", extra={
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
            
            if session and session.is_active:
                await session.stop("connection_closed")
    
    async def _create_session_from_db(
        self,
        domain_uuid: str,
        call_uuid: str,
        caller_id: str,
        websocket: ServerConnection,
    ):
        """Cria sessão com configuração do banco."""
        from services.database import db
        
        pool = await db.get_pool()
        
        async with pool.acquire() as conn:
            # Buscar secretária pela extensão de destino (8000)
            # Primeiro tenta buscar pela extensão, depois fallback para qualquer secretária do domínio
            row = await conn.fetchrow(
                """
                SELECT 
                    s.voice_secretary_uuid as secretary_uuid,
                    s.secretary_name as name,
                    s.personality_prompt as system_prompt,
                    s.greeting_message as greeting,
                    s.farewell_message as farewell,
                    p.provider_name,
                    p.config as provider_config,
                    s.extension
                FROM v_voice_secretaries s
                LEFT JOIN v_voice_ai_providers p ON p.voice_ai_provider_uuid = s.realtime_provider_uuid
                WHERE s.domain_uuid = $1
                  AND s.is_enabled = true
                  AND s.processing_mode IN ('realtime', 'auto')
                ORDER BY 
                    CASE WHEN s.extension = '8000' THEN 0 ELSE 1 END,
                    s.insert_date DESC
                LIMIT 1
                """,
                domain_uuid
            )
            
            if not row:
                raise ValueError(f"No realtime secretary configured for domain {domain_uuid}")
            
            logger.info("Secretary found", extra={
                "domain_uuid": domain_uuid,
                "secretary_uuid": str(row["secretary_uuid"]),
                "secretary_name": row["name"],
                "extension": row["extension"],
                "provider": row["provider_name"],
            })
        
        # Configurar sessão
        config = RealtimeSessionConfig(
            domain_uuid=domain_uuid,
            call_uuid=call_uuid,
            caller_id=caller_id or "unknown",
            secretary_uuid=str(row["secretary_uuid"]),
            secretary_name=row["name"] or "Voice Secretary",
            provider_name=row["provider_name"] or "elevenlabs_conversational",
            system_prompt=row["system_prompt"] or "",
            greeting=row["greeting"],
            farewell=row["farewell"],
        )
        
        logger.debug("Session config created", extra={
            "domain_uuid": domain_uuid,
            "call_uuid": call_uuid,
            "secretary_uuid": config.secretary_uuid,
            "provider": config.provider_name,
        })
        
        # Callback para enviar áudio de volta ao FreeSWITCH
        #
        # Protocolo rawAudio + binário (mod_audio_stream v1.0.3+):
        # 1. Enviar uma vez: {"type":"rawAudio","data":{"sampleRate":16000}}
        # 2. Depois enviar chunks BINÁRIOS de 640 bytes (20ms @16kHz) com pacing 20ms
        # 3. Warmup de 10 chunks (200ms) antes de começar a enviar
        #
        # Ref: https://github.com/os11k/freeswitch-elevenlabs-bridge/blob/main/server.js
        audio_out_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        pending = bytearray()
        sender_task: Optional[asyncio.Task] = None
        format_sent = False
        playback_mode = os.getenv("FS_PLAYBACK_MODE", "rawAudio").lower()
        allow_streamaudio_fallback = os.getenv("FS_STREAMAUDIO_FALLBACK", "true").lower() in ("1", "true", "yes")
        if playback_mode not in ("rawaudio", "streamaudio"):
            playback_mode = "rawaudio"

        async def _send_rawaudio_header() -> bool:
            nonlocal format_sent
            if format_sent:
                return True
            try:
                format_msg = json.dumps({
                    "type": "rawAudio",
                    "data": {
                        "sampleRate": 16000
                    }
                })
                await websocket.send(format_msg)
                format_sent = True
                logger.info("Audio format sent to FreeSWITCH (rawAudio)", extra={"call_uuid": call_uuid})
                return True
            except Exception as e:
                logger.warning(f"Failed to send rawAudio header: {e}", extra={"call_uuid": call_uuid})
                return False

        async def _send_streamaudio_frame(frame_bytes: bytes) -> None:
            payload = json.dumps({
                "type": "streamAudio",
                "data": {
                    "audioDataType": "raw",
                    "sampleRate": 16000,
                    "audioData": base64.b64encode(frame_bytes).decode("utf-8"),
                }
            })
            await websocket.send(payload)
            try:
                metrics.record_audio(call_uuid, "out", len(frame_bytes))
            except Exception:
                pass
            await asyncio.sleep(STREAMAUDIO_FRAME_MS / 1000.0)

        async def _sender_loop_rawaudio() -> None:
            """
            Envia áudio para o FreeSWITCH usando protocolo rawAudio + binário.
            Fallback para streamAudio quando rawAudio falhar (opcional).
            """
            nonlocal playback_mode
            try:
                buffered_chunks: list[bytes] = []
                streaming_started = False
                streamaudio_buffer = bytearray()

                while True:
                    chunk = await audio_out_queue.get()
                    if chunk is None:
                        # Flush remaining chunks
                        for remaining in buffered_chunks:
                            await websocket.send(remaining)
                            await asyncio.sleep(PCM16_CHUNK_MS / 1000.0)
                        if streamaudio_buffer:
                            await _send_streamaudio_frame(bytes(streamaudio_buffer))
                        return

                    # rawAudio header
                    if playback_mode == "rawaudio":
                        header_ok = await _send_rawaudio_header()
                        if not header_ok and allow_streamaudio_fallback:
                            playback_mode = "streamaudio"
                            logger.warning("Switching to streamAudio fallback (header failed)", extra={"call_uuid": call_uuid})

                    if playback_mode == "rawaudio":
                        # Acumular chunks para warmup
                        buffered_chunks.append(chunk)

                        # Iniciar streaming após warmup
                        if not streaming_started and len(buffered_chunks) >= BUFFER_WARMUP_CHUNKS:
                            streaming_started = True
                            logger.debug(
                                f"Warmup complete ({BUFFER_WARMUP_CHUNKS} chunks), starting playback",
                                extra={"call_uuid": call_uuid},
                            )

                        # Enviar chunks com pacing
                        if streaming_started:
                            while buffered_chunks:
                                chunk_to_send = buffered_chunks.pop(0)
                                try:
                                    await websocket.send(chunk_to_send)
                                    try:
                                        metrics.record_audio(call_uuid, "out", len(chunk_to_send))
                                    except Exception:
                                        pass
                                    await asyncio.sleep(PCM16_CHUNK_MS / 1000.0)
                                except Exception as e:
                                    if allow_streamaudio_fallback:
                                        playback_mode = "streamaudio"
                                        streamaudio_buffer.extend(chunk_to_send)
                                        logger.warning(
                                            f"Switching to streamAudio fallback (send failed): {e}",
                                            extra={"call_uuid": call_uuid},
                                        )
                                        break
                                    raise

                            # Continuar recebendo e enviando em tempo real
                            while playback_mode == "rawaudio":
                                try:
                                    c = await asyncio.wait_for(audio_out_queue.get(), timeout=0.5)
                                except asyncio.TimeoutError:
                                    # Sem mais áudio, sair do loop de streaming contínuo
                                    break

                                if c is None:
                                    return

                                try:
                                    await websocket.send(c)
                                    try:
                                        metrics.record_audio(call_uuid, "out", len(c))
                                    except Exception:
                                        pass
                                    await asyncio.sleep(PCM16_CHUNK_MS / 1000.0)
                                except Exception as e:
                                    if allow_streamaudio_fallback:
                                        playback_mode = "streamaudio"
                                        streamaudio_buffer.extend(c)
                                        logger.warning(
                                            f"Switching to streamAudio fallback (send failed): {e}",
                                            extra={"call_uuid": call_uuid},
                                        )
                                        break
                                    raise

                            # Reset para próximo burst de áudio
                            streaming_started = False

                    if playback_mode == "streamaudio":
                        streamaudio_buffer.extend(chunk)
                        while len(streamaudio_buffer) >= STREAMAUDIO_FRAME_BYTES:
                            frame = bytes(streamaudio_buffer[:STREAMAUDIO_FRAME_BYTES])
                            del streamaudio_buffer[:STREAMAUDIO_FRAME_BYTES]
                            await _send_streamaudio_frame(frame)

            except websockets.exceptions.ConnectionClosed:
                logger.debug("WebSocket closed during audio playback", extra={"call_uuid": call_uuid})
            except Exception as e:
                logger.error(
                    f"Error in FreeSWITCH playback sender loop: {e}",
                    exc_info=True,
                    extra={"call_uuid": call_uuid},
                )

        async def send_audio(audio_bytes: bytes):
            """
            Recebe áudio do provider (PCM16) e enfileira em chunks de 20ms (640 bytes).
            """
            nonlocal sender_task
            try:
                if not audio_bytes:
                    return

                if sender_task is None:
                    sender_task = asyncio.create_task(_sender_loop_rawaudio())
                    logger.info("FreeSWITCH playback sender started (rawAudio+binary)", extra={"call_uuid": call_uuid})

                pending.extend(audio_bytes)

                while len(pending) >= PCM16_16K_CHUNK_BYTES:
                    chunk = bytes(pending[:PCM16_16K_CHUNK_BYTES])
                    del pending[:PCM16_16K_CHUNK_BYTES]
                    await audio_out_queue.put(chunk)

            except Exception as e:
                logger.error(
                    f"Error queueing audio for FreeSWITCH (rawAudio): {e}",
                    exc_info=True,
                    extra={"call_uuid": call_uuid},
                )
        
        # Criar sessão via manager
        manager = get_session_manager()
        session = await manager.create_session(
            config=config,
            on_audio_output=send_audio,
        )
        
        return session


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
