# Change: Migrar Voice AI para G.711 Híbrido

## Why

Atualmente o Voice AI usa PCM16 16kHz do FreeSWITCH e faz resample para 24kHz antes de enviar à OpenAI.
Isso adiciona ~10-20ms de latência e consome CPU desnecessariamente.

Todos os clientes em produção usam G.711 μ-law (codec padrão de telefonia PSTN).

**Descoberta importante**: O mod_audio_stream v1.0.3 tem suporte parcial:
- **Envio (FS → WS)**: Apenas L16 PCM (não suporta G.711)
- **Recebimento (WS → FS)**: Suporta PCMU/PCMA nativo (otimizado, sem transcoding)

Ref: https://github.com/amigniter/mod_audio_stream/issues/72

## What Changes

### Fase 1: G.711 Híbrido (mod_audio_stream v1.0.3)
- **OpenAI output**: Solicitar resposta em `audio/pcmu` (G.711)
- **Playback**: mod_audio_stream reproduz G.711 direto (sem transcoding)
- **Input**: Mantém L16 PCM (limitação do mod_audio_stream)

### Fase 2: G.711 Completo (opções)

**Opção A: Fork do mod_audio_stream (RECOMENDADA)**
- Código fonte disponível: https://github.com/amigniter/mod_audio_stream
- Licença MIT permite modificação e uso comercial
- Adicionar parâmetro `format=mulaw` no comando `uuid_audio_stream`
- Usar `switch_core_codec_encode()` do FreeSWITCH para codificar L16→PCMU
- Compilar nossa própria versão (sem limite de 10 canais)
- Estimativa: 6-10h de desenvolvimento
- Benefício: Solução definitiva, zero overhead no Python

**Opção B: Conversão no Voice AI Python**
- Converter L16 PCM → G.711 µ-law usando `audioop.lin2ulaw()`
- Enviar G.711 para OpenAI via `audio/pcmu`
- Benefício: Implementação rápida, sem recompilar FreeSWITCH
- Trade-off: CPU mínima no container Python (~0.1ms por frame)

**Opção C: Media bug customizado (complexo)**
- Capturar RTP G.711 antes da decodificação via `switch_media_bug`
- Enviar pacotes G.711 direto via WebSocket
- Trade-off: Módulo C customizado, manutenção complexa
- Não recomendado

**Opção D: mod_audio_fork (não recomendada)**
- Módulo antigo com problemas de compatibilidade
- Muitos reports de falhas com FreeSWITCH recente
- Não recomendado

## Impact

- Affected specs: `voice-ai-realtime`
- Affected code:
  - `voice-ai-service/realtime/providers/openai_realtime.py` (output format)
  - `voice-ai-service/realtime/session.py` (_handle_audio_output)

## Benefits (Fase 1 - G.711 Híbrido)

| Métrica | Antes | Depois | Melhoria |
|---------|-------|--------|----------|
| Latência playback | +5-10ms | 0ms | -5-10ms |
| CPU playback | Transcoding | Zero | 100% menos |
| Input | Sem mudança | Sem mudança | - |

## Risks

- **Compatibilidade**: Verificar se canal SIP usa PCMU (maioria usa)
- **Fallback**: Se canal não for PCMU, mod_audio_stream faz transcoding automático

## Rollback

- Mudar output format de volta para `audio/pcm`
- Flag `output_format: "g711" | "pcm16"` na configuração
