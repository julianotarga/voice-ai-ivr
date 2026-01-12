# Capability: voice-ai-realtime

## Overview

O módulo `voice-ai-realtime` fornece conversação por voz em tempo real com latência ultra-baixa (<500ms), suportando barge-in, full-duplex e múltiplos providers de IA.

---

## Requirements

### REQ-REALTIME-001: Latência de Resposta

O sistema DEVE responder ao usuário em menos de 500ms após o fim da fala detectada.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Resposta rápida | Usuário conectado à secretária | Usuário termina de falar | IA responde em <500ms |
| Latência medida | Chamada em andamento | Turno de conversa completo | Métrica de latência registrada |
| Alerta de latência | Latência média > 1s por 5 min | Sistema monitora | Alerta disparado |

### REQ-REALTIME-002: Streaming Bidirecional

O sistema DEVE suportar streaming de áudio bidirecional simultâneo (full-duplex).

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Full-duplex | IA está respondendo | Usuário fala | Ambos áudios transmitidos |
| Sem bloqueio | Áudio de entrada | Áudio de saída simultâneo | Sem interferência |

### REQ-REALTIME-003: Barge-in (Interrupção)

O sistema DEVE permitir que o usuário interrompa a IA a qualquer momento.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Interrupção simples | IA falando resposta longa | Usuário começa a falar | IA para imediatamente |
| Detecção rápida | IA em playback | Fala detectada | Interrupção em <100ms |
| Contexto mantido | Interrupção ocorreu | Nova resposta gerada | Contexto preservado |

### REQ-REALTIME-004: Voice Activity Detection (VAD)

O sistema DEVE detectar automaticamente quando o usuário começa e para de falar.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Início de fala | Silêncio na linha | Usuário começa a falar | VAD detecta em <50ms |
| Fim de fala | Usuário falando | Silêncio > threshold | Fala considerada completa |
| Threshold configurável | Admin configura 0.7 | VAD processa áudio | Usa threshold 0.7 |
| Ruído ignorado | Ruído de fundo | VAD processa | Ruído não dispara fala |

### REQ-REALTIME-005: Multi-Provider

O sistema DEVE suportar múltiplos providers de IA realtime configuráveis por tenant.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| OpenAI Realtime | Provider configurado como OpenAI | Chamada recebida | Usa OpenAI Realtime API |
| ElevenLabs | Provider configurado como ElevenLabs | Chamada recebida | Usa ElevenLabs Conversational |
| Gemini Live | Provider configurado como Gemini | Chamada recebida | Usa Gemini 2.0 Flash |
| Custom Pipeline | Provider configurado como Custom | Chamada recebida | Usa STT+LLM+TTS separados |

### REQ-REALTIME-006: Multi-Tenant

O sistema DEVE isolar completamente configurações e dados entre tenants (domain_uuid).

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Isolamento de config | Tenant A usa OpenAI | Tenant B configura ElevenLabs | Configs independentes |
| Isolamento de dados | Conversa do Tenant A | Tenant B consulta | Sem acesso |
| Rate limiting por tenant | Tenant A atinge limite | Tenant B faz chamada | Tenant B não afetado |
| Billing separado | Chamadas de múltiplos tenants | Cálculo de custo | Custo por tenant |

### REQ-REALTIME-007: Resampling de Áudio

O sistema DEVE converter automaticamente entre diferentes sample rates.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| FS 16k → OpenAI 24k | FreeSWITCH @ 16kHz | Envio para OpenAI | Resample para 24kHz |
| OpenAI 24k → FS 16k | OpenAI responde @ 24kHz | Playback no FS | Resample para 16kHz |
| Qualidade mantida | Áudio reamostrado | Reprodução | Sem artefatos audíveis |

### REQ-REALTIME-008: Function Calling

O sistema DEVE suportar function calling para ações durante a conversa.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| transfer_call | Usuário pede transferência | IA identifica intent | Chamada transferida |
| create_ticket | Usuário quer registrar problema | IA cria ticket | Ticket no OmniPlay |
| lookup_customer | IA precisa de dados | Function call executado | Dados retornados para IA |

