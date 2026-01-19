# Tasks: Migrar para G.711 Nativo

## 0. Pré-requisitos
- [ ] 0.1 Verificar versão do mod_audio_stream instalada
- [ ] 0.2 Testar se mod_audio_stream suporta formato `mulaw`
- [ ] 0.3 Confirmar que OpenAI aceita `audio/pcmu` no modelo GA

## 1. Configuração
- [ ] 1.1 Adicionar campo `audio_format` em `RealtimeSessionConfig` (valores: `g711`, `pcm16`)
- [ ] 1.2 Adicionar campo `audio_format` na tabela `voice_secretaries` do FusionPBX
- [ ] 1.3 Carregar configuração no `server.py`

## 2. OpenAI Provider
- [ ] 2.1 Modificar `_build_session_config()` para usar `audio/pcmu` quando `audio_format=g711`
- [ ] 2.2 Ajustar envio de áudio (já é base64, apenas formato muda)
- [ ] 2.3 Ajustar recebimento de áudio (decode G.711 se necessário)

## 3. Session
- [ ] 3.1 Modificar `handle_audio_input()` para passthrough quando G.711
- [ ] 3.2 Modificar `_handle_audio_output()` para passthrough quando G.711
- [ ] 3.3 Remover chamadas ao ResamplerPair quando G.711

## 4. Echo Canceller
- [ ] 4.1 Ajustar `EchoCancellerWrapper` para 8kHz (frame_size=160)
- [ ] 4.2 Converter G.711↔PCM16 apenas para o AEC (Speex requer PCM)
- [ ] 4.3 Testar AEC com áudio 8kHz

## 5. Barge-in Detection
- [ ] 5.1 Ajustar thresholds de RMS para G.711 (8-bit vs 16-bit)
- [ ] 5.2 Converter G.711→PCM16 apenas para cálculo de RMS
- [ ] 5.3 Testar barge-in com G.711

## 6. ESL Client
- [ ] 6.1 Modificar `audio_stream()` para aceitar formato `mulaw`
- [ ] 6.2 Passar formato correto baseado em `audio_format`

## 7. Server
- [ ] 7.1 Ajustar `freeswitch_sample_rate` para 8000 quando G.711
- [ ] 7.2 Ajustar cálculos de frame size (160 bytes para 20ms @ 8kHz G.711)

## 8. Testes
- [ ] 8.1 Testar chamada completa com G.711
- [ ] 8.2 Comparar latência G.711 vs PCM16
- [ ] 8.3 Comparar qualidade de STT
- [ ] 8.4 Testar barge-in
- [ ] 8.5 Testar AEC com viva-voz
- [ ] 8.6 Testar transferência

## 9. Rollout
- [ ] 9.1 Ativar para secretária de teste
- [ ] 9.2 Monitorar métricas por 24h
- [ ] 9.3 Expandir para produção

## 10. Documentação
- [ ] 10.1 Atualizar CLAUDE.md com nova configuração
- [ ] 10.2 Documentar flag `audio_format` no FusionPBX
