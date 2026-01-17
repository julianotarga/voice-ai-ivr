"""
Voice AI Application - ESL Handler

Cada chamada que conecta via ESL outbound recebe uma instância
desta classe para gerenciar a conversa.

Esta classe é o ponto de integração entre:
- FreeSWITCH (via ESL/greenswitch)
- RTP Bridge (áudio direto)
- AI Providers (ElevenLabs, OpenAI, Gemini)

Referências:
- https://github.com/EvoluxBR/greenswitch
- openspec/changes/refactor-esl-rtp-bridge/design.md
"""

import os
import logging
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, Callable
from datetime import datetime
from queue import Queue, Empty

import gevent
from greenswitch.esl import OutboundSession

from ..rtp import RTPBridge, RTPBridgeConfig, PayloadType
from ..session import RealtimeSession, RealtimeSessionConfig
from ..providers.base import RealtimeConfig
from ..config_loader import load_secretary_config

logger = logging.getLogger(__name__)

# Pool de threads para executar código asyncio em contexto gevent
_async_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="AsyncBridge")


class VoiceAIApplication:
    """
    Application handler para cada chamada ESL.
    
    Lifecycle:
    1. FreeSWITCH conecta ao ESL Server
    2. OutboundSession é criada
    3. VoiceAIApplication(session) é instanciada
    4. run() é chamado em uma greenlet separada
    5. Quando run() termina, sessão é encerrada
    
    Propriedades da sessão (via OutboundSession):
    - session.uuid: UUID do canal FreeSWITCH
    - session.call_uuid: UUID da chamada
    - session.caller_id_number: Caller ID
    - session.session_data: Dict com todas as variáveis do canal
    """
    
    def __init__(self, session: OutboundSession):
        """
        Args:
            session: OutboundSession do greenswitch
        """
        self.session = session
        self._start_time = datetime.utcnow()
        
        # Será preenchido em run()
        self._uuid: Optional[str] = None
        self._caller_id: Optional[str] = None
        self._domain_uuid: Optional[str] = None
        self._secretary_uuid: Optional[str] = None
        
        # Informações de mídia RTP
        self._remote_media_ip: Optional[str] = None
        self._remote_media_port: Optional[str] = None
        self._local_media_ip: Optional[str] = None
        self._local_media_port: Optional[str] = None
        
        # RTP Bridge
        self._rtp_bridge: Optional[RTPBridge] = None
        
        # AI Session (roda em asyncio)
        self._ai_session: Optional[RealtimeSession] = None
        self._ai_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ai_thread: Optional[threading.Thread] = None
        
        # Fila para áudio de saída (AI -> RTP)
        self._audio_out_queue: Queue = Queue(maxsize=100)
        
        # Estado
        self._is_speaking = False
        self._should_stop = False
    
    def run(self) -> None:
        """
        Entry point principal - chamado pelo greenswitch.
        
        Este método é executado em uma greenlet separada.
        Deve gerenciar toda a vida da chamada.
        """
        try:
            # 1. Conectar ao canal FreeSWITCH
            self._connect()
            
            # 2. Extrair variáveis do canal
            self._extract_channel_vars()
            
            logger.info(
                f"[{self._uuid}] Voice AI call started - "
                f"caller={self._caller_id}, domain={self._domain_uuid}"
            )
            
            # 3. Atender chamada
            self._answer()
            
            # 4. Iniciar RTP Bridge
            self._start_rtp_bridge()
            
            # 5. Iniciar sessão com IA (em thread asyncio separada)
            self._start_ai_session()
            
            # 6. Loop principal de conversa
            self._conversation_loop()
            
        except Exception as e:
            logger.exception(f"[{self._uuid}] Error in Voice AI call: {e}")
        finally:
            # 7. Cleanup
            self._cleanup()
    
    def _connect(self) -> None:
        """Conecta ao canal FreeSWITCH via ESL."""
        try:
            self.session.connect()
            self.session.myevents()
            
            # CRÍTICO: linger() mantém a sessão ESL ativa
            # Ref: greenswitch/examples/outbound_socket_example.py
            self.session.linger()
            
            self._uuid = self.session.uuid
            
            logger.debug(f"[{self._uuid}] ESL connected with linger")
            
        except Exception as e:
            logger.error(f"Failed to connect ESL session: {e}")
            raise
    
    def _extract_channel_vars(self) -> None:
        """Extrai variáveis importantes do canal."""
        data = self.session.session_data or {}
        
        self._caller_id = data.get("Caller-Caller-ID-Number", "unknown")
        
        # ✅ FIX: Suportar múltiplos nomes de variáveis
        # O dialplan pode usar VOICE_AI_DOMAIN_UUID ou domain_uuid
        self._domain_uuid = (
            data.get("variable_VOICE_AI_DOMAIN_UUID") or
            data.get("variable_domain_uuid") or
            data.get("variable_voiceai_domain_uuid")
        )
        self._secretary_uuid = (
            data.get("variable_VOICE_AI_SECRETARY_UUID") or
            data.get("variable_secretary_uuid") or
            data.get("variable_voiceai_secretary_uuid")
        )
        
        # Extrair info de mídia para RTP
        self._remote_media_ip = data.get("variable_remote_media_ip")
        self._remote_media_port = data.get("variable_remote_media_port")
        self._local_media_ip = data.get("variable_local_media_ip")
        self._local_media_port = data.get("variable_local_media_port")
        
        logger.info(
            f"[{self._uuid}] Media info: remote={self._remote_media_ip}:{self._remote_media_port}, "
            f"local={self._local_media_ip}:{self._local_media_port}"
        )
        
        # Log todas as variáveis em debug
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[{self._uuid}] Channel vars: {data}")
    
    def _answer(self) -> None:
        """Atende a chamada."""
        result = self.session.answer()
        logger.debug(f"[{self._uuid}] Answer result: {result}")
    
    def _start_rtp_bridge(self) -> None:
        """
        Inicializa RTP Bridge para áudio.
        
        IMPORTANTE: Para RTP direto funcionar, precisamos:
        1. Criar nosso socket UDP
        2. Obter o endereço do FreeSWITCH (remote_media_ip:remote_media_port)
        3. Configurar o bridge para enviar/receber desse endereço
        
        O FreeSWITCH já envia RTP para o endpoint configurado no SDP.
        Para interceptar, usamos uuid_media ou proxy_media.
        """
        logger.info(f"[{self._uuid}] Starting RTP Bridge...")
        
        # Obter endereço do FreeSWITCH para envio
        remote_ip = self._remote_media_ip or os.getenv("FREESWITCH_RTP_IP", "127.0.0.1")
        remote_port = int(self._remote_media_port or 0)
        
        config = RTPBridgeConfig(
            local_address=os.getenv("RTP_BIND_ADDRESS", "0.0.0.0"),
            remote_address=remote_ip,
            remote_rtp_port=remote_port,
            payload_type=PayloadType.PCMU,
            sample_rate=8000,
            jitter_min_ms=int(os.getenv("RTP_JITTER_MIN_MS", "60")),
            jitter_max_ms=int(os.getenv("RTP_JITTER_MAX_MS", "200")),
            jitter_target_ms=int(os.getenv("RTP_JITTER_TARGET_MS", "100")),
        )
        
        self._rtp_bridge = RTPBridge(
            config=config,
            on_audio_received=self._on_audio_from_freeswitch,
            on_underrun=self._on_buffer_underrun,
            on_error=self._on_rtp_error,
        )
        
        self._rtp_bridge.start()
        
        logger.info(
            f"[{self._uuid}] RTP Bridge started on port {self._rtp_bridge.local_rtp_port}, "
            f"remote={remote_ip}:{remote_port}"
        )
        
        # Redirecionar áudio do FreeSWITCH para nosso bridge
        # usando uuid_media (se disponível)
        self._redirect_rtp_to_bridge()
    
    def _redirect_rtp_to_bridge(self) -> None:
        """
        Redireciona RTP do FreeSWITCH para nosso bridge.
        
        NOTA IMPORTANTE:
        O FreeSWITCH envia RTP para o endpoint definido no SDP da chamada.
        Para interceptar/redirecionar, temos opções:
        
        1. uuid_media_reneg - Renegocia SDP (complexo)
        2. Usar mod_audio_fork junto com ESL (híbrido)
        3. Configurar proxy_media antes do answer
        
        ABORDAGEM ATUAL:
        Capturamos o IP/porta remota do FreeSWITCH e configuramos 
        nosso bridge para responder nesse endereço. O FreeSWITCH
        auto-detecta o endereço de retorno quando recebe nossos pacotes.
        
        Ref: https://freeswitch.org/confluence/display/FREESWITCH/mod_sofia
        """
        if not self._rtp_bridge:
            return
            
        # O RTP bridge já foi configurado com remote_address/port
        # Agora enviamos um pacote inicial para que FreeSWITCH
        # detecte nosso endereço (NAT traversal)
        
        rtp_port = self._rtp_bridge.local_rtp_port
        
        logger.info(
            f"[{self._uuid}] RTP Bridge configured, waiting for incoming packets..."
        )
        
        # Se temos o endereço remoto, podemos tentar enviar um pacote
        # de "keep-alive" para iniciar o fluxo
        if self._remote_media_ip and self._remote_media_port:
            try:
                # Enviar um pacote de silêncio para iniciar fluxo
                silence = bytes(160)  # 20ms de PCMU silence = 160 bytes
                self._rtp_bridge.send_audio(silence, marker=True)
                logger.info(f"[{self._uuid}] Initial RTP packet sent")
            except Exception as e:
                logger.warning(f"[{self._uuid}] Failed to send initial RTP: {e}")
        
        # Log variáveis de mídia para debug
        logger.info(
            f"[{self._uuid}] Media config: "
            f"remote={self._remote_media_ip}:{self._remote_media_port}, "
            f"local={self._local_media_ip}:{self._local_media_port}, "
            f"bridge_port={rtp_port}"
        )
    
    def _start_ai_session(self) -> None:
        """
        Inicializa sessão com AI provider em thread asyncio.
        
        Como greenswitch usa gevent e os AI providers usam asyncio,
        precisamos de uma thread separada com event loop asyncio.
        """
        logger.info(f"[{self._uuid}] Starting AI session...")
        
        # Criar thread com event loop asyncio
        self._ai_thread = threading.Thread(
            target=self._run_ai_loop,
            name=f"AISession-{self._uuid[:8]}",
            daemon=True,
        )
        self._ai_thread.start()
        
        # Aguardar inicialização
        timeout = 10  # segundos
        start = datetime.utcnow()
        while not self._ai_session and not self._should_stop:
            gevent.sleep(0.1)
            if (datetime.utcnow() - start).total_seconds() > timeout:
                raise RuntimeError("Timeout waiting for AI session")
        
        logger.info(f"[{self._uuid}] AI session started")
    
    def _run_ai_loop(self) -> None:
        """Executa loop asyncio para AI session em thread separada."""
        self._ai_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._ai_loop)
        
        try:
            self._ai_loop.run_until_complete(self._async_ai_session())
        except Exception as e:
            logger.exception(f"[{self._uuid}] AI loop error: {e}")
        finally:
            self._ai_loop.close()
    
    async def _async_ai_session(self) -> None:
        """Código asyncio para gerenciar AI session."""
        try:
            # Carregar configuração da secretária
            secretary_config = await self._load_secretary_config()
            
            if not secretary_config:
                logger.error(f"[{self._uuid}] Failed to load secretary config")
                return
            
            # Criar RealtimeSessionConfig
            config = RealtimeSessionConfig(
                domain_uuid=self._domain_uuid or "",
                call_uuid=self._uuid or "",
                caller_id=self._caller_id or "",
                secretary_uuid=self._secretary_uuid or "",
                secretary_name=secretary_config.get("secretary_name", "Assistant"),
                provider_name=secretary_config.get("provider_name", "elevenlabs"),
                system_prompt=secretary_config.get("system_prompt", ""),
                greeting=secretary_config.get("first_message"),
                voice=secretary_config.get("voice", "alloy"),
                freeswitch_sample_rate=8000,  # RTP usa 8kHz
            )
            
            # Criar RealtimeSession
            self._ai_session = RealtimeSession(
                config=config,
                on_audio_output=self._on_audio_from_ai,
                on_transcript=self._on_transcript,
                on_session_end=self._on_session_end,
            )
            
            # Iniciar sessão
            await self._ai_session.start()
            
            # Loop para processar áudio de entrada (FS -> AI)
            while not self._should_stop:
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.exception(f"[{self._uuid}] AI session error: {e}")
        finally:
            if self._ai_session:
                await self._ai_session.stop()
    
    async def _load_secretary_config(self) -> Optional[Dict[str, Any]]:
        """Carrega configuração da secretária do banco."""
        if not self._secretary_uuid or not self._domain_uuid:
            logger.warning(
                f"[{self._uuid}] Missing secretary_uuid or domain_uuid"
            )
            return None
        
        try:
            # Usar config_loader existente
            config = await load_secretary_config(
                domain_uuid=self._domain_uuid,
                secretary_uuid=self._secretary_uuid,
            )
            return config
        except Exception as e:
            logger.error(f"[{self._uuid}] Failed to load config: {e}")
            return None
    
    def _conversation_loop(self) -> None:
        """
        Loop principal de conversa.
        
        Processa:
        - Áudio de saída da fila (AI -> RTP -> FS)
        - Verificação de desconexão
        """
        logger.info(f"[{self._uuid}] Conversation loop started")
        
        try:
            while not self._should_stop:
                # Verificar se caller desligou
                try:
                    self.session.raise_if_disconnected()
                except Exception:
                    logger.info(f"[{self._uuid}] Caller disconnected")
                    break
                
                # Processar áudio de saída
                try:
                    audio = self._audio_out_queue.get(timeout=0.02)
                    if audio and self._rtp_bridge:
                        self._rtp_bridge.send_audio(audio, marker=not self._is_speaking)
                        self._is_speaking = True
                except Empty:
                    self._is_speaking = False
                
                # Yield para outras greenlets
                gevent.sleep(0)
                
        except Exception as e:
            logger.exception(f"[{self._uuid}] Conversation loop error: {e}")
    
    def _on_audio_from_freeswitch(self, audio: bytes) -> None:
        """
        Callback: Áudio recebido do FreeSWITCH via RTP.
        
        Envia para AI provider (precisa converter para asyncio).
        """
        if self._ai_session and self._ai_loop:
            try:
                # Agendar no event loop asyncio
                asyncio.run_coroutine_threadsafe(
                    self._ai_session.send_audio(audio),
                    self._ai_loop,
                )
            except Exception as e:
                logger.error(f"[{self._uuid}] Error sending audio to AI: {e}")
    
    def _on_audio_from_ai(self, audio: bytes) -> None:
        """
        Callback: Áudio recebido do AI provider.
        
        Enfileira para envio via RTP.
        """
        try:
            self._audio_out_queue.put_nowait(audio)
        except Exception:
            # Fila cheia, descartar áudio antigo
            try:
                self._audio_out_queue.get_nowait()
                self._audio_out_queue.put_nowait(audio)
            except Exception:
                pass
    
    def _on_transcript(self, role: str, text: str) -> None:
        """Callback: Transcrição recebida."""
        logger.info(f"[{self._uuid}] {role}: {text[:100]}...")
    
    def _on_session_end(self, reason: str) -> None:
        """Callback: Sessão AI encerrada."""
        logger.info(f"[{self._uuid}] AI session ended: {reason}")
        self._should_stop = True
    
    def _on_buffer_underrun(self) -> None:
        """Callback: Buffer underrun no RTP."""
        logger.warning(f"[{self._uuid}] RTP buffer underrun")
    
    def _on_rtp_error(self, error: Exception) -> None:
        """Callback: Erro no RTP Bridge."""
        logger.error(f"[{self._uuid}] RTP error: {error}")
    
    def _cleanup(self) -> None:
        """Cleanup no final da chamada."""
        self._should_stop = True
        
        duration = (datetime.utcnow() - self._start_time).total_seconds()
        
        logger.info(
            f"[{self._uuid}] Call ended - duration={duration:.1f}s"
        )
        
        # Encerrar RTP Bridge
        if self._rtp_bridge:
            try:
                self._rtp_bridge.stop()
            except Exception as e:
                logger.warning(f"[{self._uuid}] Error stopping RTP bridge: {e}")
        
        # Aguardar thread AI
        if self._ai_thread and self._ai_thread.is_alive():
            self._ai_thread.join(timeout=5.0)
        
        # Desligar se ainda conectado
        try:
            self.session.hangup()
        except Exception:
            pass
    
    # =========================================================================
    # Public API para controle externo
    # =========================================================================
    
    def transfer(self, destination: str) -> bool:
        """
        Transfere chamada para destino.
        
        Args:
            destination: Destino (ex: "1000", "group/sales")
        
        Returns:
            True se transfer iniciado
        """
        try:
            logger.info(f"[{self._uuid}] Transferring to {destination}")
            self.session.bridge(destination, block=False)
            return True
        except Exception as e:
            logger.error(f"[{self._uuid}] Transfer failed: {e}")
            return False
    
    def hangup(self, cause: str = "NORMAL_CLEARING") -> None:
        """Desliga a chamada."""
        self._should_stop = True
        try:
            logger.info(f"[{self._uuid}] Hanging up: {cause}")
            self.session.hangup(cause)
        except Exception as e:
            logger.warning(f"[{self._uuid}] Hangup error: {e}")
    
    def play_audio(self, path: str, block: bool = True) -> None:
        """Reproduz arquivo de áudio."""
        try:
            self.session.playback(path, block=block)
        except Exception as e:
            logger.warning(f"[{self._uuid}] Playback error: {e}")
    
    @property
    def uuid(self) -> Optional[str]:
        """UUID do canal FreeSWITCH."""
        return self._uuid
    
    @property
    def caller_id(self) -> Optional[str]:
        """Caller ID number."""
        return self._caller_id
    
    @property
    def domain_uuid(self) -> Optional[str]:
        """Domain UUID (tenant)."""
        return self._domain_uuid
    
    @property
    def duration_seconds(self) -> float:
        """Duração da chamada em segundos."""
        return (datetime.utcnow() - self._start_time).total_seconds()
