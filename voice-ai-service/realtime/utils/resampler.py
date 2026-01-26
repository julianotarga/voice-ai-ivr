"""
Audio Resampler para conversão de sample rates.

Referências:
- openspec/changes/voice-ai-realtime/design.md: Decision 5 (Resampling)
- .context/docs/data-flow.md: PCM 16kHz do FreeSWITCH

Sample rates:
- FreeSWITCH: 16kHz
- OpenAI Realtime: 24kHz  
- ElevenLabs: 16kHz
- Gemini: 16kHz
"""

import logging
from math import gcd
from typing import Optional

import numpy as np

# IMPORTANTE: Importar scipy.signal no topo para evitar delay de 7.5s
# na primeira chamada do resample. O import lazy dentro do método process()
# causava latência enorme no início da sessão.
try:
    from scipy import signal as scipy_signal
    SCIPY_AVAILABLE = True
except ImportError:
    scipy_signal = None
    SCIPY_AVAILABLE = False

logger = logging.getLogger(__name__)


def _warmup_scipy():
    """
    Força a inicialização/JIT do scipy.signal.resample_poly.
    
    O scipy/numpy pode levar vários segundos na primeira execução devido a:
    1. Carregamento lazy de módulos internos
    2. Compilação JIT de funções numéricas
    3. Alocação de buffers internos
    
    Chamando uma vez com dados dummy durante o import, evitamos esse delay
    na primeira chamada real durante uma ligação.
    
    IMPORTANTE: Usar dados aleatórios (não zeros) para forçar todos os 
    caminhos de código - algoritmos podem otimizar arrays de zeros.
    """
    if not SCIPY_AVAILABLE or scipy_signal is None:
        return
    
    import time
    start_time = time.time()
    
    try:
        # Simular um resample típico: 2400 samples @ 24kHz -> 800 samples @ 8kHz
        # IMPORTANTE: Usar ruído aleatório ao invés de zeros para forçar JIT completo
        # Arrays de zeros podem ser otimizados e não ativar todos os caminhos de código
        np.random.seed(42)  # Seed fixo para reprodutibilidade
        dummy_input = np.random.randn(2400).astype(np.float32) * 1000  # Simular áudio real
        _ = scipy_signal.resample_poly(dummy_input, 1, 3)
        
        elapsed = time.time() - start_time
        if elapsed > 0.5:
            # Só logar se demorou mais de 500ms (indica JIT real)
            logger.info(f"✅ scipy.signal warmup complete ({elapsed:.1f}s) - resample_poly ready")
        else:
            logger.debug(f"✅ scipy.signal warmup complete ({elapsed:.3f}s)")
    except Exception as e:
        logger.warning(f"⚠️ scipy.signal warmup failed: {e}")


# Executar warmup imediatamente ao importar o módulo
_warmup_scipy()


class Resampler:
    """
    Resampler eficiente para streaming de áudio.
    Usa scipy.signal.resample_poly para qualidade.
    """
    
    def __init__(self, input_rate: int, output_rate: int):
        self.input_rate = input_rate
        self.output_rate = output_rate
        
        g = gcd(input_rate, output_rate)
        self.up = output_rate // g
        self.down = input_rate // g
        
        self.needs_resample = (input_rate != output_rate)
        
        logger.debug(f"Resampler: {input_rate}Hz -> {output_rate}Hz (up={self.up}, down={self.down})")
    
    def process(self, audio_bytes: bytes) -> bytes:
        """Resamplea chunk de áudio PCM16."""
        if not audio_bytes or not self.needs_resample:
            return audio_bytes
        
        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        if len(samples) == 0:
            return b""
        
        # Usar scipy pré-carregado no topo do módulo para evitar delay
        if SCIPY_AVAILABLE and scipy_signal is not None:
            float_samples = samples.astype(np.float32)
            resampled = scipy_signal.resample_poly(float_samples, self.up, self.down)
            return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
        else:
            return self._simple_resample(samples).tobytes()
    
    def _simple_resample(self, samples: np.ndarray) -> np.ndarray:
        """Fallback: interpolação linear."""
        new_length = int(len(samples) * self.up / self.down)
        indices = np.linspace(0, len(samples) - 1, new_length)
        return np.interp(indices, np.arange(len(samples)), samples).astype(np.int16)