### REQ-REALTIME-009: Fallback Automático

O sistema DEVE fazer fallback automático quando um provider falhar.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Provider primário falha | OpenAI timeout | Durante chamada | Troca para ElevenLabs |
| Fallback chain | Todos providers falham | Nenhum disponível | Usa modo turn-based (v1) |
| Log de fallback | Fallback ocorreu | Sistema registra | Log com motivo |

### REQ-REALTIME-010: Métricas e Observabilidade

O sistema DEVE expor métricas para monitoramento.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Latência exportada | Chamadas em andamento | Prometheus scrape | Métrica disponível |
| Sessões ativas | 10 sessões ativas | Consulta gauge | Retorna 10 |
| Erros contados | Provider retorna erro | Counter incrementado | Erro visível em dashboard |

### REQ-REALTIME-011: Limite de Duração

O sistema DEVE encerrar chamadas que excedam o limite configurado.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Limite atingido | max_duration = 300s | Chamada dura 300s | Chamada encerrada graciosamente |
| Aviso antes | 30s antes do limite | Sistema detecta | Avisa usuário verbalmente |

### REQ-REALTIME-012: Gravação de Transcript

O sistema DEVE gravar o transcript completo da conversa.

**Scenarios:**

| Scenario | Given | When | Then |
|----------|-------|------|------|
| Transcript salvo | Chamada encerrada | Sistema processa | Transcript no banco |
| Formato estruturado | Transcript gerado | Consulta | JSON com role, text, timestamp |
| Busca por transcript | Admin busca "cancelar" | Query executada | Conversas filtradas |

---

## Audio Specifications

### Formato de Entrada (FreeSWITCH → Bridge)
- **Codec**: PCM16 Linear (L16)
- **Sample Rate**: 16000 Hz (recomendado) ou 8000 Hz
- **Channels**: 1 (Mono)
- **Bit Depth**: 16 bits
- **Byte Order**: Little Endian
- **Chunk Size**: 20ms (320 samples = 640 bytes @ 16kHz)

### Formato por Provider

| Provider | Input Rate | Output Rate | Formato |
|----------|------------|-------------|---------|
| OpenAI Realtime | 24000 Hz | 24000 Hz | PCM16 base64 |
| ElevenLabs | 16000 Hz | 16000 Hz | PCM16 base64 |
| Gemini 2.0 | 16000 Hz | 24000 Hz | PCM16 base64 |
| Custom (Deepgram) | 16000 Hz | - | PCM16 raw |
| Custom (Piper) | - | 22050 Hz | PCM16 wav |

---

## WebSocket Protocol

### FreeSWITCH → Bridge

**URL**: `ws://bridge:8080/stream/{domain_uuid}/{call_uuid}`

**Mensagens:**

```typescript
// Audio chunk (binary frame)
type AudioChunk = ArrayBuffer;  // PCM16 bytes

// Metadata (text frame)
interface MetadataMessage {
  type: "metadata";
  caller_id: string;
  destination: string;
  domain_uuid: string;
  call_uuid: string;
  timestamp: string;
}

// DTMF event (text frame)
interface DTMFMessage {
  type: "dtmf";
  digit: string;  // "0"-"9", "*", "#"
}

// Hangup event (text frame)
interface HangupMessage {
  type: "hangup";
  reason?: string;
}
```

### Bridge → FreeSWITCH

```typescript
// Audio playback (via mod_audio_stream format)
interface PlaybackMessage {
  type: "streamAudio";
  data: {
    audioDataType: "raw" | "wav";
    sampleRate: 8000 | 16000;
    audioData: string;  // base64 encoded
  };
}

// Or raw binary audio
type AudioPlayback = ArrayBuffer;  // PCM16 bytes
```

---

## API Endpoints

### Health Check
```
GET /health
Response: {
  "status": "healthy",
  "active_sessions": 5,
  "providers": {
    "openai": "connected",
    "elevenlabs": "connected",
    "gemini": "connected"
  }
}
```

