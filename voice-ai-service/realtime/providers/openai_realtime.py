"""
OpenAI Realtime API Provider.

=== KNOWLEDGE BASE REFERENCES ===
- Context7 Library ID: /websites/platform_openai (9418 snippets)
- Context7 SDK: /openai/openai-python (429 snippets)
- Docs: https://platform.openai.com/docs/guides/realtime-conversations
- API Reference: https://platform.openai.com/docs/api-reference/realtime
- SDK: https://github.com/openai/openai-python (src/openai/resources/beta/realtime/)

=== MODELOS DISPONÍVEIS (Jan/2026) ===
GA (General Availability) - Recomendados:
- gpt-realtime (melhor qualidade, 32k ctx)
- gpt-realtime-mini (menor custo, 32k ctx)

Preview (com snapshots, formato legado):
- gpt-4o-realtime-preview (alias latest)
- gpt-4o-realtime-preview-2025-06-03
- gpt-4o-realtime-preview-2024-12-17
- gpt-4o-realtime-preview-2024-10-01
- gpt-4o-mini-realtime-preview (16k ctx)
- gpt-4o-mini-realtime-preview-2024-12-17

IMPORTANTE: Modelos GA e Preview usam formatos de session.update DIFERENTES!

=== CONFIGURAÇÕES DE ÁUDIO ===
- Input: 24kHz PCM16 mono (audio/pcm)
- Output: 24kHz PCM16 mono (audio/pcm)
- Endpoint: wss://api.openai.com/v1/realtime?model={model}
- Headers: Authorization: Bearer {api_key}
- NOTA: Header OpenAI-Beta NÃO É MAIS NECESSÁRIO para modelos GA

=== FORMATO DE EVENTOS (Context7 verificado) ===
Client → Server:
- session.update: Configurar sessão (modalities, voice, VAD, tools)
- input_audio_buffer.append: Enviar áudio (base64, até 15MiB)
- input_audio_buffer.commit: Commit manual (se VAD desabilitado)
- conversation.item.create: Criar item (message, function_call_output)
- response.create: Solicitar resposta
- response.cancel: Interromper resposta (barge-in)

Server → Client:
- session.created: Sessão iniciada
- session.updated: Configuração atualizada
- response.output_audio.delta: Chunk de áudio (base64)
- response.output_audio.done: Áudio completo
- response.audio_transcript.delta/done: Transcrição do assistente
- conversation.item.input_audio_transcription.completed: STT do usuário
- input_audio_buffer.speech_started/speech_stopped: VAD
- input_audio_buffer.timeout_triggered: Timeout de silêncio (idle_timeout_ms)
- response.function_call_arguments.done: Function call
- error: Erros (rate_limit_exceeded, etc.)

=== VAD (TURN DETECTION) - Context7 verificado Jan/2026 ===
Dois tipos disponíveis. IMPORTANTE: turn_detection vai DENTRO de audio.input!

1. server_vad (baseado em silêncio):
   - threshold: 0.5 (0.0-1.0, sensibilidade)
   - prefix_padding_ms: 300 (áudio antes da fala)
   - silence_duration_ms: 200-500 (silêncio para encerrar turno)
   - create_response: true (auto-responder)
   - interrupt_response: true (OBRIGATÓRIO para barge-in!)

2. semantic_vad (baseado em semântica - RECOMENDADO):
   - eagerness: "low" | "medium" | "high" | "auto"
     - low: Paciente, espera pausas longas
     - medium: Balanceado (recomendado pt-BR)
     - high: Responde rápido
     - auto: Deixa o modelo decidir (default)
   - create_response: true
   - interrupt_response: true (OBRIGATÓRIO para barge-in!)

3. disabled (push-to-talk):
   - Não inclui turn_detection no session.update
   - Requer input_audio_buffer.commit manual

=== API GA (General Availability) ===
Migrado para API GA em Jan/2026.
- Modelo: gpt-realtime (padrão)
- Sem header OpenAI-Beta necessário
- Limite de sessão: 60 minutos
- Custo ~20% menor que versão preview
Ref: openai.com/blog/introducing-gpt-realtime

=== SESSION.UPDATE FORMAT (Context7 Jan/2026) ===

FORMATO GA (gpt-realtime, gpt-realtime-mini):
{
    "type": "session.update",
    "session": {
        "type": "realtime",  // OBRIGATÓRIO
        "output_modalities": ["audio"],
        "instructions": "...",
        "tools": [...],
        "tool_choice": "auto",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "turn_detection": {"type": "semantic_vad", ...}
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": 24000},  // rate OBRIGATÓRIO!
                "voice": "marin"
            }
        }
    }
}

FORMATO PREVIEW (gpt-4o-realtime-preview, etc.):
{
    "type": "session.update",
    "session": {
        // SEM "type"!
        "modalities": ["text", "audio"],
        "instructions": "...",
        "voice": "alloy",
        "input_audio_format": "pcm16",
        "output_audio_format": "pcm16",
        "turn_detection": {"type": "semantic_vad", ...},
        "tools": [...],
        "tool_choice": "auto"
    }
}

NOTA sobre turn_detection:
- GA: audio.input.turn_detection (dentro do objeto audio)
- Preview: session.turn_detection (nível raiz da session)

Ref: Context7 /websites/platform_openai - session.update, realtime-vad
"""

import asyncio
import base64
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from .base import (
    BaseRealtimeProvider,
    ProviderEvent,
    ProviderEventType,
    RealtimeConfig,
)
from ..config.prompts import get_enhanced_prompt

logger = logging.getLogger(__name__)


