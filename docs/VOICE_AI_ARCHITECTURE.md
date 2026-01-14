# Voice AI Architecture - OmniPlay WABA v2

## Visão Geral

Este documento descreve a arquitetura de integração Voice AI do OmniPlay WABA v2, baseada nos aprendizados do [SignalWire AI Stack](https://signalwire.com/blogs/ceo/building-a-voice-ai-stack-that-balances-power-with-flexibility) e do [os11k/freeswitch-elevenlabs-bridge](https://github.com/os11k/freeswitch-elevenlabs-bridge).

## Arquitetura Atual

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Caller        │     │   FreeSWITCH     │     │ voice-ai-       │
│   (Zoiper/      │────▶│   + mod_audio_   │────▶│ realtime        │
│    PSTN)        │     │     stream       │     │ (Python bridge) │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │   ElevenLabs    │
                                                 │   Conversational│
                                                 │   AI            │
                                                 └─────────────────┘
```

### Fluxo de Áudio

1. **Caller → Agent (user_audio_chunk)**:
   - FreeSWITCH captura áudio PCM16 16kHz
   - `mod_audio_stream` envia via WebSocket (chunks binários)
   - `voice-ai-realtime` encoda em base64 e envia para ElevenLabs

2. **Agent → Caller (audio response)**:
   - ElevenLabs envia `audio` events com `audio_base_64`
   - `voice-ai-realtime` decodifica para PCM16
   - Envia `{"type":"rawAudio","data":{"sampleRate":16000}}` uma vez
   - Envia chunks binários de 640 bytes (20ms) com pacing
   - `mod_audio_stream v1.0.3+` reproduz no canal

### Protocolo WebSocket (FreeSWITCH ↔ Bridge)

#### Direção: FreeSWITCH → Bridge
- **Binário**: Chunks de áudio PCM16 16kHz (tamanho configurável via `STREAM_BUFFER_SIZE`)

#### Direção: Bridge → FreeSWITCH
- **JSON (uma vez)**: `{"type":"rawAudio","data":{"sampleRate":16000}}`
- **Binário**: Chunks de 640 bytes (20ms) com pacing de 20ms

Ref: [mod_audio_stream README](https://github.com/amigniter/mod_audio_stream)

### Protocolo WebSocket (Bridge ↔ ElevenLabs)

#### Client → Server (user input)
```json
{"user_audio_chunk": "<base64 encoded PCM16 16kHz>"}
```

#### Server → Client (audio output)
```json
{
  "type": "audio",
  "audio_event": {
    "audio_base_64": "<base64 encoded PCM16 16kHz>",
    "event_id": 123
  }
}
```

Ref: [ElevenLabs Conversational AI AsyncAPI](https://elevenlabs.io/docs/agents-platform/api-reference/agents-platform/websocket)

## Aprendizados do SignalWire

### 1. SWAIG (SignalWire AI Gateway)

O SignalWire usa um padrão de funções (SWAIG) que permite ao agente executar ações durante a conversa:

```json
{
  "function": "check_availability",
  "purpose": "to check calendar availability",
  "parameters": {
    "type": "object",
    "properties": {
      "date": {"type": "string", "description": "Date to check"}
    }
  }
}
```

**Aplicação no OmniPlay**: Implementar `client_tool_call` no ElevenLabs para:
- Transferir chamadas (`transfer_call`)
- Agendar retornos (`schedule_callback`)
- Consultar status de pedidos (`check_order`)

### 2. Context Switching

O agente pode mudar de contexto dinamicamente durante a conversa:

> "The agent can be dynamically altered mid-conversation to change its focus or core prompting"

**Aplicação no OmniPlay**: Usar `contextual_update` do ElevenLabs para:
- Mudar foco do agente baseado em intent detectado
- Injetar informações do CRM durante a conversa

### 3. Sliding Window

Limitar o histórico de conversa para economizar tokens:

> "A sliding window can be defined to limit the conversation to a certain number of turns"

**Aplicação no OmniPlay**: Configurar no provider config:
```json
{
  "conversation_history_limit": 10
}
```

### 4. Fillers

Frases de preenchimento enquanto processa:

> "For extreme cases... a sound file can be played like pencil scribbling or keyboard typing"

**Aplicação no OmniPlay**: Implementar no ElevenLabs via `first_message` ou custom audio.

### 5. Video/Vision (Futuro)

O SignalWire suporta visão computacional:

> "it can use a series of mp4 files to simulate a state of idle, paying attention, and talking"

**Aplicação futura**: WebRTC + ElevenLabs Vision API.

## Alternativas de Integração

### 1. SIP Trunking Direto (ElevenLabs)

O ElevenLabs oferece [SIP Trunking](https://elevenlabs.io/conversational-ai/integrations/sip-trunking) que permite conectar diretamente sem bridge customizado:

**Vantagens**:
- Menor latência
- Sem bridge para manter
- Escalabilidade automática

**Desvantagens**:
- Menos controle sobre áudio
- Custo por minuto
- Menos flexibilidade para multi-tenant

### 2. SignalWire AI Agent

Alternativa completa ao ElevenLabs com [SignalWire AI Agent](https://developer.signalwire.com/sdks/realtime-sdk/guides/voice/first-steps-with-voice):

**Vantagens**:
- Construído sobre FreeSWITCH (compatibilidade nativa)
- SWML para IVRs complexos
- Suporte a ElevenLabs TTS built-in
- Escalabilidade horizontal

**Desvantagens**:
- Lock-in no SignalWire
- Custo por minuto

### 3. Asterisk + ElevenLabs

Para quem usa Asterisk, existe [módulo de integração](https://adelinabpo.com/asterisk-elevenlabs-conversational-ai-integration):

**Aplicação**: Não aplicável (usamos FreeSWITCH).

## Configuração Multi-Tenant

O OmniPlay é multi-tenant. Cada tenant (domain) pode ter:

1. **Secretárias diferentes** (`v_voice_secretaries`)
2. **Providers diferentes** (`v_voice_ai_providers`)
3. **Extensões diferentes** (8000, 8001, etc.)

### URL Pattern

```
ws://voice-ai-realtime:8085/stream/{domain_uuid}/{call_uuid}
```

O bridge identifica o tenant via `domain_uuid` e carrega a configuração correta.

## Evolução Planejada

### Fase 1: Estabilização (Atual)
- [x] mod_audio_stream v1.0.3+ instalado
- [x] Protocolo rawAudio + binário
- [x] Warmup 200ms + pacing 20ms
- [ ] Teste end-to-end funcional

### Fase 2: Robustez
- [ ] Retry automático de conexão WebSocket
- [ ] Métricas de latência (Prometheus)
- [ ] Logs estruturados (JSON)
- [ ] Health check do ElevenLabs

### Fase 3: Features Avançadas
- [ ] Tool calling (transferências, agendamentos)
- [ ] Context switching mid-call
- [ ] Barge-in otimizado
- [ ] Gravação de conversas

### Fase 4: Alternativas
- [ ] Suporte a OpenAI Realtime API
- [ ] Suporte a Google Gemini Live
- [ ] SIP Trunking direto (opcional)
- [ ] Fallback entre providers

## Referências

- [mod_audio_stream](https://github.com/amigniter/mod_audio_stream) - Módulo FreeSWITCH
- [os11k/freeswitch-elevenlabs-bridge](https://github.com/os11k/freeswitch-elevenlabs-bridge) - Bridge de referência
- [SignalWire AI Stack](https://signalwire.com/blogs/ceo/building-a-voice-ai-stack-that-balances-power-with-flexibility) - Arquitetura de referência
- [SignalWire Digital Employees](https://github.com/signalwire/digital_employees) - Exemplos SWML
- [ElevenLabs Conversational AI](https://elevenlabs.io/docs/agents-platform/) - Documentação oficial
- [Add AI Voice Agent to FreeSWITCH](https://www.cyberpunk.tools/jekyll/update/2025/11/18/add-ai-voice-agent-to-freeswitch.html) - Tutorial