class AudioBuffer:
    """
    Buffer de áudio com warmup para playback suave.
    
    Baseado em: https://github.com/os11k/freeswitch-elevenlabs-bridge
    
    O warmup acumula áudio inicial antes de começar a enviar,
    evitando cortes e garantindo playback contínuo.
    """
    
    def __init__(
        self, 
        warmup_ms: int = 600,  # 600ms para evitar stuttering com jitter de rede (AUMENTADO 2026-01-25)
        sample_rate: int = 16000,
        bytes_per_sample: int = 2  # PCM16
    ):
        """
        Args:
            warmup_ms: Tempo de warmup em milissegundos (default: 600ms - AUMENTADO 2026-01-25)
                       Valores recomendados:
                       - 200ms: mínimo para conexões estáveis
                       - 400ms: recomendado para produção
                       - 600ms: para conexões instáveis
            sample_rate: Taxa de amostragem em Hz
            bytes_per_sample: Bytes por sample (2 para PCM16)
        """
        self.warmup_ms = warmup_ms
        self._original_warmup_ms = warmup_ms  # Guardar valor original para reset
        self.sample_rate = sample_rate
        self.bytes_per_sample = bytes_per_sample
        
        # Calcular tamanho do buffer de warmup
        samples_per_ms = sample_rate / 1000
        self.warmup_bytes = int(warmup_ms * samples_per_ms * bytes_per_sample)
        self._original_warmup_bytes = self.warmup_bytes  # Guardar valor original
        
        self._buffer = bytearray()
        self._warmup_complete = False
        self._total_buffered = 0
        
        # Log INFO para facilitar debug
        logger.info(f"🔊 [AUDIO_BUFFER] Criado: warmup={warmup_ms}ms, buffer_size={self.warmup_bytes}B, rate={sample_rate}Hz")
    
    def add(self, audio_bytes: bytes) -> bytes:
        """
        Adiciona áudio ao buffer.
        
        Durante warmup: acumula e retorna vazio
        Após warmup: retorna áudio imediatamente
        
        Returns:
            bytes: Áudio para enviar (vazio durante warmup)
        """
        if not audio_bytes:
            return b""
        
        self._total_buffered += len(audio_bytes)
        
        if not self._warmup_complete:
            self._buffer.extend(audio_bytes)
            
            if len(self._buffer) >= self.warmup_bytes:
                self._warmup_complete = True
                result = bytes(self._buffer)
                self._buffer.clear()
                # Calcular duração do buffer em ms
                audio_duration_ms = len(result) / (self.sample_rate * self.bytes_per_sample / 1000)
                logger.info(
                    f"🔊 [AUDIO_BUFFER] Warmup completo: flushing {len(result)}B "
                    f"({audio_duration_ms:.0f}ms de áudio bufferizado)"
                )
                return result
            
            return b""  # Ainda em warmup
        
        # Warmup já completou, passar direto
        return audio_bytes
    
    def flush(self) -> bytes:
        """
        Força envio de todo o buffer restante.
        Usar ao final da sessão.
        """
        if self._buffer:
            result = bytes(self._buffer)
            self._buffer.clear()
            return result
        return b""
    
    def reset(self, extended_warmup_ms: Optional[int] = None) -> None:
        """
        Reseta o buffer para nova sessão.
        
        Args:
            extended_warmup_ms: Se fornecido, usa este valor como warmup
                               (útil após resume de transferência onde há mais jitter)
                               Se None, volta ao valor original (100ms)
        """
        self._buffer.clear()
        self._warmup_complete = False
        self._total_buffered = 0
        
        if extended_warmup_ms is not None:
            # Warmup estendido temporário
            samples_per_ms = self.sample_rate / 1000
            self.warmup_bytes = int(extended_warmup_ms * samples_per_ms * self.bytes_per_sample)
            self.warmup_ms = extended_warmup_ms
            logger.debug(f"AudioBuffer: reset with extended warmup={extended_warmup_ms}ms, {self.warmup_bytes} bytes")
        else:
            # Restaurar valores originais
            self.warmup_bytes = self._original_warmup_bytes
            self.warmup_ms = self._original_warmup_ms
    
    @property
    def is_warming_up(self) -> bool:
        return not self._warmup_complete
    
    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)
    
    @property
    def buffered_ms(self) -> float:
        samples = len(self._buffer) / self.bytes_per_sample
        return (samples / self.sample_rate) * 1000