# Tools padrão conforme design.md Decision 7
# NOTA: Todos os tools devem ter "required" (mesmo que vazio) conforme Context7
DEFAULT_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "name": "transfer_call",
        "description": "Transfere a chamada para outro ramal ou departamento. Use quando o cliente pedir para falar com alguém específico ou um setor.",
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Nome do setor, pessoa ou ramal de destino"
                },
                "reason": {
                    "type": "string",
                    "description": "Motivo da transferência"
                }
            },
            "required": ["destination"]
        }
    },
    {
        "type": "function", 
        "name": "create_ticket",
        "description": "Cria um ticket/recado no sistema. Use quando o cliente quiser deixar uma mensagem ou recado.",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Assunto do ticket/recado"
                },
                "description": {
                    "type": "string",
                    "description": "Descrição detalhada da mensagem"
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Prioridade do ticket"
                }
            },
            "required": ["subject"]
        }
    },
    {
        "type": "function",
        "name": "end_call",
        "description": (
            "Encerra a chamada IMEDIATAMENTE. VOCÊ deve chamar PROATIVAMENTE após: "
            "1) Anotar recado e agradecer. 2) Cliente dizer que não precisa de mais nada. "
            "3) Qualquer despedida sua. NÃO espere o cliente dizer tchau - VOCÊ encerra."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Motivo: 'recado_anotado', 'atendimento_concluido', 'cliente_nao_quer_recado'"
                }
            },
            "required": []
        }
    }
]


