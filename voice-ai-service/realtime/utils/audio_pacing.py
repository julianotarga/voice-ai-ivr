"""
Audio Pacing - Controla o timing de envio de áudio para evitar bursts.

Este módulo implementa pacing baseado em "lead tracking" para garantir
que os pacotes de áudio sejam enviados em um ritmo constante, evitando
bursts que causam "concealed samples" e robotização no cliente.

Ref: 
- https://github.com/rexologue/xtts-stream/blob/main/src/xtts_stream/api/service/pacing.py
- https://github.com/dograh-hq/dograh/blob/main/api/services/telephony/stasis_rtp_transport.py

Conceito chave:
- Não usa asyncio.sleep(20ms) fixo (impreciso)
- Calcula quanto "à frente" do clock real já enviamos
- Só espera quando necessário para manter ritmo constante

Uso:
    pacer = AudioPacer(sample_rate=8000, bytes_per_sample=1)  # G.711 PCMU
    
    async def send_audio_loop():
        while True:
            chunk = await get_next_chunk()
            await pacer.pace(len(chunk))  # Espera se necessário
            await websocket.send(chunk)
            pacer.on_sent(len(chunk))
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AudioPacer:
    """
    Controla o timing de envio de áudio para evitar bursts.
    
    Baseado na técnica de "lead tracking" usada em projetos como xtts-stream:
    - Mantém um contador de quanto áudio já foi "enviado" (em ms)
    - Compara com o tempo real decorrido
    - Se estamos muito "à frente", espera antes de enviar o próximo chunk
    
    Isso evita que enviemos todos os chunks de uma vez (burst) e garante
    um fluxo constante de pacotes para o FreeSWITCH/cliente.
    
    Attrs:
        sample_rate: Taxa de amostragem do áudio (Hz)
        bytes_per_sample: Bytes por sample (1 para G.711, 2 para L16)
        target_lead_ms: Quanto podemos ficar "à frente" do clock real (ms)
        hysteresis_ms: Margem de tolerância para evitar micro-sleeps (ms)
        max_wait_ms: Tempo máximo de espera por vez (evita travamentos)
    """
    
    sample_rate: int = 8000
    bytes_per_sample: int = 1  # 1 para G.711 PCMU/PCMA, 2 para L16 PCM
    target_lead_ms: float = 60.0  # Buffer de "à frente" permitido
    hysteresis_ms: float = 5.0  # Margem de tolerância
    max_wait_ms: float = 50.0  # Máximo 50ms de espera por vez
    
    # Estado interno (inicializado em __post_init__)
    _bytes_per_ms: float = field(init=False, default=0.0)
    _start_time: float = field(init=False, default=0.0)
    _sent_duration_ms: float = field(init=False, default=0.0)
    _chunks_sent: int = field(init=False, default=0)
    _total_wait_ms: float = field(init=False, default=0.0)
    _is_active: bool = field(init=False, default=False)
    
    def __post_init__(self) -> None:
        """Calcula constantes baseadas na configuração."""
        # Bytes por milissegundo
        # Ex: 8000 Hz * 1 byte/sample = 8000 bytes/s = 8 bytes/ms
        self._bytes_per_ms = (self.sample_rate * self.bytes_per_sample) / 1000.0
        self._reset_state()
    
    def _reset_state(self) -> None:
        """Reseta estado interno."""
        self._start_time = 0.0
        self._sent_duration_ms = 0.0
        self._chunks_sent = 0
        self._total_wait_ms = 0.0
        self._is_active = False
    
    def start(self) -> None:
        """Inicia o pacer (chamar antes do primeiro chunk)."""
        self._reset_state()
        self._start_time = time.monotonic()
        self._is_active = True
        logger.debug(
            f"[AUDIO_PACER] Started: {self.sample_rate}Hz, "
            f"{self.bytes_per_sample}B/sample, target_lead={self.target_lead_ms}ms"
        )
    
    def stop(self) -> dict:
        """
        Para o pacer e retorna estatísticas.
        
        Returns:
            Dict com estatísticas de uso
        """
        if not self._is_active:
            return {}
        
        self._is_active = False
        elapsed_ms = (time.monotonic() - self._start_time) * 1000.0
        
        stats = {
            "chunks_sent": self._chunks_sent,
            "audio_duration_ms": round(self._sent_duration_ms, 1),
            "elapsed_ms": round(elapsed_ms, 1),
            "total_wait_ms": round(self._total_wait_ms, 1),
            "avg_wait_ms": round(
                self._total_wait_ms / self._chunks_sent, 2
            ) if self._chunks_sent > 0 else 0,
        }
        
        logger.debug(f"[AUDIO_PACER] Stopped: {stats}")
        return stats
    
    def reset(self) -> None:
        """Reseta o pacer para nova resposta (mantém ativo)."""
        was_active = self._is_active
        self._reset_state()
        if was_active:
            self._start_time = time.monotonic()
            self._is_active = True
            logger.debug("[AUDIO_PACER] Reset for new response")
    
    def duration_from_bytes(self, nbytes: int) -> float:
        """
        Calcula duração em ms a partir do tamanho do chunk.
        
        Args:
            nbytes: Número de bytes
            
        Returns:
            Duração em milissegundos
        """
        if self._bytes_per_ms == 0:
            return 0.0
        return nbytes / self._bytes_per_ms
    
    def get_lead_ms(self) -> float:
        """
        Retorna quanto estamos "à frente" do clock real.
        
        Returns:
            Diferença em ms (positivo = à frente, negativo = atrasado)
        """
        if not self._is_active:
            return 0.0
        
        elapsed_ms = (time.monotonic() - self._start_time) * 1000.0
        return self._sent_duration_ms - elapsed_ms
    
    async def pace(self, chunk_bytes: int) -> float:
        """
        Espera antes de enviar se estiver muito à frente.
        
        Este método deve ser chamado ANTES de enviar cada chunk.
        Ele calcula quanto tempo esperar (se necessário) para manter
        o ritmo de envio constante.
        
        Args:
            chunk_bytes: Tamanho do chunk que será enviado
            
        Returns:
            Tempo de espera aplicado (em ms), 0 se nenhum
        """
        if not self._is_active:
            # Auto-start se não iniciado
            self.start()
        
        # Quanto tempo real passou desde o início?
        elapsed_ms = (time.monotonic() - self._start_time) * 1000.0
        
        # Quanto estamos "à frente" do clock real?
        ahead_ms = self._sent_duration_ms - elapsed_ms
        
        # Se estamos mais de target_lead_ms à frente (com histerese), esperar
        threshold = self.target_lead_ms - self.hysteresis_ms
        
        if ahead_ms > threshold:
            # Calcular quanto esperar
            wait_ms = ahead_ms - self.target_lead_ms
            wait_ms = max(0.0, min(wait_ms, self.max_wait_ms))  # Limitar
            
            if wait_ms > 0.5:  # Só vale a pena esperar se > 0.5ms
                await asyncio.sleep(wait_ms / 1000.0)
                self._total_wait_ms += wait_ms
                return wait_ms
        
        return 0.0
    
    def on_sent(self, chunk_bytes: int) -> None:
        """
        Registra que um chunk foi enviado.
        
        Deve ser chamado DEPOIS de enviar cada chunk.
        Atualiza o contador de "duração enviada" para o cálculo de lead.
        
        Args:
            chunk_bytes: Tamanho do chunk que foi enviado
        """
        chunk_duration_ms = self.duration_from_bytes(chunk_bytes)
        self._sent_duration_ms += chunk_duration_ms
        self._chunks_sent += 1
        
        # Log periódico (a cada 50 chunks = ~1 segundo @ 20ms)
        if self._chunks_sent % 50 == 0:
            lead_ms = self.get_lead_ms()
            logger.debug(
                f"[AUDIO_PACER] Progress: {self._chunks_sent} chunks, "
                f"sent={self._sent_duration_ms:.0f}ms, lead={lead_ms:.1f}ms"
            )


# ============================================================================
# Factory functions para casos comuns
# ============================================================================

def create_pcmu_pacer(target_lead_ms: float = 60.0) -> AudioPacer:
    """
    Cria um pacer configurado para G.711 PCMU (8kHz, 1 byte/sample).
    
    Args:
        target_lead_ms: Quanto à frente do clock real podemos ficar
        
    Returns:
        AudioPacer configurado para PCMU
    """
    return AudioPacer(
        sample_rate=8000,
        bytes_per_sample=1,  # G.711 = 1 byte/sample
        target_lead_ms=target_lead_ms,
    )


def create_l16_pacer(sample_rate: int = 8000, target_lead_ms: float = 60.0) -> AudioPacer:
    """
    Cria um pacer configurado para L16 PCM (16-bit linear).
    
    Args:
        sample_rate: Taxa de amostragem (8000, 16000, 24000, etc)
        target_lead_ms: Quanto à frente do clock real podemos ficar
        
    Returns:
        AudioPacer configurado para L16
    """
    return AudioPacer(
        sample_rate=sample_rate,
        bytes_per_sample=2,  # L16 = 2 bytes/sample
        target_lead_ms=target_lead_ms,
    )