### Metrics
```
GET /metrics
Response: Prometheus text format
```

### Sessions (Admin)
```
GET /admin/sessions
Authorization: Bearer {admin_token}
Response: {
  "sessions": [
    {
      "call_uuid": "...",
      "domain_uuid": "...",
      "started_at": "...",
      "provider": "openai",
      "turns": 5
    }
  ]
}
```

---

## Database Tables

### v_voice_secretaries_realtime

| Column | Type | Description |
|--------|------|-------------|
| secretary_realtime_uuid | UUID | Primary key |
| domain_uuid | UUID | FK to v_domains |
| secretary_name | VARCHAR(255) | Nome da secretária |
| extension | VARCHAR(15) | Ramal associado |
| realtime_provider | VARCHAR(50) | openai, elevenlabs, gemini, custom |
| provider_config | JSONB | Configuração específica |
| voice_id | VARCHAR(100) | ID da voz |
| system_prompt | TEXT | Prompt do sistema |
| first_message | TEXT | Saudação inicial |
| vad_threshold | DECIMAL(3,2) | Threshold do VAD (0.0-1.0) |
| silence_duration_ms | INTEGER | Silêncio para fim de fala |
| max_duration_seconds | INTEGER | Duração máxima |
| enabled | BOOLEAN | Ativo/Inativo |

### v_voice_conversations_realtime

| Column | Type | Description |
|--------|------|-------------|
| conversation_uuid | UUID | Primary key |
| domain_uuid | UUID | FK to v_domains |
| secretary_realtime_uuid | UUID | FK to secretary |
| call_uuid | VARCHAR(255) | UUID da chamada FS |
| caller_id | VARCHAR(100) | Número do chamador |
| started_at | TIMESTAMP | Início da chamada |
| ended_at | TIMESTAMP | Fim da chamada |
| duration_seconds | INTEGER | Duração total |
| total_turns | INTEGER | Número de turnos |
| avg_latency_ms | INTEGER | Latência média |
| transcript | JSONB | Transcrição completa |
| outcome | VARCHAR(50) | Resultado final |
| estimated_cost | DECIMAL(10,4) | Custo estimado |

---

## Error Codes

| Code | Name | Description |
|------|------|-------------|
| REALTIME_001 | PROVIDER_UNAVAILABLE | Provider não está respondendo |
| REALTIME_002 | AUDIO_FORMAT_ERROR | Formato de áudio inválido |
| REALTIME_003 | SESSION_TIMEOUT | Sessão expirou por inatividade |
| REALTIME_004 | MAX_DURATION_EXCEEDED | Limite de duração atingido |
| REALTIME_005 | RATE_LIMIT_EXCEEDED | Limite de chamadas do tenant excedido |
| REALTIME_006 | INVALID_DOMAIN | domain_uuid inválido ou não encontrado |
| REALTIME_007 | CONFIG_NOT_FOUND | Secretária não configurada para extensão |
| REALTIME_008 | FUNCTION_CALL_FAILED | Function call retornou erro |
| REALTIME_009 | TRANSFER_FAILED | Transferência de chamada falhou |
| REALTIME_010 | WEBSOCKET_ERROR | Erro na conexão WebSocket |

---

## Security Considerations

1. **Autenticação**: Conexões WebSocket validam domain_uuid
2. **Isolamento**: Dados de tenant completamente isolados
3. **Rate Limiting**: Por domain_uuid para evitar abuso
4. **API Keys**: Criptografadas em repouso no banco
5. **TLS**: WSS obrigatório em produção
6. **Logs**: Sem dados sensíveis em logs

---

## Performance Targets

| Metric | Target | Maximum |
|--------|--------|---------|
| Response latency (p50) | 300ms | 500ms |
| Response latency (p99) | 500ms | 1000ms |
| Barge-in detection | 50ms | 100ms |
| VAD detection | 30ms | 50ms |
| Concurrent sessions | 100 | 500 |
| Memory per session | 50MB | 100MB |
| CPU per session | 5% | 10% |
