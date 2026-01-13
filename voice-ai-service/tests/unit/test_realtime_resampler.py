"""
Tests for Realtime Audio Resampler.

Referências:
- openspec/changes/voice-ai-realtime/tasks.md (7.1.1)
- voice-ai-service/realtime/utils/resampler.py
"""

import pytest
import numpy as np
from unittest.mock import patch, MagicMock


class TestResampler:
    """Testes para o Resampler de áudio."""
    
    @pytest.fixture
    def resampler_16_to_24(self):
        """Resampler 16kHz → 24kHz."""
        from realtime.utils.resampler import Resampler
        return Resampler(input_rate=16000, output_rate=24000)
    
    @pytest.fixture
    def resampler_24_to_16(self):
        """Resampler 24kHz → 16kHz."""
        from realtime.utils.resampler import Resampler
        return Resampler(input_rate=24000, output_rate=16000)
    
    @pytest.fixture
    def sample_audio_16k(self):
        """Gera áudio de teste a 16kHz (1 segundo, 440Hz)."""
        duration = 1.0
        sample_rate = 16000
        frequency = 440.0
        
        t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
        samples = (np.sin(2 * np.pi * frequency * t) * 32767).astype(np.int16)
        return samples.tobytes()
    
    @pytest.fixture
    def sample_audio_24k(self):
        """Gera áudio de teste a 24kHz (1 segundo, 440Hz)."""
        duration = 1.0
        sample_rate = 24000
        frequency = 440.0
        
        t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
        samples = (np.sin(2 * np.pi * frequency * t) * 32767).astype(np.int16)
        return samples.tobytes()
    
    def test_resampler_initialization(self, resampler_16_to_24):
        """Testa inicialização do resampler."""
        assert resampler_16_to_24.input_rate == 16000
        assert resampler_16_to_24.output_rate == 24000
        assert resampler_16_to_24.up == 3  # 24000/8000 = 3
        assert resampler_16_to_24.down == 2  # 16000/8000 = 2
    
    def test_resampler_16k_to_24k(self, resampler_16_to_24, sample_audio_16k):
        """Testa resampling 16kHz → 24kHz."""
        output = resampler_16_to_24.process(sample_audio_16k)
        
        # Output deve ser maior (mais samples)
        assert len(output) > len(sample_audio_16k)
        
        # Proporção esperada: 24000/16000 = 1.5
        expected_ratio = 24000 / 16000
        actual_ratio = len(output) / len(sample_audio_16k)
        assert abs(actual_ratio - expected_ratio) < 0.1  # 10% de tolerância
    
    def test_resampler_24k_to_16k(self, resampler_24_to_16, sample_audio_24k):
        """Testa resampling 24kHz → 16kHz."""
        output = resampler_24_to_16.process(sample_audio_24k)
        
        # Output deve ser menor (menos samples)
        assert len(output) < len(sample_audio_24k)
        
        # Proporção esperada: 16000/24000 = 0.666
        expected_ratio = 16000 / 24000
        actual_ratio = len(output) / len(sample_audio_24k)
        assert abs(actual_ratio - expected_ratio) < 0.1
    
    def test_resampler_no_change(self):
        """Testa quando input e output rate são iguais."""
        from realtime.utils.resampler import Resampler
        
        resampler = Resampler(input_rate=16000, output_rate=16000)
        
        # Gerar áudio de teste
        samples = np.random.randint(-32768, 32767, 1600, dtype=np.int16)
        audio = samples.tobytes()
        
        output = resampler.process(audio)
        
        # Deve retornar praticamente o mesmo tamanho
        assert abs(len(output) - len(audio)) < 10
    
    def test_resampler_small_chunk(self, resampler_16_to_24):
        """Testa com chunks pequenos (20ms)."""
        # 20ms de áudio a 16kHz = 320 samples = 640 bytes
        samples = np.random.randint(-32768, 32767, 320, dtype=np.int16)
        chunk = samples.tobytes()
        
        output = resampler_16_to_24.process(chunk)
        
        # Deve processar sem erro
        assert len(output) > 0
    
    def test_resampler_empty_input(self, resampler_16_to_24):
        """Testa com input vazio."""
        output = resampler_16_to_24.process(b"")
        assert output == b"" or len(output) == 0
    
    def test_resampler_multiple_chunks(self, resampler_16_to_24):
        """Testa processamento de múltiplos chunks sequenciais."""
        total_output = b""
        
        for _ in range(10):
            samples = np.random.randint(-32768, 32767, 320, dtype=np.int16)
            chunk = samples.tobytes()
            output = resampler_16_to_24.process(chunk)
            total_output += output
        
        # Deve ter processado todos os chunks
        assert len(total_output) > 0
    
    def test_resampler_output_is_int16(self, resampler_16_to_24, sample_audio_16k):
        """Verifica que output está em formato int16."""
        output = resampler_16_to_24.process(sample_audio_16k)
        
        # Converter para numpy e verificar
        samples = np.frombuffer(output, dtype=np.int16)
        
        # Valores devem estar no range int16
        assert samples.min() >= -32768
        assert samples.max() <= 32767


class TestResamplerEdgeCases:
    """Testes de casos extremos."""
    
    def test_resampler_single_sample(self):
        """Testa com apenas 1 sample."""
        from realtime.utils.resampler import Resampler
        
        resampler = Resampler(16000, 24000)
        sample = np.array([1000], dtype=np.int16)
        
        output = resampler.process(sample.tobytes())
        assert len(output) >= 0  # Não deve falhar
    
    def test_resampler_max_values(self):
        """Testa com valores máximos (clipping)."""
        from realtime.utils.resampler import Resampler
        
        resampler = Resampler(16000, 24000)
        samples = np.array([32767, -32768] * 100, dtype=np.int16)
        
        output = resampler.process(samples.tobytes())
        output_samples = np.frombuffer(output, dtype=np.int16)
        
        # Não deve haver overflow
        assert output_samples.min() >= -32768
        assert output_samples.max() <= 32767
    
    def test_resampler_zero_input(self):
        """Testa com silêncio (zeros)."""
        from realtime.utils.resampler import Resampler
        
        resampler = Resampler(16000, 24000)
        samples = np.zeros(1600, dtype=np.int16)
        
        output = resampler.process(samples.tobytes())
        output_samples = np.frombuffer(output, dtype=np.int16)
        
        # Output deve ser silêncio também
        assert np.abs(output_samples).mean() < 1
