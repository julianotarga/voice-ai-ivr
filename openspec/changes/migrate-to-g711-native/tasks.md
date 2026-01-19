# Tasks: Migrar para G.711 Híbrido/Nativo

## Fase 0: Fork do mod_audio_stream (CONCLUÍDO)

### 0.1 Fork e Patch
- [x] 0.1.1 Clonar repositório https://github.com/amigniter/mod_audio_stream
- [x] 0.1.2 Adicionar constantes AUDIO_FORMAT_* em mod_audio_stream.h
- [x] 0.1.3 Adicionar campo audio_format e write_codec no struct private_data
- [x] 0.1.4 Modificar parsing do comando para aceitar parâmetro format (pcmu/pcma/l16)
- [x] 0.1.5 Inicializar codec G.711 usando switch_core_codec_init()
- [x] 0.1.6 Implementar encode_g711() helper function
- [x] 0.1.7 Modificar stream_frame() para codificar L16→G.711 antes de enviar
- [x] 0.1.8 Criar README.fork.md documentando as modificações

### 0.2 Build e Deploy
- [x] 0.2.0 Revisão L7: correções de segurança e performance
- [x] 0.2.1 Criar script de build `build-g711-fork.sh`
- [ ] 0.2.2 Compilar no servidor FreeSWITCH de produção
- [ ] 0.2.3 Testar comando `uuid_audio_stream <uuid> start <url> mono 8k pcmu`
- [ ] 0.2.4 Verificar se áudio G.711 está sendo enviado corretamente

---

## Fase 1: G.711 Híbrido (Output Only)

### 1.1 Configuração
- [ ] 1.1.1 Adicionar campo `audio_format` em `RealtimeSessionConfig`
- [ ] 1.1.2 Adicionar campo `g711_mode` ("hybrid" | "full")
- [ ] 1.1.3 Carregar configuração no `server.py`

### 1.2 OpenAI Provider - Output G.711
- [ ] 1.2.1 Modificar `_build_session_config()` para output `audio/pcmu`
- [ ] 1.2.2 Manter input como `audio/pcm` (por enquanto)
- [ ] 1.2.3 Testar recebimento de áudio G.711 da OpenAI

### 1.3 Session - Passthrough G.711
- [ ] 1.3.1 Modificar `_handle_audio_output()` para enviar G.711 direto ao WebSocket
- [ ] 1.3.2 Remover resample de output quando G.711
- [ ] 1.3.3 Testar playback no FreeSWITCH

### 1.4 Testes Fase 1
- [ ] 1.4.1 Testar chamada completa com output G.711
- [ ] 1.4.2 Comparar latência de playback
- [ ] 1.4.3 Verificar qualidade de áudio

---

## Fase 2: G.711 Completo (Input + Output)

### 2.1 Conversão L16→G.711 no Python
- [ ] 2.1.1 Criar `utils/audio_codec.py` com funções `pcm_to_ulaw()` e `ulaw_to_pcm()`
- [ ] 2.1.2 Usar `audioop` (stdlib) para conversão
- [ ] 2.1.3 Testar conversão isolada

### 2.2 Session - Input G.711
- [ ] 2.2.1 Modificar `handle_audio_input()` para converter L16→G.711
- [ ] 2.2.2 Manter L16 para processamento AEC
- [ ] 2.2.3 Enviar G.711 para OpenAI

### 2.3 OpenAI Provider - Input G.711
- [ ] 2.3.1 Modificar `_build_session_config()` para input `audio/pcmu`
- [ ] 2.3.2 Ajustar envio de áudio (base64 G.711)

### 2.4 Echo Canceller - Adaptar para 8kHz
- [ ] 2.4.1 Ajustar `EchoCancellerWrapper` para 8kHz
- [ ] 2.4.2 `frame_size` = 160 samples (20ms @ 8kHz)
- [ ] 2.4.3 `filter_length` = 1024 samples (128ms)
- [ ] 2.4.4 Converter G.711↔L16 apenas para AEC

### 2.5 Barge-in Detection
- [ ] 2.5.1 Ajustar threshold RMS para 8-bit (G.711)
- [ ] 2.5.2 Ou converter G.711→L16 para cálculo RMS
- [ ] 2.5.3 Testar barge-in com G.711

### 2.6 Remover Resampler
- [ ] 2.6.1 Remover `ResamplerPair` quando `g711_mode=full`
- [ ] 2.6.2 Limpar código de resample não usado

### 2.7 Testes Fase 2
- [ ] 2.7.1 Testar chamada completa com G.711 bidirecional
- [ ] 2.7.2 Comparar latência total vs PCM16
- [ ] 2.7.3 Comparar qualidade de STT
- [ ] 2.7.4 Testar barge-in
- [ ] 2.7.5 Testar AEC com viva-voz
- [ ] 2.7.6 Testar transferência

---

## Fase 3: Rollout

### 3.1 Configuração FusionPBX
- [ ] 3.1.1 Adicionar campo `audio_format` na tabela `voice_secretaries`
- [ ] 3.1.2 Adicionar UI no FusionPBX para configurar formato
- [ ] 3.1.3 Migrar secretárias existentes (default: pcm16)

### 3.2 Rollout Gradual
- [ ] 3.2.1 Ativar G.711 para uma secretária de teste
- [ ] 3.2.2 Monitorar métricas por 24h
- [ ] 3.2.3 Expandir para 10% das secretárias
- [ ] 3.2.4 Expandir para 100%

### 3.3 Documentação
- [ ] 3.3.1 Atualizar CLAUDE.md
- [ ] 3.3.2 Documentar flag `audio_format` no FusionPBX
- [ ] 3.3.3 Criar runbook de troubleshooting