class ResamplerPair:
    """
    Par de resamplers para comunicação bidirecional.
    
    - Input: FreeSWITCH (16kHz) -> Provider (input_rate)
    - Output: Provider (output_rate) -> FreeSWITCH (16kHz)
    
    IMPORTANTE: Input e output do provider podem ter sample rates diferentes!
    - ElevenLabs: input=16kHz, output=16kHz/22050Hz/44100Hz (dinâmico)
    - OpenAI Realtime: input=24kHz, output=24kHz
    - Gemini Live: input=16kHz, output=24kHz
    
    Inclui buffer de warmup no output para playback suave.
    """
    
    def __init__(
        self, 
        freeswitch_rate: int = 16000, 
        provider_input_rate: int = 24000,
        provider_output_rate: int = None,  # Se None, usa provider_input_rate
        output_warmup_ms: int = 600  # 600ms para evitar stuttering (AUMENTADO 2026-01-25)
    ):
        # Se output rate não especificado, assume igual ao input
        if provider_output_rate is None:
            provider_output_rate = provider_input_rate
        
        self.freeswitch_rate = freeswitch_rate
        self.provider_input_rate = provider_input_rate
        self.provider_output_rate = provider_output_rate
        
        # Input: FS -> Provider (usa input_rate do provider)
        self.input_resampler = Resampler(freeswitch_rate, provider_input_rate)
        
        # Output: Provider -> FS (usa output_rate do provider)
        self.output_resampler = Resampler(provider_output_rate, freeswitch_rate)
        
        # Buffer de warmup para output (FS)
        self.output_buffer = AudioBuffer(
            warmup_ms=output_warmup_ms,
            sample_rate=freeswitch_rate
        )
        
        logger.debug(f"ResamplerPair: FS({freeswitch_rate}) <-> Provider(in:{provider_input_rate}, out:{provider_output_rate})")
    
    def resample_input(self, audio_bytes: bytes) -> bytes:
        """FS -> Provider"""
        return self.input_resampler.process(audio_bytes)
    
    def resample_output(self, audio_bytes: bytes) -> bytes:
        """Provider -> FS (com warmup buffer)"""
        # Log no primeiro chunk para debug
        if not hasattr(self, '_output_logged') or not self._output_logged:
            self._output_logged = True
            needs_resample = self.output_resampler.needs_resample
            logger.info(
                f"First output chunk: {len(audio_bytes)} bytes, "
                f"resample needed: {needs_resample} "
                f"({self.provider_output_rate}Hz -> {self.freeswitch_rate}Hz)"
            )
        
        resampled = self.output_resampler.process(audio_bytes)
        return self.output_buffer.add(resampled)
    
    def flush_output(self) -> bytes:
        """Força envio do buffer restante."""
        return self.output_buffer.flush()
    
    def reset_output_buffer(self, extended_warmup_ms: Optional[int] = None) -> None:
        """
        Reseta buffer para nova resposta.
        
        Args:
            extended_warmup_ms: Se fornecido, usa warmup estendido
                               (recomendado após resume de transferência)
        """
        self.output_buffer.reset(extended_warmup_ms)
    
    @property
    def is_output_warming_up(self) -> bool:
        return self.output_buffer.is_warming_up
