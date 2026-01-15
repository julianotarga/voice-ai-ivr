"""
RTP Bridge

Ponte bidirecional de áudio RTP entre FreeSWITCH e AI Providers.
Gerencia sockets UDP, jitter buffer, e callbacks de áudio.

Arquitetura:
    FreeSWITCH ◄─── RTP ───► RTPBridge ◄─── callback ───► AI Provider
    
Referências:
- RFC 3550: RTP Protocol
- openspec/changes/refactor-esl-rtp-bridge/design.md
"""

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional, Callable

from .protocol import RTPPacket, RTPPacketBuilder, PayloadType
from .jitter_buffer import JitterBuffer, JitterStats
from .port_pool import get_port_pool

logger = logging.getLogger(__name__)


@dataclass
class RTPBridgeConfig:
    """Configuração do RTP Bridge."""
    # Local bind
    local_address: str = "0.0.0.0"
    
    # Remote (FreeSWITCH)
    remote_address: Optional[str] = None
    remote_rtp_port: Optional[int] = None
    
    # Codec
    payload_type: int = PayloadType.PCMU
    sample_rate: int = 8000
    
    # Jitter buffer
    jitter_min_ms: int = 60
    jitter_max_ms: int = 200
    jitter_target_ms: int = 100
    
    # Timeouts
    recv_timeout_ms: int = 5000
    silence_timeout_ms: int = 30000


