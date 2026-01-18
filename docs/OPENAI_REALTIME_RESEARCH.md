## OpenAI Realtime - Pesquisa Consolidada (Jan/2026)

Fontes solicitadas:
- https://platform.openai.com/docs/guides/realtime
- https://platform.openai.com/docs/api-reference/realtime
- https://platform.openai.com/docs/guides/realtime-websocket
- https://platform.openai.com/docs/guides/realtime-conversations
- https://openai.com/pt-BR/api/pricing/

Observacoes importantes:
- O Context7 MCP ficou indisponivel; usei web_search para obter resumos oficiais.
- Alguns detalhes podem variar por atualizacoes do site. Validar diretamente nos links acima
  quando necessario para decisao de producao.

---

### 1) Realtime API - Visao Geral (Guides / Realtime)

Pontos principais (resumo):
- API para conversas de baixa latencia com modelos multimodais (texto, audio e imagem).
- Suporta WebRTC, WebSocket e SIP como formas de conexao.
- Fluxo de sessao: session.created -> session.update -> envio de audio/texto -> response.*.
- Sessao tem duracao maxima de 60 minutos.

Eventos e mudancas citadas:
- response.text.delta -> response.output_text.delta
- response.audio.delta -> response.output_audio.delta
- response.audio_transcript.delta -> response.output_audio_transcript.delta (nomenclatura pode variar)
- Itens de conversa: conversation.item.added / conversation.item.done (no lugar de somente created)

Notas GA:
- Header OpenAI-Beta: realtime=v1 removido para modelos GA.
- Endpoint unificado para client_secrets (realtime/transcription).

---

### 2) Realtime Conversations (Guide)

Pontos principais (resumo):
- Sessao realtime eh stateful e pode receber audio e texto com respostas em audio/texto.
- session.update configura modalities, voice, turn_detection (VAD) e tools.
- response.create e necessario para gerar resposta quando o fluxo nao e automatico.
- VAD pode ser desabilitado (push-to-talk), exigindo commit manual do buffer.

---

### 3) Realtime WebSocket (Guide)

Pontos principais (resumo):
- WebSocket recomendado para servidor (backend) com controle fino de eventos.
- Envio de audio em chunks base64 via input_audio_buffer.append.
- Quando VAD desabilitado, usar input_audio_buffer.commit + response.create.
- Eventos de speech_started/speech_stopped informam o VAD.

---

### 4) API Reference - Realtime

Pontos principais (resumo):
- Eventos principais: session.created, session.updated, response.*.
- input_audio_buffer.append para streaming de audio.
- response.create para solicitar resposta (texto/audio).
- response.cancel para interromper (barge-in).
- function calling via response.function_call_arguments.* e conversation.item.create (output).

---

### 5) Precos (OpenAI Pricing - pt-BR)

Resumo (valores podem variar, conferir link):
- gpt-realtime (texto):
  - input: ~US$ 4.00 / 1M tokens
  - cached input: ~US$ 0.40 / 1M tokens
  - output: ~US$ 16.00 / 1M tokens
- gpt-realtime (audio):
  - input: ~US$ 32.00 / 1M tokens
  - output: ~US$ 64.00 / 1M tokens
- gpt-realtime-mini (texto):
  - input: ~US$ 0.60 / 1M tokens
  - cached input: ~US$ 0.06 / 1M tokens
  - output: ~US$ 2.40 / 1M tokens
- gpt-realtime-mini (audio):
  - input: ~US$ 10.00 / 1M tokens
  - output: ~US$ 20.00 / 1M tokens

Notas de custo citadas:
- Audio e tokenizado por duracao (ex: ~1 token por 100ms de audio input).
- Respostas longas e historico maior aumentam custo por turno.

---

### 6) Eventos e payloads (detalhado)

Eventos cliente -> servidor (WebSocket/WebRTC):
- session.update: altera configuracao (turn_detection, tools, voice, audio format, instructions).
- input_audio_buffer.append: envia audio base64 (chunk).
- input_audio_buffer.commit: confirma buffer como item de usuario (obrigatorio com VAD off).
- input_audio_buffer.clear: limpa buffer de entrada (push-to-talk / reinicio de captura).
- response.create: solicita resposta manualmente (sem VAD ou fluxo controlado).
- response.cancel: interrompe resposta em curso (barge-in).
- conversation.item.create: cria item explicitamente (texto, audio ou imagem).

Eventos servidor -> cliente:
- session.created / session.updated: ciclo de vida da sessao.
- input_audio_buffer.speech_started / speech_stopped: somente com VAD ativo.
- input_audio_buffer.committed: buffer aceito como item de usuario.
- conversation.item.added / conversation.item.done: item criado e finalizado.
- response.created: resposta em andamento.
- response.output_audio.delta / response.output_audio.done: streaming de audio.
- response.output_text.delta / response.output_text.done: streaming de texto.
- response.done: resposta completa (status completed/cancelled/failed).
- conversation.item.input_audio_transcription.completed: transcricao do audio de entrada.
- rate_limits.updated: limites atuais de tokens/requests (quando disponivel).

---

### 7) VAD, push-to-talk e configuracoes

Modos de VAD:
- server_vad: baseado em silencio (threshold, prefix_padding_ms, silence_duration_ms).
- semantic_vad: baseado em modelo semantico (eagerness low/medium/high/auto).
- disabled (turn_detection: null): cliente controla tudo.

Fluxo push-to-talk (VAD disabled):
1) session.update com turn_detection: null.
2) push down: iniciar captura; opcional limpar buffer (input_audio_buffer.clear).
3) push up: enviar audio (input_audio_buffer.append), depois commit (input_audio_buffer.commit).
4) solicitar resposta (response.create).
5) em interrupcoes: response.cancel + output_audio_buffer.clear (se aplicavel).

Observacao: sem VAD, nao ha speech_started/speech_stopped; silencio deve ser detectado no cliente.

---

### 8) Limites e custos (detalhado)

- Context window do gpt-realtime: 32k tokens; output max: 4096 tokens.
- Sessao realtime expira por duracao (ex.: ~60 minutos).
- Audio input: ~1 token por 100ms; audio output: ~1 token por 50ms.
- Custos aumentam com historico longo; usar cache de input quando possivel.
- Transcricao de audio (quando habilitada) tem custo separado.

---

### 9) Edge cases e boas praticas

- Limpar input_audio_buffer antes de novo push-to-talk para evitar vazamento de audio.
- Cancelar resposta ao detectar interrupcao do usuario (barge-in).
- Confirmar session.updated apos alterar turn_detection/voice.
- Monitorar response.done e rate_limits.updated para evitar exceder limites.
- Ajustar thresholds de VAD e parametros de silencio para ambientes ruidosos.

---

### 10) Itens de controle e riscos para producao

Checklist rapido (baseado nos guias):
- Confirmar nomes exatos de eventos no modelo GA usado.
- Confirmar formato do session.update (campos e nesting).
- Validar requisitos do push-to-talk (commit manual + response.create).
- Definir VAD (semantic_vad vs server_vad) e interrupt_response para barge-in.
- Monitorar limites de sessao (60 min) e reconectar se necessario.
- Controlar custos com cached input e modelos mini quando possivel.

---

### 11) Links diretos para consulta

- Realtime Guide: https://platform.openai.com/docs/guides/realtime
- Realtime API Ref: https://platform.openai.com/docs/api-reference/realtime
- Realtime WebSocket: https://platform.openai.com/docs/guides/realtime-websocket
- Realtime Conversations: https://platform.openai.com/docs/guides/realtime-conversations
- Pricing (pt-BR): https://openai.com/pt-BR/api/pricing/
