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
import time
from typing import Optional

import websockets
from websockets.asyncio.server import ServerConnection, serve

from .session import RealtimeSessionConfig
from .session_manager import get_session_manager
from .utils.metrics import get_metrics
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

# 20ms @ 16kHz PCM16 mono:
# 16000 samples/sec * 2 bytes/sample = 32000 bytes/sec
# 20ms => 640 bytes
PCM16_16K_CHUNK_BYTES = 640
PCM16_CHUNK_MS = 20

# Warmup: acumular N chunks antes de começar a enviar (evita stuttering inicial)
# Ref: os11k/freeswitch-elevenlabs-bridge usa 10 chunks (200ms)

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
        
        # Criar sessão imediatamente (mod_audio_stream não envia metadata)
        # caller_id agora é recebido via URL
        try:
            session = await self._create_session_from_db(
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
                    p.provider_name,
                    p.config as provider_config,
                    s.extension,
                    s.max_turns,
                    s.transfer_extension,
                    s.language,
                    -- Handoff OmniPlay fields
                    COALESCE(s.handoff_enabled, true) as handoff_enabled,
                    COALESCE(s.handoff_timeout, 30) as handoff_timeout,
                    COALESCE(s.handoff_keywords, 'atendente,humano,pessoa,operador') as handoff_keywords,
                    s.handoff_queue_id,
                    COALESCE(s.fallback_ticket_enabled, true) as fallback_ticket_enabled,
                    COALESCE(s.presence_check_enabled, true) as presence_check_enabled,
                    s.omniplay_webhook_url,
                    s.omniplay_company_id,
                    -- Audio Configuration fields
                    COALESCE(s.audio_warmup_chunks, 15) as audio_warmup_chunks,
                    COALESCE(s.audio_warmup_ms, 400) as audio_warmup_ms,
                    COALESCE(s.audio_adaptive_warmup, true) as audio_adaptive_warmup,
                    COALESCE(s.jitter_buffer_min, 100) as jitter_buffer_min,
                    COALESCE(s.jitter_buffer_max, 300) as jitter_buffer_max,
                    COALESCE(s.jitter_buffer_step, 40) as jitter_buffer_step,
                    COALESCE(s.stream_buffer_size, 20) as stream_buffer_size,  -- 20ms default (NOT samples!)
                    -- Business Hours (Time Condition)
                    s.time_condition_uuid
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
        max_response_output_tokens = int(os.getenv("REALTIME_MAX_OUTPUT_TOKENS", "4096"))
        voice = (os.getenv("REALTIME_VOICE", "") or "").strip()
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
            max_response_output_tokens = int(provider_config.get("max_response_output_tokens", max_response_output_tokens))
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
                    # Detectar idioma da secretária (fallback pt-BR)
                    language = "pt-BR"  # Pode ser extraído da config futuramente
                    
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
        
        audio_mode = os.getenv("AUDIO_MODE", "websocket").lower()
        
        logger.info("Session tools configured", extra={
            "call_uuid": call_uuid,
            "audio_mode": audio_mode,
            "tool_count": len(tools),
            "tool_names": [t.get("name") or (t.get("function") or {}).get("name") for t in tools if isinstance(t, dict)],
        })
        
        # Combinar system_prompt base + transfer_context
        final_system_prompt = system_prompt_base
        if transfer_context:
            final_system_prompt = f"{system_prompt_base}\n{transfer_context}"

        # Parse handoff keywords from comma-separated string
        handoff_keywords_str = row.get("handoff_keywords") or "atendente,humano,pessoa,operador"
        handoff_keywords = [k.strip() for k in handoff_keywords_str.split(",") if k.strip()]
        
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
        db_warmup_chunks = int(row.get("audio_warmup_chunks") or 15)
        db_warmup_ms = int(row.get("audio_warmup_ms") or 400)
        db_adaptive_warmup = row.get("audio_adaptive_warmup")
        if db_adaptive_warmup is None:
            db_adaptive_warmup = True
        elif isinstance(db_adaptive_warmup, str):
            db_adaptive_warmup = db_adaptive_warmup.lower() in ("true", "1", "yes")
        else:
            db_adaptive_warmup = bool(db_adaptive_warmup)
        db_jitter_min = int(row.get("jitter_buffer_min") or 100)
        db_jitter_max = int(row.get("jitter_buffer_max") or 300)
        db_jitter_step = int(row.get("jitter_buffer_step") or 40)
        db_stream_buffer = int(row.get("stream_buffer_size") or 20)  # 20ms default
        
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
            provider_name=row["provider_name"] or "elevenlabs_conversational",
            system_prompt=final_system_prompt,
            greeting=row["greeting"],
            farewell=row["farewell"],
            vad_threshold=vad_threshold,
            silence_duration_ms=silence_duration_ms,
            prefix_padding_ms=prefix_padding_ms,
            max_response_output_tokens=max_response_output_tokens,
            voice=voice or "alloy",
            tools=tools,
            fallback_providers=fallback_providers,
            barge_in_enabled=barge_in_enabled,
            omniplay_webhook_url=row.get("omniplay_webhook_url"),
            # Handoff OmniPlay config
            handoff_enabled=bool(row.get("handoff_enabled", True)),
            handoff_timeout_ms=int(row.get("handoff_timeout", 30)) * 1000,  # seconds to ms
            handoff_keywords=handoff_keywords,
            handoff_max_ai_turns=int(row.get("max_turns", 20)),
            handoff_queue_id=row.get("handoff_queue_id"),
            omniplay_company_id=row.get("omniplay_company_id"),
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
                time_result.message if time_result and not time_result.is_open
                else "Estamos fora do horário de atendimento."
            ),
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
        audio_out_queue: asyncio.Queue[Optional[tuple[int, bytes]]] = asyncio.Queue()
        pending = bytearray()
        sender_task: Optional[asyncio.Task] = None
        format_sent = False
        playback_generation = 0
        playback_lock = asyncio.Lock()
        playback_mode = os.getenv("FS_PLAYBACK_MODE", "rawAudio").lower()
        allow_streamaudio_fallback = os.getenv("FS_STREAMAUDIO_FALLBACK", "true").lower() in ("1", "true", "yes")
        
        # Audio Configuration - usar valores já extraídos do banco (db_warmup_* definidos acima)
        adaptive_warmup = db_adaptive_warmup
        warmup_min = max(5, db_warmup_chunks - 10)  # min = default - 10 (mas pelo menos 5)
        warmup_max = db_warmup_chunks + 10  # max = default + 10
        warmup_default = db_warmup_chunks
        
        # Provider config pode sobrescrever
        if isinstance(provider_config, dict):
            if "adaptive_warmup" in provider_config:
                adaptive_warmup = str(provider_config.get("adaptive_warmup")).lower() in ("1", "true", "yes")
            if "warmup_chunks_min" in provider_config:
                warmup_min = int(provider_config.get("warmup_chunks_min"))
            if "warmup_chunks_max" in provider_config:
                warmup_max = int(provider_config.get("warmup_chunks_max"))
            if "warmup_chunks" in provider_config:
                warmup_default = int(provider_config.get("warmup_chunks"))
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

        # Obter instância de métricas para funções aninhadas
        _metrics = get_metrics()

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
                _metrics.record_audio(call_uuid, "out", len(frame_bytes))
            except Exception:
                pass
            await asyncio.sleep(STREAMAUDIO_FRAME_MS / 1000.0)

        async def _sender_loop_rawaudio() -> None:
            """
            Envia áudio para o FreeSWITCH usando protocolo rawAudio + binário.
            Fallback para streamAudio quando rawAudio falhar (opcional).
            
            FIX: Não resetar streaming_started após cada micro-pausa.
            Problema anterior: cada pausa de 20ms forçava re-warmup de 300ms.
            Solução: usar contador de underruns consecutivos para decidir reset.
            """
            nonlocal playback_mode
            try:
                buffered_chunks: list[tuple[int, bytes]] = []
                streaming_started = False
                streamaudio_buffer = bytearray()
                warmup_chunks = max(warmup_min, min(warmup_default, warmup_max))
                underrun_count = 0
                consecutive_underruns = 0  # NEW: contador de underruns consecutivos
                # TTS envia audio em bursts - entre bursts pode haver 200-500ms de pausa
                # 25 underruns × 20ms = 500ms de tolerância
                max_consecutive_underruns = 25
                last_health_update = 0.0

                while True:
                    item = await audio_out_queue.get()
                    if item is None:
                        # Flush remaining chunks
                        for _, remaining in buffered_chunks:
                            await websocket.send(remaining)
                            await asyncio.sleep(PCM16_CHUNK_MS / 1000.0)
                        if streamaudio_buffer:
                            await _send_streamaudio_frame(bytes(streamaudio_buffer))
                        return
                    generation, chunk = item
                    if generation != playback_generation:
                        continue

                    # rawAudio header
                    if playback_mode == "rawaudio":
                        header_ok = await _send_rawaudio_header()
                        if not header_ok and allow_streamaudio_fallback:
                            playback_mode = "streamaudio"
                            logger.warning("Switching to streamAudio fallback (header failed)", extra={"call_uuid": call_uuid})

                    if playback_mode == "rawaudio":
                        # Limpar chunks obsoletos (de generation anterior) antes de acumular
                        # Isso evita que chunks antigos sejam contados no warmup
                        buffered_chunks = [(g, c) for g, c in buffered_chunks if g == generation]
                        
                        # Acumular chunks para warmup
                        buffered_chunks.append((generation, chunk))

                        # Iniciar streaming após warmup
                        if not streaming_started and len(buffered_chunks) >= warmup_chunks:
                            streaming_started = True
                            consecutive_underruns = 0  # Reset ao iniciar
                            logger.debug(
                                f"Warmup complete ({warmup_chunks} chunks), starting playback",
                                extra={"call_uuid": call_uuid},
                            )

                        # Enviar chunks com pacing PRECISO usando clock absoluto
                        # FIX: Usar clock absoluto ao invés de sleep relativo para evitar drift
                        if streaming_started:
                            chunk_interval = PCM16_CHUNK_MS / 1000.0  # 0.02s
                            next_send_time = time.monotonic()
                            
                            while buffered_chunks:
                                buffered_generation, chunk_to_send = buffered_chunks.pop(0)
                                if buffered_generation != playback_generation:
                                    continue
                                try:
                                    await websocket.send(chunk_to_send)
                                    try:
                                        _metrics.record_audio(call_uuid, "out", len(chunk_to_send))
                                    except Exception:
                                        pass
                                    consecutive_underruns = 0
                                    
                                    # Clock absoluto: dormir apenas o tempo restante até próximo slot
                                    next_send_time += chunk_interval
                                    sleep_duration = next_send_time - time.monotonic()
                                    if sleep_duration > 0:
                                        await asyncio.sleep(sleep_duration)
                                    # Se sleep_duration <= 0, estamos atrasados - não dormir!
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
                                    c = await asyncio.wait_for(
                                        audio_out_queue.get(),
                                        timeout=chunk_interval,
                                    )
                                except asyncio.TimeoutError:
                                    underrun_count += 1
                                    consecutive_underruns += 1
                                    _metrics.record_playback_underrun(call_uuid)
                                    
                                    # Logar apenas no primeiro, ou a cada 50
                                    if underrun_count == 1 or underrun_count % 50 == 0:
                                        logger.warning(
                                            f"Audio underrun #{underrun_count} (consecutive: {consecutive_underruns})",
                                            extra={"call_uuid": call_uuid, "underrun_count": underrun_count}
                                        )
                                    
                                    # Só resetar streaming após múltiplos underruns consecutivos
                                    if consecutive_underruns >= max_consecutive_underruns:
                                        if adaptive_warmup and warmup_chunks < warmup_max:
                                            warmup_chunks += 1
                                        streaming_started = False
                                        consecutive_underruns = 0
                                        logger.debug(
                                            f"Stream paused after {max_consecutive_underruns} underruns, waiting for new audio",
                                            extra={"call_uuid": call_uuid}
                                        )
                                        break
                                    else:
                                        # Atualizar clock mesmo em underrun para manter sincronização
                                        next_send_time += chunk_interval
                                        continue

                                if c is None:
                                    return
                                c_generation, c_bytes = c
                                if c_generation != playback_generation:
                                    continue

                                consecutive_underruns = 0
                                try:
                                    await websocket.send(c_bytes)
                                    try:
                                        _metrics.record_audio(call_uuid, "out", len(c_bytes))
                                    except Exception:
                                        pass
                                    
                                    # Clock absoluto preciso
                                    next_send_time += chunk_interval
                                    sleep_duration = next_send_time - time.monotonic()
                                    if sleep_duration > 0:
                                        await asyncio.sleep(sleep_duration)
                                except Exception as e:
                                    if allow_streamaudio_fallback:
                                        playback_mode = "streamaudio"
                                        streamaudio_buffer.extend(c_bytes)
                                        logger.warning(
                                            f"Switching to streamAudio fallback (send failed): {e}",
                                            extra={"call_uuid": call_uuid},
                                        )
                                        break
                                    raise

                            # Só resetar streaming se saiu por underruns consecutivos
                            # (Não resetar se saiu por outro motivo)
                            if consecutive_underruns >= max_consecutive_underruns:
                                streaming_started = False
                            elif adaptive_warmup and underrun_count == 0 and warmup_chunks > warmup_min:
                                warmup_chunks -= 1

                    if playback_mode == "streamaudio":
                        streamaudio_buffer.extend(chunk)
                        while len(streamaudio_buffer) >= STREAMAUDIO_FRAME_BYTES:
                            frame = bytes(streamaudio_buffer[:STREAMAUDIO_FRAME_BYTES])
                            del streamaudio_buffer[:STREAMAUDIO_FRAME_BYTES]
                            await _send_streamaudio_frame(frame)

                    # Atualizar health score periodicamente
                    now = time.time()
                    if now - last_health_update >= 1.0:
                        session_metrics = _metrics.get_session_metrics(call_uuid)
                        if session_metrics:
                            underrun_ratio = session_metrics.playback_underruns / max(1, session_metrics.audio_chunks_sent)
                            latency_penalty = min(30.0, session_metrics.avg_latency_ms / 50.0)
                            underrun_penalty = min(50.0, underrun_ratio * 200.0)
                            health_score = 100.0 - latency_penalty - underrun_penalty
                            _metrics.update_health_score(call_uuid, health_score)
                        last_health_update = now

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
                    await audio_out_queue.put((playback_generation, chunk))

            except Exception as e:
                logger.error(
                    f"Error queueing audio for FreeSWITCH (rawAudio): {e}",
                    exc_info=True,
                    extra={"call_uuid": call_uuid},
                )

        async def clear_playback(_: str) -> None:
            """
            Barge-in: limpa buffer e descarta áudio pendente.
            """
            nonlocal playback_generation
            async with playback_lock:
                playback_generation += 1
                pending.clear()
                try:
                    while True:
                        audio_out_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
        
        # Criar sessão via manager
        manager = get_session_manager()
        session = await manager.create_session(
            config=config,
            on_audio_output=send_audio,
            on_barge_in=clear_playback,
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
