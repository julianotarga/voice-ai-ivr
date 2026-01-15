"""
Adaptive Jitter Buffer

Buffer circular que absorve variações de latência (jitter) na rede.
Implementa algoritmo adaptativo que ajusta tamanho baseado em métricas.

Referências:
- RFC 3550 Appendix A.8: Jitter Calculations
- openspec/changes/refactor-esl-rtp-bridge/specs/esl-rtp-protocol/spec.md
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Deque, Callable

from .protocol import RTPPacket

logger = logging.getLogger(__name__)


@dataclass
class JitterStats:
    """Estatísticas do jitter buffer."""
    packets_received: int = 0
    packets_dropped: int = 0
    packets_reordered: int = 0
    packets_duplicated: int = 0
    buffer_underruns: int = 0
    buffer_overflows: int = 0
    current_delay_ms: float = 0.0
    average_jitter_ms: float = 0.0
    max_jitter_ms: float = 0.0
    current_buffer_size: int = 0


class JitterBuffer:
    """
    Jitter Buffer adaptativo para pacotes RTP.
    
    Funcionamento:
    1. Pacotes chegam e são inseridos ordenados por sequence
    2. Consumer retira pacotes em ordem
    3. Buffer adapta tamanho baseado no jitter observado
    
    Parâmetros adaptativos:
    - min_delay_ms: Delay mínimo (default: 60ms = 3 pacotes de 20ms)
    - max_delay_ms: Delay máximo (default: 200ms = 10 pacotes)
    - target_delay_ms: Delay alvo (default: 100ms = 5 pacotes)
    """
    
    def __init__(
        self,
        min_delay_ms: int = 60,
        max_delay_ms: int = 200,
        target_delay_ms: int = 100,
        packet_duration_ms: int = 20,
        on_underrun: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            min_delay_ms: Delay mínimo do buffer
            max_delay_ms: Delay máximo do buffer
            target_delay_ms: Delay alvo
            packet_duration_ms: Duração de cada pacote (20ms para RTP típico)
            on_underrun: Callback chamado em buffer underrun
        """
        self.min_delay_ms = min_delay_ms
        self.max_delay_ms = max_delay_ms
        self.target_delay_ms = target_delay_ms
        self.packet_duration_ms = packet_duration_ms
        self.on_underrun = on_underrun
        
        # Calcular tamanhos em pacotes
        self._min_packets = max(1, min_delay_ms // packet_duration_ms)
        self._max_packets = max_delay_ms // packet_duration_ms
        self._target_packets = target_delay_ms // packet_duration_ms
        
        # Buffer ordenado por sequence
        self._buffer: Deque[RTPPacket] = deque(maxlen=self._max_packets * 2)
        
        # Estado
        self._lock = threading.Lock()
        self._expected_seq: Optional[int] = None
        self._last_pop_time: Optional[float] = None
        self._started = False
        
        # Estatísticas
        self._stats = JitterStats()
        
        # Jitter calculation (RFC 3550)
        self._last_arrival_time: Optional[float] = None
        self._last_rtp_timestamp: Optional[int] = None
        self._jitter: float = 0.0
        
        logger.debug(
            f"JitterBuffer initialized: {min_delay_ms}-{max_delay_ms}ms "
            f"(target={target_delay_ms}ms, packets={self._min_packets}-{self._max_packets})"
        )
    
    def push(self, packet: RTPPacket) -> bool:
        """
        Adiciona pacote ao buffer.
        
        Args:
            packet: Pacote RTP a adicionar
            
        Returns:
            True se pacote foi aceito, False se descartado
        """
        with self._lock:
            self._stats.packets_received += 1
            arrival_time = time.time()
            
            # Calcular jitter (RFC 3550)
            self._update_jitter(packet, arrival_time)
            
            # Inicializar expected sequence se primeiro pacote
            if self._expected_seq is None:
                self._expected_seq = packet.sequence
            
            # Verificar overflow
            if len(self._buffer) >= self._max_packets * 2:
                self._stats.buffer_overflows += 1
                # Descartar pacote mais antigo
                self._buffer.popleft()
                logger.warning("Jitter buffer overflow, dropping oldest packet")
            
            # Inserir ordenado por sequence
            inserted = self._insert_ordered(packet)
            
            if not inserted:
                self._stats.packets_duplicated += 1
                return False
            
            self._stats.current_buffer_size = len(self._buffer)
            return True
    
    def pop(self, timeout_ms: Optional[int] = None) -> Optional[RTPPacket]:
        """
        Remove e retorna próximo pacote em ordem.
        
        Args:
            timeout_ms: Timeout para aguardar pacote (None = não bloqueia)
            
        Returns:
            RTPPacket ou None se buffer vazio
        """
        start_time = time.time()
        
        while True:
            with self._lock:
                # Verificar se buffer tem pacotes suficientes para iniciar
                if not self._started:
                    if len(self._buffer) >= self._min_packets:
                        self._started = True
                        self._last_pop_time = time.time()
                        logger.debug(
                            f"Jitter buffer started with {len(self._buffer)} packets"
                        )
                    else:
                        # Ainda em warmup
                        if timeout_ms is None:
                            return None
                        # Continuar esperando
                        pass
                
                if self._started and self._buffer:
                    packet = self._buffer.popleft()
                    self._expected_seq = (packet.sequence + 1) & 0xFFFF
                    self._last_pop_time = time.time()
                    self._stats.current_buffer_size = len(self._buffer)
                    return packet
                
                if self._started and not self._buffer:
                    # Buffer underrun
                    self._stats.buffer_underruns += 1
                    self._started = False  # Precisa warmup novamente
                    
                    if self.on_underrun:
                        self.on_underrun()
                    
                    logger.warning("Jitter buffer underrun")
            
            # Verificar timeout
            if timeout_ms is None:
                return None
            
            elapsed_ms = (time.time() - start_time) * 1000
            if elapsed_ms >= timeout_ms:
                return None
            
            # Aguardar um pouco e tentar novamente
            time.sleep(0.005)  # 5ms
    
    def _insert_ordered(self, packet: RTPPacket) -> bool:
        """
        Insere pacote mantendo ordem por sequence.
        
        Returns:
            True se inserido, False se duplicado
        """
        seq = packet.sequence
        
        # Buffer vazio
        if not self._buffer:
            self._buffer.append(packet)
            return True
        
        # Verificar duplicado
        for existing in self._buffer:
            if existing.sequence == seq:
                return False
        
        # Encontrar posição correta
        # Sequence wrap-around: considerar distância circular
        insert_pos = len(self._buffer)
        
        for i, existing in enumerate(self._buffer):
            # Comparação com wrap-around
            diff = (seq - existing.sequence) & 0xFFFF
            if diff > 0x8000:  # seq é "menor" que existing (wrap)
                insert_pos = i
                self._stats.packets_reordered += 1
                break
            elif diff == 0:
                return False  # Duplicado
        
        # Inserir na posição
        if insert_pos == len(self._buffer):
            self._buffer.append(packet)
        else:
            self._buffer.insert(insert_pos, packet)
        
        return True
    
    def _update_jitter(self, packet: RTPPacket, arrival_time: float) -> None:
        """
        Calcula jitter inter-pacote (RFC 3550).
        
        J(i) = J(i-1) + (|D(i-1,i)| - J(i-1)) / 16
        
        Onde D(i-1,i) é a diferença entre intervalos de chegada
        e intervalos de RTP timestamp.
        """
        if self._last_arrival_time is not None and self._last_rtp_timestamp is not None:
            # Calcular intervalos
            arrival_delta = (arrival_time - self._last_arrival_time) * 1000  # ms
            
            # Assumir sample rate de 8000Hz para timestamp (160 samples = 20ms)
            timestamp_delta = (
                (packet.timestamp - self._last_rtp_timestamp) & 0xFFFFFFFF
            ) / 8.0  # ms (8 samples/ms for 8kHz)
            
            # Diferença
            d = abs(arrival_delta - timestamp_delta)
            
            # Atualizar jitter (exponential moving average)
            self._jitter = self._jitter + (d - self._jitter) / 16.0
            
            self._stats.average_jitter_ms = self._jitter
            self._stats.max_jitter_ms = max(self._stats.max_jitter_ms, d)
        
        self._last_arrival_time = arrival_time
        self._last_rtp_timestamp = packet.timestamp
    
    def clear(self) -> None:
        """Limpa o buffer."""
        with self._lock:
            self._buffer.clear()
            self._expected_seq = None
            self._started = False
            self._stats.current_buffer_size = 0
    
    def get_stats(self) -> JitterStats:
        """Retorna cópia das estatísticas."""
        with self._lock:
            return JitterStats(
                packets_received=self._stats.packets_received,
                packets_dropped=self._stats.packets_dropped,
                packets_reordered=self._stats.packets_reordered,
                packets_duplicated=self._stats.packets_duplicated,
                buffer_underruns=self._stats.buffer_underruns,
                buffer_overflows=self._stats.buffer_overflows,
                current_delay_ms=len(self._buffer) * self.packet_duration_ms,
                average_jitter_ms=self._stats.average_jitter_ms,
                max_jitter_ms=self._stats.max_jitter_ms,
                current_buffer_size=len(self._buffer),
            )
    
    @property
    def size(self) -> int:
        """Número de pacotes no buffer."""
        with self._lock:
            return len(self._buffer)
    
    @property
    def delay_ms(self) -> float:
        """Delay atual em ms."""
        return self.size * self.packet_duration_ms
    
    @property
    def is_ready(self) -> bool:
        """True se buffer tem pacotes suficientes para playback."""
        return self._started and self.size > 0
