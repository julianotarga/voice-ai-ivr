# Design: Migração para G.711 Nativo

## Context

O Voice AI atualmente usa a seguinte cadeia de áudio:

```
FreeSWITCH (PCM16 16kHz) → Resample (16→24kHz) → OpenAI (PCM16 24kHz)
OpenAI (PCM16 24kHz) → Resample (24→16kHz) → FreeSWITCH (PCM16 16kHz)
```

Todos os clientes em produção usam G.711 μ-law nas linhas PSTN.
A OpenAI Realtime API suporta G.711 nativamente via formato `audio/pcmu`.

## Goals

- Eliminar resample 16kHz↔24kHz
- Reduzir latência em ~10-20ms
- Reduzir consumo de banda em 8x
- Manter compatibilidade com clientes existentes

## Non-Goals

- Suportar outros codecs (A-law, Opus, etc.) - fora de escopo
- Mudar arquitetura do mod_audio_stream
- Alterar fluxo de transferência/handoff

## Decisions

### Decision 1: Usar audio/pcmu na OpenAI

A OpenAI Realtime API aceita os seguintes formatos de áudio:
- `audio/pcm` - PCM16 (16-bit signed, little-endian)
- `audio/pcmu` - G.711 μ-law (8-bit, 8kHz)

Decisão: Usar `audio/pcmu` para input e output.

```python
# session.update para OpenAI GA
{
    "type": "session.update",
    "session": {
        "audio": {
            "input": {
                "format": {"type": "audio/pcmu"}  # G.711 μ-law
            },
            "output": {
                "format": {"type": "audio/pcmu"}  # G.711 μ-law
            }
        }
    }
}
```

### Decision 2: Configurar mod_audio_stream para G.711

O mod_audio_stream suporta diferentes formatos:
- `mono 16k` - PCM16 16kHz (atual)
- `mono 8k` - PCM16 8kHz
- `mulaw` - G.711 μ-law 8kHz

Decisão: Usar `mulaw` no dialplan/ESL.

```bash
# Comando ESL
uuid_audio_stream <uuid> start <ws_url> mulaw
```

### Decision 3: Remover ResamplerPair

Com G.711 nativo, não há necessidade de resample:
- Input: G.711 8kHz → OpenAI (mesmo formato)
- Output: OpenAI G.711 8kHz → FreeSWITCH (mesmo formato)

Decisão: Remover `ResamplerPair` e substituir por passthrough.

### Decision 4: Adaptar Echo Canceller para 8kHz

O Speex AEC precisa de ajustes para 8kHz:
- `sample_rate`: 8000 (era 16000)
- `frame_size`: 160 samples (era 320) para 20ms
- `filter_length`: 1024 samples (era 2048) para 128ms

### Decision 5: Flag de configuração

Adicionar flag para permitir rollback:

```python
@dataclass
class RealtimeSessionConfig:
    # Audio format
    audio_format: str = "g711"  # "g711" ou "pcm16"
```

## Alternatives Considered

### Alternative 1: Manter PCM16 com resample otimizado
- Prós: Melhor qualidade de áudio
- Contras: Latência adicional, CPU desnecessária
- Rejeitado: Clientes já usam G.711 na PSTN

### Alternative 2: Usar Opus
- Prós: Melhor compressão, qualidade adaptativa
- Contras: OpenAI não suporta Opus nativo
- Rejeitado: Não suportado

## Risks / Trade-offs

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Qualidade de áudio inferior | Médio | Testar STT accuracy |
| STT menos preciso | Baixo | G.711 é padrão telefonia |
| mod_audio_stream não suporta mulaw | Alto | Verificar versão |

## Migration Plan

### Fase 1: Preparação
1. Verificar versão do mod_audio_stream
2. Testar G.711 em ambiente de dev
3. Implementar flag de configuração

### Fase 2: Implementação
1. Adicionar suporte a G.711 no código
2. Manter PCM16 como fallback
3. Testar em paralelo

### Fase 3: Rollout
1. Ativar G.711 para uma secretária de teste
2. Monitorar métricas (latência, STT accuracy)
3. Expandir para todas as secretárias

### Rollback
- Mudar `audio_format: "pcm16"` na configuração
- Reiniciar container

## Open Questions

1. Qual versão do mod_audio_stream está instalada? Suporta `mulaw`?
2. A OpenAI cobra diferente por G.711 vs PCM16?
3. Deepgram e Gemini também suportam G.711?
