"""
Echo Canceller usando Speex DSP.

Remove o eco do áudio de entrada (microfone) usando o áudio de saída (speaker) como referência.

Uso:
    ec = EchoCancellerWrapper(sample_rate=16000, frame_size_ms=20, filter_length_ms=128)
    
    # Quando receber áudio do speaker (resposta do agente)
    ec.add_speaker_frame(speaker_audio)
    
    # Quando receber áudio do mic (caller)
    clean_audio = ec.process(mic_audio)  # Retorna áudio sem eco

IMPORTANTE:
- O frame_size deve ser consistente (20ms = 320 samples @ 16kHz)
- O filter_length define a "memória" do AEC (quanto eco pode remover)
- filter_length típico: 100-200ms para telefonia

Ref: https://github.com/xiongyihui/speexdsp-python
"""

import logging
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# Tentar importar speexdsp
try:
    from speexdsp import EchoCanceller
    SPEEXDSP_AVAILABLE = True
except ImportError:
    SPEEXDSP_AVAILABLE = False
    logger.warning("speexdsp não instalado - AEC desabilitado. Instale com: pip install speexdsp")


class EchoCancellerWrapper:
    """
    Wrapper para Speex Echo Canceller com buffer circular para speaker.
    
    O AEC precisa dos dois sinais sincronizados:
    - Mic input: áudio do caller (com eco)
    - Speaker output: áudio enviado ao caller (referência para subtrair)
    
    Como o speaker é enviado antes do mic capturar o eco, mantemos um buffer
    circular do speaker para sincronizar com o delay acústico.
    
    IMPORTANTE: O echo delay típico em telefonia é 100-300ms:
    - FreeSWITCH → RTP → Telefone: ~50-100ms
    - Telefone speaker → mic: ~10-20ms  
    - Telefone → RTP → FreeSWITCH: ~50-100ms
    
    Por isso, NÃO consumimos o speaker buffer imediatamente. Em vez disso,
    mantemos os frames por um tempo (echo_delay_frames) antes de usá-los.
    """
    
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_size_ms: int = 20,
        filter_length_ms: int = 128,
        echo_delay_ms: int = 200,  # Delay típico do echo
        enabled: bool = True
    ):
        """
        Inicializa o Echo Canceller.
        
        Args:
            sample_rate: Taxa de amostragem (Hz)
            frame_size_ms: Tamanho do frame em ms (deve coincidir com chunks de áudio)
            filter_length_ms: Tamanho do filtro adaptativo em ms (quanto eco pode remover)
            echo_delay_ms: Delay estimado do echo em ms (100-300ms típico)
            enabled: Se True, processa AEC. Se False, retorna áudio inalterado.
        """
        self.sample_rate = sample_rate
        self.frame_size_ms = frame_size_ms
        self.filter_length_ms = filter_length_ms
        self.echo_delay_ms = echo_delay_ms
        self.enabled = enabled and SPEEXDSP_AVAILABLE
        
        # Calcular tamanhos em samples
        self.frame_size = int(sample_rate * frame_size_ms / 1000)  # 160 @ 8kHz, 20ms
        self.filter_length = int(sample_rate * filter_length_ms / 1000)  # 1024 @ 8kHz, 128ms
        
        # Calcular delay em frames
        self.echo_delay_frames = int(echo_delay_ms / frame_size_ms)  # 10 frames @ 200ms
        
        # Buffer circular para speaker (referência)
        # Tamanho: delay + margem para variação de timing
        self.max_speaker_frames = self.echo_delay_frames + 20  # ~600ms de buffer
        self.speaker_buffer: deque = deque(maxlen=self.max_speaker_frames)
        
        # Buffer de delay - mantém frames até o echo aparecer
        self.delay_buffer: deque = deque(maxlen=self.max_speaker_frames)
        
        # Inicializar Speex AEC se disponível
        self._ec: Optional[EchoCanceller] = None
        if self.enabled:
            try:
                # EchoCanceller.create() não aceita keyword arguments
                self._ec = EchoCanceller.create(self.frame_size, self.filter_length)
                logger.info(
                    f"✅ Speex AEC inicializado: frame={self.frame_size} samples ({frame_size_ms}ms), "
                    f"filter={self.filter_length} samples ({filter_length_ms}ms)"
                )
            except Exception as e:
                logger.error(f"❌ Erro ao inicializar Speex AEC: {e}")
                self.enabled = False
        
        # Métricas
        self.frames_processed = 0
        self.frames_with_echo_removed = 0
    
    def add_speaker_frame(self, audio_bytes: bytes) -> None:
        """
        Adiciona áudio do speaker ao delay buffer.
        
        Os frames são mantidos no delay_buffer por echo_delay_frames antes
        de serem movidos para o speaker_buffer (referência para AEC).
        
        Chamar sempre que enviar áudio para o FreeSWITCH (resposta do agente).
        
        Args:
            audio_bytes: PCM16 audio bytes
        """
        if not self.enabled or not audio_bytes:
            return
        
        # Contador de chamadas ao add_speaker_frame (para logs)
        if not hasattr(self, '_speaker_calls'):
            self._speaker_calls = 0
        self._speaker_calls += 1
        
        # Dividir em frames do tamanho esperado
        frame_bytes = self.frame_size * 2  # 2 bytes por sample (PCM16)
        offset = 0
        frames_added = 0
        
        while offset + frame_bytes <= len(audio_bytes):
            frame = audio_bytes[offset:offset + frame_bytes]
            # Adicionar ao delay_buffer primeiro
            self.delay_buffer.append(frame)
            offset += frame_bytes
            frames_added += 1
        
        # Guardar resto parcial (se houver)
        if offset < len(audio_bytes):
            # Pad com silêncio para completar frame
            remaining = audio_bytes[offset:]
            padding = bytes(frame_bytes - len(remaining))
            self.delay_buffer.append(remaining + padding)
            frames_added += 1
        
        # Mover frames do delay_buffer para speaker_buffer após o delay
        # Isso sincroniza o speaker com o momento que o echo aparece no mic
        frames_moved = 0
        while len(self.delay_buffer) > self.echo_delay_frames:
            delayed_frame = self.delay_buffer.popleft()
            self.speaker_buffer.append(delayed_frame)
            frames_moved += 1
        
        # Log nas primeiras chamadas e periodicamente
        if self._speaker_calls <= 5 or self._speaker_calls % 50 == 0:
            logger.info(
                f"🔊 [AEC] speaker call #{self._speaker_calls}: "
                f"+{frames_added} frames, moved={frames_moved}, "
                f"delay_buf={len(self.delay_buffer)}, "
                f"speaker_buf={len(self.speaker_buffer)}, "
                f"input={len(audio_bytes)}B"
            )
    
    def process(self, mic_audio: bytes) -> bytes:
        """
        Processa áudio do microfone removendo eco.
        
        Args:
            mic_audio: PCM16 audio bytes do microfone (pode conter eco)
            
        Returns:
            PCM16 audio bytes sem eco (ou original se AEC desabilitado)
        """
        if not self.enabled or not self._ec or not mic_audio:
            return mic_audio
        
        frame_bytes = self.frame_size * 2
        result = bytearray()
        offset = 0
        frames_with_reference = 0
        frames_without_reference = 0
        
        while offset + frame_bytes <= len(mic_audio):
            mic_frame = mic_audio[offset:offset + frame_bytes]
            
            # Pegar frame do speaker para referência
            if self.speaker_buffer:
                speaker_frame = self.speaker_buffer.popleft()
                frames_with_reference += 1
            else:
                # Sem referência, usar silêncio (AEC não vai remover nada)
                speaker_frame = bytes(frame_bytes)
                frames_without_reference += 1
            
            try:
                # Processar AEC
                clean_frame = self._ec.process(mic_frame, speaker_frame)
                result.extend(clean_frame)
                self.frames_with_echo_removed += 1
            except Exception as e:
                # Em caso de erro, usar áudio original
                logger.warning(f"AEC process error: {e}")
                result.extend(mic_frame)
            
            self.frames_processed += 1
            offset += frame_bytes
        
        # Log periódico a cada 250 frames (~5 segundos)
        if self.frames_processed % 250 == 0:
            logger.info(
                f"🔇 [AEC] frames={self.frames_processed}, "
                f"with_ref={frames_with_reference}, no_ref={frames_without_reference}, "
                f"speaker_buffer={len(self.speaker_buffer)}, "
                f"sample_rate={self.sample_rate}Hz"
            )
        
        # Log inicial para debug
        if self.frames_processed <= 5:
            logger.info(
                f"🔇 [AEC] frame #{self.frames_processed}: "
                f"mic={len(mic_audio)}B, with_ref={frames_with_reference}, no_ref={frames_without_reference}"
            )
        
        # Processar resto parcial
        if offset < len(mic_audio):
            result.extend(mic_audio[offset:])
        
        return bytes(result)
    
    def reset(self) -> None:
        """Reseta o AEC e buffers."""
        self.speaker_buffer.clear()
        self.delay_buffer.clear()
        if self._ec:
            try:
                # Recriar o echo canceller (sem keyword arguments)
                self._ec = EchoCanceller.create(self.frame_size, self.filter_length)
            except Exception:
                pass
    
    def get_stats(self) -> dict:
        """Retorna estatísticas do AEC."""
        return {
            "enabled": self.enabled,
            "frames_processed": self.frames_processed,
            "frames_with_echo_removed": self.frames_with_echo_removed,
            "speaker_buffer_size": len(self.speaker_buffer),
            "frame_size_samples": self.frame_size,
            "filter_length_samples": self.filter_length,
        }