class OpenAIRealtimeProvider(BaseRealtimeProvider):
    """
    Provider para OpenAI Realtime API (GA - General Availability).
    
    Sample rates (conforme SDK oficial):
    - Input: 24kHz PCM16 mono
    - Output: 24kHz PCM16 mono
    
    Protocolo WebSocket (GA):
    - Header: Authorization: Bearer {api_key}
    - URL: wss://api.openai.com/v1/realtime?model={model}
    
    NOTA: Header OpenAI-Beta NÃO é mais necessário para modelos GA.
    """
    
    REALTIME_URL = "wss://api.openai.com/v1/realtime"
    # Modelo padrão para Realtime API (GA - Janeiro 2026)
    # Ref: https://platform.openai.com/docs/guides/realtime
    DEFAULT_MODEL = "gpt-realtime"
    
    def __init__(self, credentials: Dict[str, Any], config: RealtimeConfig):
        import os
        super().__init__(credentials, config)
        
        # Fallback para variáveis de ambiente se credentials estiver vazio
        self.api_key = credentials.get("api_key") or os.getenv("OPENAI_API_KEY")
        self.model = credentials.get("model", self.DEFAULT_MODEL)
        
        if not self.api_key:
            raise ValueError("OpenAI API key not configured (check DB config or OPENAI_API_KEY env)")
        
        logger.info("OpenAI Realtime credentials loaded", extra={
            "api_key_source": "db" if credentials.get("api_key") else "env",
            "model": self.model,
        })
        
        self._ws: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[ProviderEvent] = asyncio.Queue()
        self._session_id: Optional[str] = None
        
        # Tracking de tempo de sessão (limite OpenAI: 60 minutos)
        self._session_start_time: Optional[float] = None
        self._max_session_duration_seconds: int = 55 * 60  # 55 min (5 min de margem)
        self._response_active: bool = False
        # Flag para indicar que há resultado de função pendente aguardando processamento
        # Se True, quando _response_active se tornar False, solicitar nova resposta
        self._pending_function_result: bool = False
        
        # Contadores para agregação de logs de áudio (reduzir ruído)
        self._audio_chunks_sent: int = 0
        self._audio_bytes_sent: int = 0
        self._audio_chunks_received: int = 0
        self._audio_bytes_received: int = 0
        self._last_audio_log_time: float = 0.0
        self._AUDIO_LOG_INTERVAL: float = 5.0  # Logar a cada 5 segundos
    
    @property
    def name(self) -> str:
        return "openai_realtime"
    
    @property
    def input_sample_rate(self) -> int:
        # G.711 (pcmu/pcma) usa 8kHz nativo
        # PCM16 usa 24kHz
        audio_format = getattr(self.config, 'audio_format', 'pcm16')
        if audio_format in ("g711_ulaw", "pcmu", "ulaw", "g711_alaw", "pcma", "alaw"):
            return 8000  # G.711 é sempre 8kHz
        return 24000  # PCM16 @ 24kHz
    
    @property
    def output_sample_rate(self) -> int:
        # G.711 (pcmu/pcma) usa 8kHz nativo
        # PCM16 usa 24kHz
        # Context7 confirma: OpenAI suporta g711_ulaw/g711_alaw para output
        audio_format = getattr(self.config, 'audio_format', 'pcm16')
        if audio_format in ("g711_ulaw", "pcmu", "ulaw", "g711_alaw", "pcma", "alaw"):
            return 8000  # G.711 é sempre 8kHz
        return 24000  # PCM16 @ 24kHz
    
    async def connect(self) -> None:
        """Conecta ao OpenAI Realtime API."""
        if self._connected:
            return
        
        url = f"{self.REALTIME_URL}?model={self.model}"
        
        # Headers para API GA (General Availability)
        # NOTA: OpenAI-Beta NÃO é mais necessário para modelos GA (gpt-realtime)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        
        # Adicionar header Beta apenas para modelos preview (fallback)
        if "preview" in self.model.lower():
            headers["OpenAI-Beta"] = "realtime=v1"
            logger.warning(f"Using preview model {self.model} - consider migrating to gpt-realtime")
        
        logger.debug(f"Connecting to OpenAI Realtime (GA): {url}", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
            "is_ga": "preview" not in self.model.lower(),
        })
        
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=None,
            ping_interval=20,
        )
        
        # Aguardar session.created
        response = await asyncio.wait_for(self._ws.recv(), timeout=10)
        event = json.loads(response)
        
        if event.get("type") != "session.created":
            raise ConnectionError(f"Unexpected initial event: {event.get('type')}")
        
        self._session_id = event.get("session", {}).get("id")
        self._connected = True
        self._session_start_time = time.time()  # Registrar início da sessão
        self._receive_task = asyncio.create_task(self._receive_loop())
        
        logger.info("Connected to OpenAI Realtime", extra={
            "domain_uuid": self.config.domain_uuid,
            "model": self.model,
            "session_id": self._session_id,
            "max_duration_minutes": self._max_session_duration_seconds // 60,
        })
    
    def _is_ga_model(self) -> bool:
        """
        Verifica se o modelo é GA (General Availability) ou Preview.
        
        GA models: gpt-realtime, gpt-realtime-mini
        Preview models: gpt-4o-realtime-preview, gpt-4o-mini-realtime-preview, etc.
        
        Returns:
            True se for modelo GA, False se for preview.
        """
        return "preview" not in self.model.lower()
    
    async def configure(self) -> None:
        """
        Configura sessão com prompt, voz, VAD, tools.
        
        DIFERENÇA ENTRE FORMATOS (Context7 Jan/2026):
        
        === FORMATO GA (gpt-realtime, gpt-realtime-mini) ===
        {
            "type": "session.update",
            "session": {
                "type": "realtime",  // OBRIGATÓRIO para GA
                "model": "gpt-realtime",
                "output_modalities": ["audio"],
                "instructions": "system prompt",
                "tools": [...],
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "turn_detection": {...}  // VAD em audio.input
                    },
                    "output": {
                        "format": {"type": "audio/pcm"},
                        "voice": "marin"
                    }
                }
            }
        }
        
        === FORMATO PREVIEW (gpt-4o-realtime-preview) ===
        {
            "type": "session.update",
            "session": {
                // SEM "type" - NÃO SUPORTADO EM PREVIEW!
                "modalities": ["text", "audio"],  // "modalities", não "output_modalities"
                "instructions": "system prompt",
                "voice": "alloy",  // Voice no nível raiz, não em audio.output
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {...},  // VAD no nível raiz
                "tools": [...],
                "tool_choice": "auto"
            }
        }
        
        Ref: Context7 /websites/platform_openai - session.update
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Vozes disponíveis: alloy, ash, ballad, coral, echo, marin, sage, shimmer, verse, cedar
        voice = self.config.voice or "alloy"
        
        # Construir VAD config
        vad_config = self._build_vad_config()
        
        # Detectar formato baseado no modelo
        is_ga = self._is_ga_model()
        format_label = "GA" if is_ga else "PREVIEW"
        
        vad_type = vad_config.get("type") if vad_config else "disabled"
        vad_eagerness = vad_config.get("eagerness") if vad_config else None
        
        # Preparar tools
        tools = self.config.tools or DEFAULT_TOOLS
        tools_names = [t.get("name") for t in tools]
        
        # Enriquecer prompt com regras conversacionais
        # Adiciona: confirmações variadas, adaptação emocional, coerência contextual
        enhanced_prompt = get_enhanced_prompt(self.config.system_prompt or "")
        
        if is_ga:
            # === FORMATO GA: Enviar configuração em DUAS ETAPAS ===
            # Context7 mostra que áudio e tools são enviados separadamente em alguns exemplos
            
            # ETAPA 1: Configuração de áudio e sessão
            # Determinar formato de áudio baseado na configuração
            # G.711 μ-law (8kHz) elimina resampling e reduz latência ~50ms
            audio_format = getattr(self.config, 'audio_format', 'pcm16')
            if audio_format in ("g711_ulaw", "pcmu", "ulaw"):
                input_format = {"type": "audio/pcmu"}  # G.711 μ-law @ 8kHz
                input_rate_log = "8kHz G.711 μ-law"
            elif audio_format in ("g711_alaw", "pcma", "alaw"):
                input_format = {"type": "audio/pcma"}  # G.711 A-law @ 8kHz
                input_rate_log = "8kHz G.711 A-law"
            else:
                input_format = {"type": "audio/pcm", "rate": 24000}  # PCM16 @ 24kHz
                input_rate_log = "24kHz PCM16"
            
            logger.info(f"OpenAI Realtime audio format: input={input_rate_log}", extra={
                "audio_format": audio_format,
                "domain_uuid": self.config.domain_uuid,
            })
            
            # Configurar output format baseado no input (G.711 elimina resampling)
            # Context7 confirma: OpenAI suporta g711_ulaw e g711_alaw para output também!
            if audio_format in ("g711_ulaw", "pcmu", "ulaw"):
                output_format = {"type": "audio/pcmu"}  # G.711 μ-law @ 8kHz
                output_rate_log = "8kHz G.711 μ-law"
            elif audio_format in ("g711_alaw", "pcma", "alaw"):
                output_format = {"type": "audio/pcma"}  # G.711 A-law @ 8kHz
                output_rate_log = "8kHz G.711 A-law"
            else:
                output_format = {"type": "audio/pcm", "rate": 24000}  # PCM16 @ 24kHz
                output_rate_log = "24kHz PCM16"
            
            logger.info(f"OpenAI Realtime audio: input={input_rate_log}, output={output_rate_log}", extra={
                "audio_format": audio_format,
                "domain_uuid": self.config.domain_uuid,
            })
            
            audio_config = {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": enhanced_prompt,
                    "audio": {
                        "input": {
                            "format": input_format,
                            # Noise reduction para ambientes ruidosos (viva-voz, salas)
                            # far_field = microfone distante, near_field = headset
                            "noise_reduction": {"type": "far_field"},
                        },
                        "output": {
                            # G.711 μ-law nativo elimina resampling 24kHz→8kHz
                            # Reduz latência ~50ms e melhora qualidade (sem artefatos de resample)
                            "format": output_format,
                            "voice": voice,
                        },
                    },
                }
            }
            
            # VAD para GA: vai em audio.input.turn_detection
            if vad_config is not None:
                audio_config["session"]["audio"]["input"]["turn_detection"] = vad_config
            
            logger.info(f"Sending session.update #1 ({format_label} - audio/session)", extra={
                "domain_uuid": self.config.domain_uuid,
                "voice": voice,
                "vad_type": vad_type,
                "model": self.model,
            })
            
            try:
                await self._ws.send(json.dumps(audio_config))
                logger.debug(f"session.update #1 payload: {json.dumps(audio_config)[:1500]}")
            except Exception as e:
                logger.error(f"Failed to send session.update #1: {e}")
                raise
            
            # Pequena pausa para garantir processamento
            await asyncio.sleep(0.1)
            
            # ETAPA 2: Configurar tools separadamente
            # IMPORTANTE: Para GA, session.type é OBRIGATÓRIO em TODOS os session.update!
            tools_config = {
                "type": "session.update",
                "session": {
                    "type": "realtime",  # OBRIGATÓRIO em cada session.update para GA!
                    "tools": tools,
                    "tool_choice": "auto",
                }
            }
            
            logger.info(f"Sending session.update #2 ({format_label} - tools)", extra={
                "domain_uuid": self.config.domain_uuid,
                "tools_count": len(tools),
                "tools_names": tools_names,
            })
            
            try:
                await self._ws.send(json.dumps(tools_config))
                logger.debug(f"session.update #2 payload: {json.dumps(tools_config)[:1500]}")
                logger.info(f"session.update sent successfully ({format_label} format)", extra={
                    "tools_count": len(tools),
                })
            except Exception as e:
                logger.error(f"Failed to send session.update #2 (tools): {e}")
                raise
                
        else:
            # === FORMATO PREVIEW: Enviar tudo junto (mantém comportamento anterior) ===
            # Determinar formato de áudio para modelos preview
            # Context7: OpenAI suporta g711_ulaw e g711_alaw para input E output
            audio_format = getattr(self.config, 'audio_format', 'pcm16')
            if audio_format in ("g711_ulaw", "pcmu", "ulaw"):
                input_audio_fmt = "g711_ulaw"
                output_audio_fmt = "g711_ulaw"  # G.711 elimina resampling
            elif audio_format in ("g711_alaw", "pcma", "alaw"):
                input_audio_fmt = "g711_alaw"
                output_audio_fmt = "g711_alaw"  # G.711 elimina resampling
            else:
                input_audio_fmt = "pcm16"
                output_audio_fmt = "pcm16"
            
            logger.info(f"OpenAI Realtime PREVIEW: input={input_audio_fmt}, output={output_audio_fmt}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": enhanced_prompt,
                    "voice": voice,
                    "input_audio_format": input_audio_fmt,
                    "output_audio_format": output_audio_fmt,  # G.711 elimina resampling 24k→8k
                    # Noise reduction para ambientes ruidosos (viva-voz, salas)
                    # far_field = microfone distante (telefonia), near_field = headset
                    "input_audio_noise_reduction": {"type": "far_field"},
                    "tools": tools,
                    "tool_choice": "auto",
                }
            }
            
            # VAD para preview: vai no nível raiz da session
            if vad_config is not None:
                session_config["session"]["turn_detection"] = vad_config
            
            logger.info(f"Sending session.update ({format_label} format) - voice={voice}, vad={vad_type}", extra={
                "domain_uuid": self.config.domain_uuid,
                "has_instructions": bool(self.config.system_prompt),
                "voice": voice,
                "vad_type": vad_type,
                "vad_eagerness": vad_eagerness,
                "model": self.model,
                "is_ga_model": is_ga,
                "tools_count": len(tools),
                "tools_names": tools_names,
            })
            
            logger.debug(f"session.update payload: {json.dumps(session_config, indent=2)[:2000]}")
            
            try:
                await self._ws.send(json.dumps(session_config))
                logger.info(f"session.update sent successfully ({format_label} format)", extra={
                    "tools_count": len(tools),
                })
            except Exception as e:
                logger.error(f"Failed to send session.update: {e}")
                raise
        
        # Se houver first_message (saudação), enviar como texto e solicitar resposta
        if self.config.first_message:
            logger.debug(f"Sending first message: {self.config.first_message[:50]}...")
            # send_text já faz response.create quando request_response=True
            await self.send_text(self.config.first_message, request_response=True)
    
    def _build_vad_config(self) -> Optional[Dict[str, Any]]:
        """
        Constrói configuração de VAD (Voice Activity Detection).
        
        Suporta dois tipos (Context7 verificado Jan/2026):
        - server_vad: Baseado em silêncio (threshold, silence_duration_ms)
        - semantic_vad: Baseado em semântica (eagerness) - MAIS INTELIGENTE
        
        semantic_vad entende quando o usuário TERMINOU de falar,
        não apenas quando fez uma pausa. Melhor para pt-BR e linguagem natural.
        
        Parâmetros semantic_vad (Context7 /websites/platform_openai):
        - eagerness: "low" | "medium" | "high" | "auto"
          - low: Paciente, espera pausas longas antes de responder
          - medium: Balanceado (recomendado para pt-BR)
          - high: Responde rápido, pode interromper
          - auto: Deixa o modelo decidir (default na API)
        - create_response: true para responder automaticamente
        - interrupt_response: true para permitir barge-in (OBRIGATÓRIO para barge-in!)
        
        Parâmetros server_vad (Context7 /websites/platform_openai):
        - threshold: 0.0-1.0 (sensibilidade, padrão 0.5)
        - prefix_padding_ms: ms de áudio antes da fala (padrão 300)
        - silence_duration_ms: ms de silêncio para encerrar turno (padrão 200-500)
        - create_response: true
        - interrupt_response: true (OBRIGATÓRIO para barge-in!)
        
        Ref: Context7 query "Realtime API turn_detection semantic_vad server_vad"
        """
        vad_type = getattr(self.config, 'vad_type', 'server_vad')
        
        if vad_type == "semantic_vad":
            # semantic_vad: Mais inteligente, entende contexto semântico
            eagerness = getattr(self.config, 'vad_eagerness', 'medium')
            
            # Validar eagerness (Context7: inclui "auto" como opção válida)
            valid_eagerness = ["low", "medium", "high", "auto"]
            if eagerness not in valid_eagerness:
                logger.warning(f"Invalid eagerness '{eagerness}', using 'medium'")
                eagerness = "medium"
            
            logger.info(f"VAD: semantic_vad (eagerness={eagerness})")
            
            return {
                "type": "semantic_vad",
                "eagerness": eagerness,
                "create_response": True,
                # interrupt_response: permite usuário interromper agente (barge-in)
                # Context7: OBRIGATÓRIO para barge-in funcionar
                "interrupt_response": True,
            }
        
        elif vad_type == "disabled":
            # Push-to-talk: VAD desabilitado, controle manual
            logger.info("VAD: disabled (push-to-talk mode)")
            return None  # Sem turn_detection = push-to-talk
        
        else:
            # server_vad: Baseado em silêncio (padrão tradicional)
            threshold = self.config.vad_threshold or 0.5
            silence_ms = self.config.silence_duration_ms or 500
            prefix_ms = self.config.prefix_padding_ms or 300
            
            logger.info(f"VAD: server_vad (threshold={threshold}, silence={silence_ms}ms)")
            
            return {
                "type": "server_vad",
                "threshold": threshold,
                "prefix_padding_ms": prefix_ms,
                "silence_duration_ms": silence_ms,
                "create_response": True,
                # Context7: interrupt_response OBRIGATÓRIO para barge-in funcionar
                "interrupt_response": True,
            }
    
    async def send_audio(self, audio_bytes: bytes) -> None:
        """
        Envia áudio para OpenAI.
        
        Formato: base64 PCM16 @ 24kHz
        Ref: input_audio_buffer.append event (SDK oficial)
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        
        await self._ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }))
        
        # Agregação de logs - logar resumo a cada N segundos em vez de cada chunk
        import time
        self._audio_chunks_sent += 1
        self._audio_bytes_sent += len(audio_bytes)
        
        now = time.time()
        if now - self._last_audio_log_time >= self._AUDIO_LOG_INTERVAL:
            logger.debug(
                f"📤 [OPENAI] Audio sent: {self._audio_chunks_sent} chunks, {self._audio_bytes_sent} bytes in last {self._AUDIO_LOG_INTERVAL}s",
                extra={
                    "domain_uuid": self.config.domain_uuid,
                    "chunks_sent": self._audio_chunks_sent,
                    "bytes_sent": self._audio_bytes_sent,
                }
            )
            self._audio_chunks_sent = 0
            self._audio_bytes_sent = 0
            self._last_audio_log_time = now

    async def commit_audio_buffer(self) -> None:
        """Commit manual do buffer de áudio (necessário quando VAD está desabilitado)."""
        if not self._ws:
            raise RuntimeError("Not connected")

        await self._ws.send(json.dumps({
            "type": "input_audio_buffer.commit"
        }))
        logger.debug("Audio buffer committed", extra={
            "domain_uuid": self.config.domain_uuid,
        })

    async def request_response(self) -> None:
        """Solicita resposta do modelo (útil para push-to-talk)."""
        if not self._ws:
            raise RuntimeError("Not connected")
        if not self._response_active:
            await self._ws.send(json.dumps({"type": "response.create"}))
            logger.debug("Response requested from OpenAI", extra={
                "domain_uuid": self.config.domain_uuid,
            })
        else:
            logger.debug("Response already active; skipping response.create", extra={
                "domain_uuid": self.config.domain_uuid,
            })
    
    async def send_text(self, text: str, request_response: bool = True) -> None:
        """
        Envia mensagem de texto como input do usuário e solicita resposta.
        
        IMPORTANTE: Isso é interpretado como se o USUÁRIO tivesse falado.
        Para enviar mensagens como ASSISTENTE (fillers, etc.), use send_assistant_message().
        
        Ref: conversation.item.create + response.create events (SDK oficial)
        
        Args:
            text: Texto a enviar (será interpretado como input do usuário)
            request_response: Se True, solicita resposta do modelo após enviar
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}]
            }
        }))
        
        logger.debug(f"Text sent to OpenAI: {text[:50]}...", extra={
            "domain_uuid": self.config.domain_uuid,
        })
        
        # Solicitar resposta do modelo (gera áudio TTS)
        if request_response:
            if not self._response_active:
                await self._ws.send(json.dumps({"type": "response.create"}))
                logger.debug("Response requested from OpenAI", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
            else:
                logger.debug("Response already active; skipping response.create", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
    
    async def send_assistant_message(self, text: str) -> None:
        """
        Envia mensagem como se o ASSISTENTE tivesse falado.
        
        Útil para fillers e mensagens de transição que devem aparecer
        na conversa como falas do assistente, sem solicitar nova resposta.
        
        NOTA: Isso adiciona à história da conversa mas NÃO gera áudio TTS.
        O OpenAI Realtime não suporta TTS de mensagens do assistente retroativamente.
        Para gerar áudio, use response.create com instruções.
        
        Args:
            text: Texto que o assistente "falou"
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": text}]
            }
        }))
        
        logger.debug(f"Assistant message added: {text[:50]}...", extra={
            "domain_uuid": self.config.domain_uuid,
        })
    
    async def send_instruction(self, instruction: str) -> None:
        """
        Envia instrução para a IA executar e gerar áudio TTS.
        
        Diferente de send_text (que envia como input do usuário),
        este método usa response.create com instruções específicas,
        fazendo a IA FALAR o texto, não responder a ele.
        
        Útil para:
        - Silence fallback: "Você ainda está aí?"
        - Avisos do sistema
        - Mensagens de transição
        
        Args:
            instruction: O que a IA deve DIZER (não uma pergunta para ela responder)
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        # Interromper resposta ativa se houver
        if self._response_active:
            await self.interrupt()
            await asyncio.sleep(0.1)  # Pequeno delay para garantir interrupção
        
        # Usar response.create com instructions claras
        # O instructions sobrescreve as instruções da sessão APENAS para esta resposta
        # Isso faz a IA responder com o texto especificado
        await self._ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "instructions": (
                    f"O usuário ficou em silêncio. "
                    f"Você DEVE perguntar exatamente: \"{instruction}\" "
                    f"Não diga mais nada, apenas essa pergunta."
                )
            }
        }))
        
        self._response_active = True
        logger.info(f"Instruction sent to OpenAI: {instruction[:50]}...", extra={
            "domain_uuid": self.config.domain_uuid,
        })
    
    async def interrupt(self) -> None:
        """
        Interrompe resposta atual (barge-in).
        
        Ref: response.cancel event (SDK oficial)
        """
        if self._ws:
            await self._ws.send(json.dumps({"type": "response.cancel"}))
            logger.debug("Interrupt signal sent to OpenAI", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            # Otimista: marcar como não ativa para evitar response.create concorrente
            self._response_active = False
    
    async def send_function_result(
        self,
        function_name: str,
        result: Dict[str, Any],
        call_id: Optional[str] = None,
        request_response: bool = True
    ) -> None:
        """
        Envia resultado de function call.
        
        Args:
            function_name: Nome da função executada
            result: Resultado da função
            call_id: ID do function call (para correlação)
            request_response: Se True, solicita nova resposta da IA após enviar resultado.
                              Usar False para funções que já geraram resposta manualmente
                              (ex: request_handoff que usa _send_text_to_provider)
        
        Ref: conversation.item.create (type: function_call_output) (SDK oficial)
        """
        if not self._ws:
            raise RuntimeError("Not connected")
        
        await self._ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id or "",
                "output": json.dumps(result)
            }
        }))
        
        # Solicitar nova resposta após enviar resultado da função
        # APENAS se request_response=True (default)
        if request_response:
            if not self._response_active:
                await self._ws.send(json.dumps({"type": "response.create"}))
                self._pending_function_result = False
            else:
                # Marcar que há resultado pendente - será processado quando response.done chegar
                self._pending_function_result = True
                logger.debug("Response already active; marking pending function result", extra={
                    "domain_uuid": self.config.domain_uuid,
                    "call_id": call_id,
                })
        else:
            # Não solicitar resposta - a função já cuidou disso
            self._pending_function_result = False
            logger.debug(f"Function result sent (no response requested): {function_name}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
        
        logger.debug(f"Function result sent to OpenAI: {function_name}", extra={
            "domain_uuid": self.config.domain_uuid,
            "call_id": call_id,
        })
    
    def get_session_remaining_seconds(self) -> Optional[int]:
        """
        Retorna segundos restantes antes do limite de sessão OpenAI.
        
        OpenAI Realtime tem limite de 60 minutos por sessão.
        Retornamos tempo restante para permitir reconexão preventiva.
        
        Returns:
            Segundos restantes ou None se não conectado
        """
        if not self._session_start_time:
            return None
        
        elapsed = time.time() - self._session_start_time
        remaining = self._max_session_duration_seconds - elapsed
        return max(0, int(remaining))
    
    def is_session_expiring_soon(self, threshold_seconds: int = 300) -> bool:
        """
        Verifica se sessão está perto de expirar.
        
        Args:
            threshold_seconds: Segundos de threshold (default: 5 min)
        
        Returns:
            True se sessão expira em menos de threshold_seconds
        """
        remaining = self.get_session_remaining_seconds()
        if remaining is None:
            return False
        return remaining < threshold_seconds
    
    async def receive_events(self) -> AsyncIterator[ProviderEvent]:
        """Generator de eventos."""
        while self._connected:
            try:
                # Verificar limite de tempo de sessão a cada iteração
                if self.is_session_expiring_soon(threshold_seconds=60):
                    remaining = self.get_session_remaining_seconds()
                    logger.warning(
                        f"OpenAI session expiring in {remaining}s (limit: 60min)",
                        extra={
                            "domain_uuid": self.config.domain_uuid,
                            "session_id": self._session_id,
                            "remaining_seconds": remaining,
                        }
                    )
                    # Emitir evento de warning para permitir ação preventiva
                    yield ProviderEvent(
                        type=ProviderEventType.ERROR,
                        data={
                            "error": {
                                "code": "session_expiring",
                                "message": f"Session expiring in {remaining}s",
                            }
                        }
                    )
                    break
                
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                yield event
                if event.type in (ProviderEventType.SESSION_ENDED, ProviderEventType.ERROR):
                    break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
    
    async def _receive_loop(self) -> None:
        """Loop de recebimento de eventos."""
        if not self._ws:
            return
        
        try:
            async for message in self._ws:
                event = json.loads(message)
                provider_event = self._parse_event(event)
                if provider_event:
                    await self._event_queue.put(provider_event)
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"OpenAI WebSocket closed: {e}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.SESSION_ENDED,
                data={"reason": str(e)}
            ))
        except Exception as e:
            logger.error(f"OpenAI receive loop error: {e}", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            await self._event_queue.put(ProviderEvent(
                type=ProviderEventType.ERROR,
                data={"error": str(e)}
            ))
    
    def _parse_event(self, event: Dict[str, Any]) -> Optional[ProviderEvent]:
        """
        Converte evento OpenAI para ProviderEvent.
        
        IMPORTANTE - Nomes de eventos conforme documentação oficial (Jan/2026):
        - response.output_audio.delta (NÃO response.audio.delta!)
        - response.output_audio.done
        - response.audio_transcript.delta/done
        - conversation.item.input_audio_transcription.completed
        """
        etype = event.get("type", "")
        
        # Log para debug de eventos desconhecidos
        # COMPATIBILIDADE: Inclui formatos antigos (response.audio.*) e novos (response.output_audio.*)
        KNOWN_EVENTS = {
            # Áudio (formatos antigo e novo)
            "response.audio.delta", "response.output_audio.delta",
            "response.audio.done", "response.output_audio.done",
            # Transcrição do assistente (formatos antigo e novo)
            "response.audio_transcript.delta", "response.audio_transcript.done",
            "response.output_audio_transcript.delta", "response.output_audio_transcript.done",  # Formato GA 2026
            # Transcrição do usuário (STT)
            "conversation.item.input_audio_transcription.completed",
            "conversation.item.input_audio_transcription.failed",
            "conversation.item.input_audio_transcription.delta",  # Novo em 2026
            # VAD
            "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
            # Response lifecycle
            "response.created", "response.done",
            "response.content_part.added", "response.content_part.done",
            "response.output_item.added", "response.output_item.done",
            # Text output (se modality text habilitada)
            "response.text.delta", "response.text.done",
            # Function calls
            "response.function_call_arguments.delta", "response.function_call_arguments.done",
            # Session
            "session.created", "session.updated",
            # Buffers
            "input_audio_buffer.committed", "input_audio_buffer.cleared",
            # Conversation items lifecycle
            "conversation.item.created", "conversation.item.truncated",
            "conversation.item.done",  # Item concluído (formato GA 2026)
            # Rate limits (info, não erro)
            "rate_limits.updated",
            # Errors
            "error",
        }
        
        if etype not in KNOWN_EVENTS:
            logger.debug(f"OpenAI event received: {etype}", extra={
                "domain_uuid": self.config.domain_uuid,
                "event_data_preview": str(event)[:200],
            })
        
        # ===== ÁUDIO OUTPUT =====
        # COMPATIBILIDADE: API pode retornar response.audio.delta OU response.output_audio.delta
        # dependendo da versão/modelo. Suportamos ambos.
        if etype in ("response.audio.delta", "response.output_audio.delta"):
            audio_b64 = event.get("delta", "")
            audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
            
            # Agregação de logs - contar chunks, logar apenas no primeiro
            self._audio_chunks_received += 1
            self._audio_bytes_received += len(audio_bytes)
            
            # Log apenas no primeiro chunk de cada resposta
            if self._audio_chunks_received == 1:
                logger.info(
                    f"🔊 [OPENAI] First audio chunk: {len(audio_bytes)}B",
                    extra={"domain_uuid": self.config.domain_uuid}
                )
            
            return ProviderEvent(
                type=ProviderEventType.AUDIO_DELTA,
                data={"audio": audio_bytes},
                response_id=event.get("response_id"),
                item_id=event.get("item_id"),
            )
        
        # COMPATIBILIDADE: Suporta ambos os formatos de audio.done
        if etype in ("response.audio.done", "response.output_audio.done"):
            # Log agregado do total de áudio recebido nesta resposta
            logger.info(
                f"🔊 [OPENAI] Audio complete: {self._audio_chunks_received} chunks, {self._audio_bytes_received}B",
                extra={
                    "domain_uuid": self.config.domain_uuid,
                    "chunks": self._audio_chunks_received,
                    "bytes": self._audio_bytes_received,
                }
            )
            # Resetar contadores para próxima resposta
            self._audio_chunks_received = 0
            self._audio_bytes_received = 0
            
            return ProviderEvent(type=ProviderEventType.AUDIO_DONE, data={})
        
        # ===== TRANSCRIÇÃO DO ASSISTENTE =====
        # COMPATIBILIDADE: Suporta formatos antigo (response.audio_transcript.*) 
        # e novo GA (response.output_audio_transcript.*)
        if etype in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            return ProviderEvent(
                type=ProviderEventType.TRANSCRIPT_DELTA,
                data={"transcript": event.get("delta", "")}
            )
        
        if etype in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
            return ProviderEvent(
                type=ProviderEventType.TRANSCRIPT_DONE,
                data={"transcript": event.get("transcript", "")}
            )
        
        # ===== TRANSCRIÇÃO DO USUÁRIO (STT) =====
        if etype == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript", "")
            logger.debug(f"User transcript: {transcript[:50]}...", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(
                type=ProviderEventType.USER_TRANSCRIPT,
                data={"transcript": transcript}
            )
        
        if etype == "conversation.item.input_audio_transcription.failed":
            logger.warning("User audio transcription failed", extra={
                "domain_uuid": self.config.domain_uuid,
                "error": event.get("error"),
            })
            return None  # Não é erro crítico
        
        # ===== VAD (Voice Activity Detection) =====
        if etype == "input_audio_buffer.speech_started":
            logger.debug("Speech started (VAD)", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(type=ProviderEventType.SPEECH_STARTED, data={})
        
        if etype == "input_audio_buffer.speech_stopped":
            logger.debug("Speech stopped (VAD)", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(type=ProviderEventType.SPEECH_STOPPED, data={})
        
        # ===== RESPONSE LIFECYCLE =====
        if etype == "response.created":
            self._response_active = True
            logger.debug("Response started", extra={
                "domain_uuid": self.config.domain_uuid,
            })
            return ProviderEvent(type=ProviderEventType.RESPONSE_STARTED, data={})
        
        if etype == "response.done":
            self._response_active = False
            response = event.get("response", {})
            status = response.get("status", "completed")
            logger.debug(f"Response done: {status}", extra={
                "domain_uuid": self.config.domain_uuid,
                "status": status,
            })
            
            # Se há resultado de função pendente, solicitar nova resposta para processá-lo
            # Isso acontece quando a IA chamou função DURANTE uma resposta ativa
            if self._pending_function_result and self._ws:
                self._pending_function_result = False
                logger.info("Requesting response for pending function result", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
                # Usar create_task para não bloquear o event loop
                asyncio.create_task(self._ws.send(json.dumps({"type": "response.create"})))
            
            return ProviderEvent(
                type=ProviderEventType.RESPONSE_DONE,
                data={"status": status}
            )
        
        # ===== FUNCTION CALLS =====
        if etype == "response.function_call_arguments.done":
            func_name = event.get("name", "")
            try:
                arguments = json.loads(event.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}
            
            logger.info(f"Function call: {func_name}", extra={
                "domain_uuid": self.config.domain_uuid,
                "call_id": event.get("call_id"),
            })
            
            return ProviderEvent(
                type=ProviderEventType.FUNCTION_CALL,
                data={
                    "function_name": func_name,
                    "arguments": arguments,
                    "call_id": event.get("call_id", ""),
                }
            )
        
        # ===== ERRORS =====
        if etype == "error":
            error = event.get("error", {})
            error_code = error.get("code", "unknown")
            error_message = error.get("message", "Unknown error")
            
            # Erros não-críticos (esperados em alguns fluxos)
            non_critical_errors = [
                "response_cancel_not_active",  # Tentar cancelar quando não há resposta
                "conversation_already_has_active_response",  # Já tem resposta ativa
            ]
            
            if error_code in non_critical_errors:
                logger.warning(f"OpenAI non-critical: {error_code} - {error_message}", extra={
                    "domain_uuid": self.config.domain_uuid,
                })
                return None  # Ignorar, não é erro crítico
            
            # Log detalhado do erro para debug
            logger.error(f"OpenAI error: {error_code} - {error_message}", extra={
                "domain_uuid": self.config.domain_uuid,
                "error_type": error.get("type"),
                "error_param": error.get("param"),
                "full_error": json.dumps(error)[:500],
            })
            
            if error_code == "rate_limit_exceeded":
                return ProviderEvent(type=ProviderEventType.RATE_LIMITED, data={"error": error})
            
            return ProviderEvent(type=ProviderEventType.ERROR, data={"error": error})
        
        # ===== SESSION UPDATED - CONFIRMAÇÃO DA CONFIGURAÇÃO =====
        if etype == "session.updated":
            session_data = event.get("session", {})
            tools_count = len(session_data.get("tools", []))
            tools_names = [t.get("name") for t in session_data.get("tools", [])]
            
            logger.info("OpenAI session.updated - configuration confirmed", extra={
                "domain_uuid": self.config.domain_uuid,
                "model": session_data.get("model"),
                "voice": session_data.get("audio", {}).get("output", {}).get("voice"),
                "tools_count": tools_count,
                "tools_names": tools_names,
                "has_turn_detection": session_data.get("audio", {}).get("input", {}).get("turn_detection") is not None,
            })
            return None
        
        # ===== SESSION/META EVENTS (confirmações, ignorar) =====
        if etype in (
            "session.created",
            "input_audio_buffer.committed", "input_audio_buffer.cleared",
            "conversation.item.created", "conversation.item.done",  # Item lifecycle
            "response.content_part.added", "response.content_part.done",
            "response.output_item.added", "response.output_item.done",
            "rate_limits.updated",  # Info de rate limits (não é erro)
        ):
            return None  # Eventos de confirmação, não precisam de handling
        
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
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        logger.info("Disconnected from OpenAI Realtime", extra={
            "domain_uuid": self.config.domain_uuid,
            "session_id": self._session_id,
        })