class RTPBridge:
    """
    Ponte RTP bidirecional.
    
    Uso típico:
        bridge = RTPBridge(config, on_audio_received=callback)
        bridge.start()
        # ...
        bridge.send_audio(audio_bytes)
        # ...
        bridge.stop()
    
    Callbacks:
    - on_audio_received(audio_bytes): Chamado quando áudio chega do FreeSWITCH
    - on_underrun(): Chamado quando jitter buffer tem underrun
    - on_error(exception): Chamado em erro fatal
    """
    
    def __init__(
        self,
        config: RTPBridgeConfig,
        on_audio_received: Optional[Callable[[bytes], None]] = None,
        on_underrun: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ):
        """
        Args:
            config: Configuração do bridge
            on_audio_received: Callback para áudio recebido
            on_underrun: Callback para buffer underrun
            on_error: Callback para erros
        """
        self.config = config
        self.on_audio_received = on_audio_received
        self.on_underrun = on_underrun
        self.on_error = on_error
        
        # Alocar portas
        ports = get_port_pool().allocate()
        if not ports:
            raise RuntimeError("No RTP ports available")
        
        self.local_rtp_port, self.local_rtcp_port = ports
        
        # Sockets
        self._rtp_socket: Optional[socket.socket] = None
        
        # Jitter buffer para áudio recebido
        self._jitter_buffer = JitterBuffer(
            min_delay_ms=config.jitter_min_ms,
            max_delay_ms=config.jitter_max_ms,
            target_delay_ms=config.jitter_target_ms,
            on_underrun=self._handle_underrun,
        )
        
        # Builder para pacotes de saída
        self._packet_builder = RTPPacketBuilder(
            payload_type=config.payload_type,
            sample_rate=config.sample_rate,
        )
        
        # Estado
        self._running = False
        self._recv_thread: Optional[threading.Thread] = None
        self._consumer_thread: Optional[threading.Thread] = None
        
        # Métricas
        self._packets_sent = 0
        self._packets_received = 0
        self._bytes_sent = 0
        self._bytes_received = 0
        self._last_recv_time: Optional[float] = None
        
        logger.info(
            f"RTPBridge created: local={config.local_address}:{self.local_rtp_port}, "
            f"remote={config.remote_address}:{config.remote_rtp_port}"
        )
    
    def start(self) -> None:
        """Inicia o bridge (threads de recepção e consumo)."""
        if self._running:
            return
        
        logger.info(f"Starting RTPBridge on port {self.local_rtp_port}")
        
        # Criar socket RTP
        self._rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rtp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._rtp_socket.bind((self.config.local_address, self.local_rtp_port))
        self._rtp_socket.settimeout(1.0)  # Timeout para verificar _running
        
        self._running = True
        
        # Thread para receber pacotes
        self._recv_thread = threading.Thread(
            target=self._receive_loop,
            name=f"RTPRecv-{self.local_rtp_port}",
            daemon=True,
        )
        self._recv_thread.start()
        
        # Thread para consumir jitter buffer
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop,
            name=f"RTPConsumer-{self.local_rtp_port}",
            daemon=True,
        )
        self._consumer_thread.start()
        
        logger.debug("RTPBridge started")
    
    def stop(self) -> None:
        """Para o bridge graciosamente."""
        if not self._running:
            return
        
        logger.info(f"Stopping RTPBridge on port {self.local_rtp_port}")
        
        self._running = False
        
        # Aguardar threads
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=2.0)
        
        if self._consumer_thread and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=2.0)
        
        # Fechar socket
        if self._rtp_socket:
            try:
                self._rtp_socket.close()
            except Exception:
                pass
            self._rtp_socket = None
        
        # Liberar porta
        get_port_pool().release(self.local_rtp_port)
        
        # Limpar buffer
        self._jitter_buffer.clear()
        
        logger.info(
            f"RTPBridge stopped: sent={self._packets_sent} pkts/{self._bytes_sent} bytes, "
            f"recv={self._packets_received} pkts/{self._bytes_received} bytes"
        )
    
    def send_audio(self, audio: bytes, marker: bool = False) -> bool:
        """
        Envia áudio para FreeSWITCH via RTP.
        
        Args:
            audio: Bytes de áudio (payload RTP, ex: PCMU encoded)
            marker: True se primeiro pacote de novo talkspurt
            
        Returns:
            True se enviado com sucesso
        """
        if not self._running or not self._rtp_socket:
            return False
        
        if not self.config.remote_address or not self.config.remote_rtp_port:
            logger.warning("Remote address not set, cannot send RTP")
            return False
        
        try:
            # Construir pacote RTP
            packet = self._packet_builder.build(audio, marker=marker)
            
            # Enviar
            data = packet.to_bytes()
            self._rtp_socket.sendto(
                data,
                (self.config.remote_address, self.config.remote_rtp_port)
            )
            
            self._packets_sent += 1
            self._bytes_sent += len(data)
            
            return True
            
        except Exception as e:
            logger.error(f"Error sending RTP packet: {e}")
            return False
    
    def set_remote(self, address: str, port: int) -> None:
        """
        Define endereço remoto (FreeSWITCH).
        
        Chamado quando recebemos o primeiro pacote do FreeSWITCH
        ou quando obtemos o SDP.
        """
        self.config.remote_address = address
        self.config.remote_rtp_port = port
        logger.info(f"Remote RTP set to {address}:{port}")
    
    def _receive_loop(self) -> None:
        """Loop de recepção de pacotes RTP."""
        logger.debug("RTP receive loop started")
        
        while self._running:
            try:
                # Receber pacote
                data, addr = self._rtp_socket.recvfrom(2048)
                
                # Auto-detect remote address
                if not self.config.remote_address:
                    self.set_remote(addr[0], addr[1])
                
                # Parse pacote
                packet = RTPPacket.parse(data)
                
                self._packets_received += 1
                self._bytes_received += len(data)
                self._last_recv_time = time.time()
                
                # Adicionar ao jitter buffer
                self._jitter_buffer.push(packet)
                
            except socket.timeout:
                # Verificar timeout de silêncio
                if self._last_recv_time:
                    silence = (time.time() - self._last_recv_time) * 1000
                    if silence > self.config.silence_timeout_ms:
                        logger.warning(f"Silence timeout ({silence:.0f}ms)")
                        # Não encerrar, apenas logar
                continue
                
            except Exception as e:
                if self._running:
                    logger.error(f"Error receiving RTP: {e}")
        
        logger.debug("RTP receive loop ended")
    
    def _consumer_loop(self) -> None:
        """Loop que consome jitter buffer e chama callback."""
        logger.debug("RTP consumer loop started")
        
        packet_interval_s = self.config.jitter_min_ms / 1000 / 3  # ~20ms por pacote
        
        while self._running:
            try:
                # Tentar obter pacote do buffer
                packet = self._jitter_buffer.pop(timeout_ms=int(packet_interval_s * 1000))
                
                if packet and self.on_audio_received:
                    self.on_audio_received(packet.payload)
                
                if not packet:
                    # Sleep se não há pacotes
                    time.sleep(packet_interval_s)
                    
            except Exception as e:
                if self._running:
                    logger.error(f"Error in consumer loop: {e}")
                    if self.on_error:
                        self.on_error(e)
        
        logger.debug("RTP consumer loop ended")
    
    def _handle_underrun(self) -> None:
        """Callback interno para buffer underrun."""
        logger.warning("RTP jitter buffer underrun")
        if self.on_underrun:
            try:
                self.on_underrun()
            except Exception as e:
                logger.error(f"Error in underrun callback: {e}")
    
    def get_stats(self) -> JitterStats:
        """Retorna estatísticas do jitter buffer."""
        return self._jitter_buffer.get_stats()
    
    @property
    def is_running(self) -> bool:
        """True se bridge está ativo."""
        return self._running
    
    @property
    def is_receiving(self) -> bool:
        """True se está recebendo pacotes recentemente."""
        if not self._last_recv_time:
            return False
        return (time.time() - self._last_recv_time) < 5.0
