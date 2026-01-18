# Checklist de Produção - Voice AI Realtime

**Data:** 18/01/2026  
**Revisado com base na documentação oficial OpenAI**

---

## ✅ Configurações Implementadas

### VAD (Voice Activity Detection)
- [x] `semantic_vad` implementado (mais inteligente que server_vad)
- [x] Parâmetro `eagerness`: low, medium, high
- [x] Validação de valores de eagerness
- [x] Suporte a `disabled` (push-to-talk)
- [x] Fallback para `server_vad` com threshold/silence_duration

### Guardrails de Segurança
- [x] Instruções de segurança automáticas no system prompt
- [x] Proteção contra prompt injection
- [x] Lista de tópicos proibidos configurável (`guardrails_topics`)
- [x] Flag `guardrails_enabled` para ativar/desativar

### Formato de API
- [x] session.update com `session` wrapper (formato Beta correto)
- [x] Suporte a modalities: ["audio", "text"]
- [x] input/output_audio_format: "pcm16"
- [x] input_audio_transcription: whisper-1

### Limite de Sessão
- [x] Tracking de `_session_start_time`
- [x] `get_session_remaining_seconds()` para monitorar limite
- [x] `is_session_expiring_soon()` com warning 60s antes
- [x] Limite interno de 55 min (5 min de margem do limite de 60 min)

### Tratamento de Eventos
- [x] response.audio.delta / response.output_audio.delta (compatibilidade)
- [x] response.audio.done / response.output_audio.done
- [x] response.audio_transcript.delta/done
- [x] conversation.item.input_audio_transcription.completed
- [x] input_audio_buffer.speech_started/speech_stopped (VAD)
- [x] response.function_call_arguments.done (function calling)
- [x] rate_limits.updated (info, não erro)
- [x] Erros não-críticos: response_cancel_not_active

### TTS (Text-to-Speech para anúncios)
- [x] Sanitização de input (_sanitize_text)
- [x] Remoção de emojis, HTML, caracteres de controle
- [x] Limite de tamanho (1000 chars)
- [x] Timeout configurável (padrão: 30s)
- [x] Suporte a ElevenLabs e OpenAI TTS
- [x] Cache de áudio gerado (TTL: 1 hora)
- [x] Conversão MP3 → WAV via ffmpeg

---

## ⚠️ Avisos Importantes

### Depreciação da API Beta
> **ATENÇÃO:** A interface Beta (`OpenAI-Beta: realtime=v1`) será **DESCONTINUADA em 27/02/2026**.
> 
> Planejar migração para API GA quando disponível.
> Ref: https://platform.openai.com/docs/deprecations

### Custos
- Modelo `gpt-realtime` é ~20% mais barato que preview
- Sessões longas acumulam tokens de áudio rapidamente
- Monitorar uso via rate_limits.updated

### Disclosure
> É **OBRIGATÓRIO** informar ao usuário que a voz é gerada por IA.
> Já implementado via greeting message e personality prompt.

---

## 🔧 Configurações Recomendadas

### Para pt-BR (Brasil)
```python
SessionConfig(
    vad_type="semantic_vad",
    vad_eagerness="medium",  # Balanceado para português
    guardrails_enabled=True,
    language="pt-BR",
)
```

### Para atendimento rápido
```python
SessionConfig(
    vad_type="semantic_vad",
    vad_eagerness="high",  # Responde rápido
)
```

### Para atendimento paciente (idosos, etc)
```python
SessionConfig(
    vad_type="semantic_vad",
    vad_eagerness="low",  # Aguarda pausas longas
)
```

---

## 📊 Métricas para Monitorar

1. **Latência de resposta** - Tempo entre fim da fala do usuário e início do áudio do agente
2. **Taxa de barge-in** - Quantas vezes usuário interrompe o agente
3. **Taxa de erros** - Erros de conexão, rate limits, etc
4. **Duração média de sessão** - Para estimar custos
5. **Tokens de áudio** - Input e output para billing

---

## 🧪 Testes Recomendados Antes de Produção

1. [x] Teste de conexão WebSocket prolongada (>30 min)
2. [ ] Teste de barge-in (usuário interrompe agente)
3. [ ] Teste de ruído ambiente (microfone com barulho)
4. [ ] Teste de transferência completa (MOH → anúncio → bridge)
5. [ ] Teste de reconexão após perda de rede
6. [ ] Teste de limite de sessão (55+ min)
7. [ ] Teste de guardrails (tentativas de prompt injection)

---

## 📁 Arquivos Revisados

| Arquivo | Status |
|---------|--------|
| `providers/openai_realtime.py` | ✅ Revisado |
| `providers/base.py` | ✅ Revisado |
| `session.py` | ✅ Revisado |
| `handlers/realtime_announcement.py` | ✅ Revisado |
| `handlers/announcement_tts.py` | ✅ Revisado |
| `handlers/transfer_manager.py` | ✅ Revisado |

---

**Próximos Passos:**
1. Rodar migrations no FusionPBX
2. Rebuild do container Docker
3. Testes end-to-end
4. Monitorar logs em produção
