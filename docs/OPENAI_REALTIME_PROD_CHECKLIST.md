# Checklist Técnico - Pronto para Produção (OpenAI Realtime)

**Foco:** Voice AI IVR usando OpenAI Realtime (GA).  
**Data:** 18/01/2026

---

## 1) Configuração de Sessão
- [ ] `model` = `gpt-realtime` (ou `gpt-realtime-mini` quando custo for prioridade)
- [ ] Sem header `OpenAI-Beta` para modelos GA
- [ ] `session.update` válido para GA (modalities, voice, turn_detection, audio formats)
- [ ] `turn_detection` configurado conforme o cenário:
  - semantic_vad (low/medium/high) para voz natural
  - server_vad para ambientes ruidosos com thresholds ajustados
  - disabled para push-to-talk (commit manual)
- [ ] `input_audio_transcription` habilitada quando precisa de texto do usuário

---

## 2) Áudio e Sample Rate
- [ ] Resample 16kHz <-> 24kHz correto (FreeSWITCH vs OpenAI)
- [ ] Audio format `pcm16` (mono) consistente
- [ ] Buffer de warmup e jitter configurados para evitar audio picotado
- [ ] Limites de gain/normalização compatíveis com ambientes ruidosos

---

## 3) VAD, Barge-in e Estado
- [ ] `response.cancel` ao detectar interrupção
- [ ] `input_audio_buffer.clear` quando necessário (push-to-talk)
- [ ] Logs de `CallState` habilitados
- [ ] Métricas de transição de estado habilitadas
- [ ] Fallback de silêncio testado (reprompt/hangup)
- [ ] PTT RMS e hits ajustados em ambiente real (ruído)

---

## 4) Fluxo de Transferência
- [ ] Hand-off funciona em horários de expediente
- [ ] Presence check para ramais online
- [ ] Fallback de transferência (ticket/callback/voicemail)
- [ ] Transferência anunciada (TTS) validada
- [ ] Transferência realtime (premium) validada ou desabilitada
- [ ] Unbridge configurado (retomar bot ou encerrar)

---

## 5) Resiliência & Sessão
- [ ] Reconexão ou encerramento limpo em timeouts
- [ ] Sessão de 60 min monitorada (limite interno 55 min)
- [ ] Log dos erros críticos (rate_limit_exceeded, socket closed, etc)
- [ ] Tratamento de erros não críticos (response_cancel_not_active)

---

## 6) Multi-tenant e Segurança
- [ ] `domain_uuid` obrigatório em todos os fluxos
- [ ] Guardrails habilitados por default (quando aplicável)
- [ ] URLs e tokens por tenant validados
- [ ] Não vazar dados entre tenants

---

## 7) Custos
- [ ] Monitorar `rate_limits.updated` e uso de tokens
- [ ] Avaliar uso de cached input
- [ ] Evitar respostas longas em áudio sem necessidade

---

## 8) Testes Recomendados
- [ ] Barge-in real (usuario interrompe o agente)
- [ ] Ruído ambiente (microfone com barulho)
- [ ] Transferência completa (MOH → anúncio → bridge)
- [ ] Reconexão após perda de rede
- [ ] Sessão longa (55+ min)
- [ ] Guardrails (tentativas de prompt injection)

