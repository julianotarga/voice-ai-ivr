# Change: Migrar Voice AI para G.711 Nativo

## Why

Atualmente o Voice AI usa PCM16 16kHz do FreeSWITCH e faz resample para 24kHz antes de enviar à OpenAI. 
Isso adiciona ~10-20ms de latência e consome CPU desnecessariamente.

Todos os clientes em produção usam G.711 μ-law (codec padrão de telefonia PSTN).
A OpenAI Realtime API suporta G.711 nativamente (`audio/pcmu`), eliminando a necessidade de resample.

Ref: https://github.com/aicc2025/sip-to-ai (projeto que já usa G.711 direto)

## What Changes

- **mod_audio_stream**: Configurar para enviar G.711 μ-law 8kHz em vez de PCM16 16kHz
- **OpenAI session.update**: Mudar formato de `audio/pcm` para `audio/pcmu`
- **Resampler**: Remover resample 16kHz↔24kHz (não mais necessário)
- **Echo Canceller**: Adaptar para 8kHz (frame_size = 160 samples)
- **Barge-in detection**: Ajustar thresholds para G.711 (8-bit vs 16-bit)

## Impact

- Affected specs: `voice-ai-realtime`
- Affected code:
  - `voice-ai-service/realtime/providers/openai_realtime.py` (session.update)
  - `voice-ai-service/realtime/session.py` (handle_audio_input, _handle_audio_output)
  - `voice-ai-service/realtime/utils/resampler.py` (pode ser removido)
  - `voice-ai-service/realtime/utils/echo_canceller.py` (adaptar para 8kHz)
  - `voice-ai-service/realtime/server.py` (configuração de sample rate)

## Benefits

| Métrica | Antes (PCM16) | Depois (G.711) | Melhoria |
|---------|---------------|----------------|----------|
| Latência | +10-20ms | 0ms | -10-20ms |
| Banda (uplink) | 512kbps | 64kbps | 8x menor |
| CPU (resample) | Contínuo | Zero | 100% menos |

## Risks

- **Qualidade de áudio**: G.711 8kHz tem menos fidelidade que PCM16 24kHz
- **STT accuracy**: Pode ser ligeiramente inferior (a testar)
- **Compatibilidade**: Requer mod_audio_stream configurado para G.711

## Rollback

- Manter código de resample como fallback configurável
- Flag `use_g711_native: bool` na configuração da secretária
