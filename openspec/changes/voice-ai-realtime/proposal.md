# Proposal: Voice AI Realtime - Secretária Virtual com Latência Ultra-Baixa

## Status
- **Proposed**: 2026-01-12
- **Author**: OmniPlay Engineering
- **Priority**: Critical
- **Extends**: add-voice-ai-ivr (v1 turn-based continua funcionando)
- **Backwards Compatible**: ✅ Sim

---

## Why

### Problema

O sistema atual (v1) usa uma abordagem **turn-based** (turno a turno):

```
Usuário fala → Grava arquivo → STT → LLM → TTS → Reproduz áudio
                    ↓
           Latência: 2-5 segundos por turno
```

**Problemas críticos**:

1. **Latência inaceitável** - 2-5 segundos não é natural para conversação
2. **Sem interrupção (barge-in)** - Usuário não pode interromper a IA
3. **Experiência robótica** - Pausas longas quebram o fluxo da conversa
4. **Sem full-duplex** - Não suporta fala simultânea

### Benchmark de Mercado (2025-2026)

| Solução | Latência | Barge-in | Full-duplex |
|---------|----------|----------|-------------|
| OpenAI Realtime API | ~300ms | ✅ | ✅ |
| ElevenLabs Conversational AI | ~400ms | ✅ | ✅ |
| Google Gemini 2.0 Flash | ~350ms | ✅ | ✅ |
| Deepgram + LLM + TTS | ~500ms | ✅ | Parcial |
| **Nossa v1 (turn-based)** | **2000-5000ms** | ❌ | ❌ |

---

## What Changes

### Nova Arquitetura: Full-Duplex Streaming

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ARQUITETURA REALTIME                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────────────┐    ┌─────────────────┐   │
│  │   Telefone   │───▶│    FreeSWITCH        │───▶│ mod_audio_stream│   │
│  │   (SIP/RTP)  │◀───│    (FusionPBX)       │◀───│   (WebSocket)   │   │
│  └──────────────┘    └──────────────────────┘    └────────┬────────┘   │
│                                                            │            │
│                                                            ▼            │
│                                    ┌───────────────────────────────┐    │
│                                    │    VOICE AI REALTIME BRIDGE   │    │
│                                    │         (Python/asyncio)      │    │
│                                    │                               │    │
│                                    │  ┌─────────────────────────┐  │    │
│                                    │  │   Session Manager       │  │    │
│                                    │  │   - domain_uuid         │  │    │
│                                    │  │   - call_uuid           │  │    │
│                                    │  │   - conversation_state  │  │    │
│                                    │  └─────────────────────────┘  │    │
│                                    │                               │    │
│                                    │  ┌─────────────────────────┐  │    │
│                                    │  │   Provider Router       │  │    │
│                                    │  │   - OpenAI Realtime     │  │    │
│                                    │  │   - ElevenLabs Conv.    │  │    │
│                                    │  │   - Gemini 2.0 Flash    │  │    │
│                                    │  │   - Custom (STT+LLM+TTS)│  │    │
│                                    │  └─────────────────────────┘  │    │
│                                    └───────────────┬───────────────┘    │
│                                                    │                    │
│                          ┌─────────────────────────┼────────────────┐   │
│                          ▼                         ▼                ▼   │
│              ┌─────────────────┐    ┌─────────────────┐  ┌──────────┐  │
│              │ OpenAI Realtime │    │ ElevenLabs API  │  │ Custom   │  │
│              │ wss://api...    │    │ Conversational  │  │ Pipeline │  │
│              │                 │    │                 │  │          │  │
│              │ GPT-4o-realtime │    │ + Voice Clone   │  │ Deepgram │  │
│              │ Voice: alloy    │    │ + Emotion       │  │ + Groq   │  │
│              │                 │    │                 │  │ + Piper  │  │
│              └─────────────────┘    └─────────────────┘  └──────────┘  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Componentes Principais

#### 1. mod_audio_stream (FreeSWITCH)
- **Módulo C** que captura áudio RTP em tempo real
- Streaming bidirecional via WebSocket
- Suporta PCM16 @ 8kHz/16kHz
- Full-duplex: envia e recebe simultaneamente
- **Já existe**: https://github.com/amigniter/mod_audio_stream

#### 2. Voice AI Realtime Bridge (Python)
- **Novo componente** central do sistema
- Servidor WebSocket que recebe áudio do FreeSWITCH
- Roteia para o provider de IA configurado
- Gerencia sessões e contexto por domain_uuid
- Multi-tenant por design

#### 3. Provider Integrations
| Provider | Tipo | Latência | Recursos |
|----------|------|----------|----------|
| **OpenAI Realtime API** | All-in-one | ~300ms | GPT-4o, VAD nativo, function calling |
| **ElevenLabs Conversational** | All-in-one | ~400ms | Vozes premium, voice cloning, emoção |
| **Gemini 2.0 Flash** | All-in-one | ~350ms | Multimodal, contexto longo |
| **Custom Pipeline** | Modular | ~500ms | Deepgram + Groq + Piper (custo baixo) |

#### 4. FusionPBX Integration
- Páginas PHP para configuração de secretárias realtime
- Seleção de provider e voz
- Configuração de prompts e knowledge base
- Monitoramento de uso e custos

---

## Fluxo de Áudio Realtime

### 1. Chamada Recebida
```
Telefone → FreeSWITCH → Dialplan → mod_audio_stream start
                                         ↓
                               ws://bridge:8085/{domain_uuid}/{call_uuid}
```

### 2. Streaming Bidirecional
```
┌─────────────────────────────────────────────────────────────┐
│                    FULL-DUPLEX STREAMING                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  FreeSWITCH                Bridge                 AI API    │
│      │                        │                      │       │
│      │──PCM16 audio (20ms)──▶│                      │       │
│      │──PCM16 audio (20ms)──▶│──base64 audio──────▶│       │
│      │──PCM16 audio (20ms)──▶│                      │       │
│      │                        │                      │       │
│      │                        │◀──audio.delta──────│       │
│      │◀──PCM16 playback──────│◀──audio.delta──────│       │
│      │◀──PCM16 playback──────│                      │       │
│      │                        │                      │       │
│      │──PCM16 audio (20ms)──▶│ (barge-in detected) │       │
│      │                        │──interrupt─────────▶│       │
│      │                        │                      │       │
└─────────────────────────────────────────────────────────────┘
```

### 3. Voice Activity Detection (VAD)
- **Server-side VAD**: OpenAI, ElevenLabs, Gemini têm VAD nativo
- **Local VAD**: Silero VAD para pipelines custom
- **Threshold configurável** por tenant
- **Barge-in**: Detecção de interrupção do usuário

---

## Formatos de Áudio

### FreeSWITCH → Bridge
- **Formato**: PCM16 Linear (L16)
- **Sample Rate**: 16000 Hz (recomendado) ou 8000 Hz
- **Channels**: Mono
- **Chunk size**: 20ms (320 bytes @ 16kHz)

### Bridge → AI Provider
| Provider | Input Format | Output Format |
|----------|--------------|---------------|
| OpenAI Realtime | pcm16 base64 @ 24kHz | pcm16 base64 @ 24kHz |
| ElevenLabs | pcm16 base64 @ 16kHz | pcm16 base64 @ 16kHz |
| Gemini 2.0 | pcm16 base64 @ 16kHz | pcm16 base64 @ 24kHz |
| Deepgram STT | pcm16 @ 16kHz | JSON transcript |

### Resampling
- Bridge faz resample automático entre taxas
- `scipy.signal.resample` para upsampling 16k→24k
- Buffer circular para acumulação eficiente

---

## Providers Suportados

### Tier 1: All-in-One (Recomendado)

#### OpenAI Realtime API
```python
# Conexão WebSocket
wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview

# Session config
{
    "type": "session.update",
    "session": {
        "modalities": ["text", "audio"],
        "voice": "alloy",  # alloy, echo, fable, onyx, nova, shimmer
        "input_audio_format": "pcm16",
        "output_audio_format": "pcm16",
        "turn_detection": {
            "type": "server_vad",
            "threshold": 0.5,
            "silence_duration_ms": 500
        },
        "tools": [...]  # Function calling
    }
}
```

#### ElevenLabs Conversational AI
```python
# Conexão WebSocket
wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}

# Audio config
{
    "type": "conversation_config_override",
    "conversation_config_override": {
        "agent": {
            "prompt": {...},
            "first_message": "Olá! Como posso ajudar?"
        },
        "tts": {
            "voice_id": "21m00Tcm4TlvDq8ikWAM"
        }
    }
}
```

#### Google Gemini 2.0 Flash
```python
# Conexão WebSocket (via SDK)
from google import genai

client = genai.Client(api_key=GOOGLE_API_KEY)
config = {
    "generation_config": {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {"prebuilt_voice_config": {"voice_name": "Aoede"}}
        }
    }
}

async with client.aio.live.connect(model="gemini-2.0-flash-exp", config=config) as session:
    # Streaming bidirecional
    await session.send(audio_chunk, end_of_turn=False)
    async for response in session.receive():
        yield response.data  # Audio bytes
```

### Tier 2: Custom Pipeline (Custo Baixo)

Para casos onde custo é prioridade sobre latência:

```
Deepgram Nova STT (streaming) → Groq Llama 3 (fast) → Piper TTS (local)
       ~100ms                       ~150ms                ~50ms
                          Total: ~300-500ms
```

---

## Database Schema

### Nova tabela: v_voice_secretaries_realtime

```sql
CREATE TABLE v_voice_secretaries_realtime (
    secretary_realtime_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL REFERENCES v_domains(domain_uuid),
    
    -- Identificação
    secretary_name VARCHAR(255) NOT NULL,
    extension VARCHAR(15),
    
    -- Provider principal
    realtime_provider VARCHAR(50) NOT NULL,  -- openai, elevenlabs, gemini, custom
    provider_config JSONB NOT NULL DEFAULT '{}',
    
    -- Configurações de voz
    voice_id VARCHAR(100),
    voice_settings JSONB DEFAULT '{}',
    
    -- Personalidade
    system_prompt TEXT,
    first_message TEXT,
    
    -- VAD Settings
    vad_threshold DECIMAL(3,2) DEFAULT 0.5,
    silence_duration_ms INTEGER DEFAULT 500,
    
    -- Knowledge Base (RAG)
    enable_rag BOOLEAN DEFAULT false,
    rag_collection_id UUID,
    
    -- Function Calling
    enable_functions BOOLEAN DEFAULT false,
    functions_config JSONB DEFAULT '[]',
    
    -- Transfer Rules
    transfer_rules JSONB DEFAULT '[]',
    
    -- Limits
    max_duration_seconds INTEGER DEFAULT 300,
    max_turns INTEGER DEFAULT 50,
    
    -- Status
    enabled BOOLEAN DEFAULT true,
    
    -- Audit
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Índices multi-tenant
CREATE INDEX idx_secretary_realtime_domain ON v_voice_secretaries_realtime(domain_uuid);
CREATE INDEX idx_secretary_realtime_extension ON v_voice_secretaries_realtime(domain_uuid, extension);
```

### Nova tabela: v_voice_conversations_realtime

```sql
CREATE TABLE v_voice_conversations_realtime (
    conversation_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_uuid UUID NOT NULL REFERENCES v_domains(domain_uuid),
    secretary_realtime_uuid UUID REFERENCES v_voice_secretaries_realtime,
    
    -- Call info
    call_uuid VARCHAR(255) NOT NULL,
    caller_id VARCHAR(100),
    destination VARCHAR(100),
    
    -- Timing
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    
    -- Metrics
    total_turns INTEGER DEFAULT 0,
    avg_latency_ms INTEGER,
    
    -- Transcript
    transcript JSONB DEFAULT '[]',
    
    -- Outcome
    outcome VARCHAR(50),  -- completed, transferred, abandoned, timeout
    transfer_destination VARCHAR(100),
    
    -- Cost tracking
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    audio_seconds DECIMAL(10,2) DEFAULT 0,
    estimated_cost DECIMAL(10,4) DEFAULT 0,
    
    -- Audit
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_conv_realtime_domain ON v_voice_conversations_realtime(domain_uuid);
CREATE INDEX idx_conv_realtime_date ON v_voice_conversations_realtime(domain_uuid, started_at);
```

---

## Multi-Tenant

### Isolamento por domain_uuid

1. **WebSocket Path**: `ws://bridge:8085/{domain_uuid}/{call_uuid}`
2. **Config lookup**: Busca secretary por domain + extension
3. **Rate limiting**: Por domain_uuid
4. **Cost tracking**: Por domain_uuid
5. **Logs separados**: Por domain_uuid

### Configuração por Tenant

```json
{
    "domain_uuid": "abc-123",
    "realtime_config": {
        "provider": "openai",
        "api_key_encrypted": "...",
        "default_voice": "alloy",
        "max_concurrent_calls": 10,
        "monthly_budget_usd": 100.00
    }
}
```

---

## Impact

### Latência Esperada

| Métrica | v1 (Turn-based) | v2 (Realtime) | Melhoria |
|---------|-----------------|---------------|----------|
| First response | 2-5s | 300-500ms | **10x** |
| Turn latency | 2-5s | 200-400ms | **10x** |
| Barge-in | N/A | <100ms | **∞** |
| Total call feel | Robótico | Natural | Qualitativo |

### Custos Estimados (por minuto de conversa)

| Provider | Custo/min | Observação |
|----------|-----------|------------|
| OpenAI Realtime | ~$0.12 | Input + output audio |
| ElevenLabs | ~$0.10 | Depende do plano |
| Gemini 2.0 Flash | ~$0.04 | Mais econômico |
| Custom Pipeline | ~$0.02 | Deepgram + Groq + Piper |

### Compatibilidade

- ✅ **FreeSWITCH 1.10+** com mod_audio_stream
- ✅ **FusionPBX 5.x** - Páginas de configuração
- ✅ **Multi-tenant** - Isolamento total por domain
- ✅ **Fallback** - Se realtime falhar, usa v1 turn-based
- ✅ **Docker** - Todos os serviços em containers

---

## Arquitetura Docker

### Visão Geral

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              SERVIDOR HOST                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────┐                                        │
│  │  FreeSWITCH + FusionPBX         │  ← Instalado no HOST (bare metal)      │
│  │  - mod_audio_stream             │                                        │
│  │  - Dialplan XML                 │                                        │
│  │  - PostgreSQL (FusionPBX)       │                                        │
│  └─────────────────┬───────────────┘                                        │
│                    │ ws://localhost:8085                                     │
│                    │ ws://localhost:8100                                     │
│                    ▼                                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                        DOCKER COMPOSE                                │    │
│  ├─────────────────────────────────────────────────────────────────────┤    │
│  │                                                                      │    │
│  │  ┌─────────────────────┐  ┌─────────────────────┐                   │    │
│  │  │ voice-ai-realtime   │  │ voice-ai-service    │                   │    │
│  │  │ (Bridge WebSocket)  │  │ (API Turn-based v1) │                   │    │
│  │  │ Port: 8085          │  │ Port: 8100          │                   │    │
│  │  │                     │  │                     │                   │    │
│  │  │ • OpenAI Realtime   │  │ • STT endpoints     │                   │    │
│  │  │ • ElevenLabs Conv   │  │ • TTS endpoints     │                   │    │
│  │  │ • Gemini Live       │  │ • Chat endpoints    │                   │    │
│  │  │ • Custom Pipeline   │  │ • RAG/Documents     │                   │    │
│  │  └─────────────────────┘  └─────────────────────┘                   │    │
│  │           │                        │                                 │    │
│  │           └────────────┬───────────┘                                 │    │
│  │                        ▼                                             │    │
│  │  ┌─────────────────────┐  ┌─────────────────────┐                   │    │
│  │  │ redis               │  │ chromadb (opcional) │                   │    │
│  │  │ Port: 6379          │  │ Port: 8000          │                   │    │
│  │  │ • Rate limiting     │  │ • Vector store      │                   │    │
│  │  │ • Session cache     │  │ • RAG embeddings    │                   │    │
│  │  └─────────────────────┘  └─────────────────────┘                   │    │
│  │                                                                      │    │
│  │  ┌─────────────────────┐  ┌─────────────────────┐                   │    │
│  │  │ ollama (opcional)   │  │ piper-tts (opcional)│                   │    │
│  │  │ Port: 11434         │  │ (interno)           │                   │    │
│  │  │ • LLM local         │  │ • TTS local         │                   │    │
│  │  └─────────────────────┘  └─────────────────────┘                   │    │
│  │                                                                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### docker-compose.yml (Atualizado)

```yaml
services:
  # =========================================================================
  # Voice AI Realtime Bridge (NOVO)
  # =========================================================================
  voice-ai-realtime:
    build:
      context: ./voice-ai-service
      dockerfile: Dockerfile.realtime
    container_name: voice-ai-realtime
    ports:
      - "8085:8085"
    environment:
      - PYTHONPATH=/app
      - REDIS_URL=redis://redis:6379
      - DATABASE_URL=postgresql://fusionpbx:password@host.docker.internal/fusionpbx
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY}
      - GOOGLE_API_KEY=${GOOGLE_API_KEY}
    volumes:
      - voice-ai-logs:/app/logs
    depends_on:
      redis:
        condition: service_healthy
    networks:
      - voice-ai-network
    restart: unless-stopped
    healthcheck:
      # WebSocket healthcheck deve ser handshake (ver health_check.py)
      test: ["CMD", "python", "-m", "realtime.health_check"]
      interval: 30s
      timeout: 10s
      retries: 3

  # =========================================================================
  # Voice AI Service (v1 Turn-based - Mantido para compatibilidade)
  # =========================================================================
  voice-ai-service:
    build:
      context: ./voice-ai-service
      dockerfile: Dockerfile
    container_name: voice-ai-service
    ports:
      - "8100:8100"
    # ... (configuração existente mantida)

  # Outros serviços (redis, chromadb, ollama) mantidos...
```

---

## Coexistência v1 (Turn-based) e v2 (Realtime)

### Arquitetura de Coexistência

```
┌───────────────────────────────────────────────────────────────────────┐
│                         FREESWITCH DIALPLAN                            │
├───────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │  Extensão 8XXX → Secretária Virtual                              │  │
│  │                                                                   │  │
│  │  1. Buscar config: SELECT * FROM v_voice_secretaries             │  │
│  │     WHERE extension = $1 AND domain_uuid = $2                    │  │
│  │                                                                   │  │
│  │  2. Verificar campo: processing_mode                             │  │
│  │                                                                   │  │
│  │     ┌────────────────────┐      ┌────────────────────┐           │  │
│  │     │ mode = 'realtime'  │      │ mode = 'turn_based'│           │  │
│  │     │                    │      │                    │           │  │
│  │     │ → mod_audio_stream │      │ → Lua script v1    │           │  │
│  │     │ → ws://bridge:8085 │      │ → HTTP API :8100   │           │  │
│  │     └────────────────────┘      └────────────────────┘           │  │
│  │                                                                   │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                        │
└───────────────────────────────────────────────────────────────────────┘
```

### Campo `processing_mode` na Tabela

```sql
-- Adicionar campo à tabela existente
ALTER TABLE v_voice_secretaries 
ADD COLUMN processing_mode VARCHAR(20) DEFAULT 'turn_based'
CHECK (processing_mode IN ('turn_based', 'realtime', 'auto'));

-- Valores possíveis:
-- 'turn_based' : Usa v1 (Lua + HTTP API) - DEFAULT
-- 'realtime'   : Usa v2 (mod_audio_stream + WebSocket)
-- 'auto'       : Tenta realtime, fallback para turn_based
```

### Dialplan Unificado

```xml
<extension name="voice_ai_secretary">
  <condition field="destination_number" expression="^(8\d{3})$">
    <!-- Obter configuração do banco -->
    <action application="set" data="secretary_extension=$1"/>
    <action application="lua" data="get_secretary_mode.lua"/>
    
    <!-- Roteamento baseado no modo -->
    <action application="execute_extension" 
            data="voice_ai_${processing_mode}" 
            inline="true"/>
  </condition>
</extension>

<!-- Modo Realtime (v2) -->
<extension name="voice_ai_realtime">
  <condition>
    <action application="set" data="STREAM_PLAYBACK=true"/>
    <action application="set" data="STREAM_SAMPLE_RATE=16000"/>
    <action application="set" data="api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/stream/${domain_uuid}/${uuid} mono 16k"/>
    <action application="answer"/>
    <action application="park"/>
  </condition>
</extension>

<!-- Modo Turn-based (v1) -->
<extension name="voice_ai_turn_based">
  <condition>
    <action application="answer"/>
    <action application="lua" data="secretary_ai.lua"/>
  </condition>
</extension>
```

---

## Escolha do Administrador (UI FusionPBX)

### Tela de Configuração da Secretária

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SECRETÁRIA VIRTUAL - CONFIGURAÇÃO                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Nome: [Recepção Principal          ]                                   │
│  Ramal: [8000]                                                          │
│                                                                          │
│  ─────────────────────────────────────────────────────────────────────  │
│  MODO DE PROCESSAMENTO                                                   │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                          │
│  ○ Turn-based (v1)                                                      │
│    └─ Latência: 2-5 segundos                                            │
│    └─ Custo: Mais baixo ($0.02-0.04/min)                                │
│    └─ Ideal para: IVRs simples, FAQ                                     │
│                                                                          │
│  ● Realtime (v2) ✨ RECOMENDADO                                         │
│    └─ Latência: 300-500ms                                               │
│    └─ Custo: Moderado ($0.04-0.12/min)                                  │
│    └─ Ideal para: Conversas naturais, atendimento premium               │
│    └─ Recursos: Barge-in, full-duplex                                   │
│                                                                          │
│  ○ Automático                                                           │
│    └─ Tenta realtime, fallback para turn-based se falhar                │
│                                                                          │
│  ─────────────────────────────────────────────────────────────────────  │
│  CONFIGURAÇÃO DO MODO REALTIME                                           │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                          │
│  Provider: [OpenAI Realtime API     ▼]                                  │
│  Voz:      [Alloy                   ▼]                                  │
│                                                                          │
│  VAD Threshold: [0.5] (0.0 = mais sensível, 1.0 = menos sensível)       │
│  Silêncio para fim de fala: [500] ms                                    │
│                                                                          │
│  ─────────────────────────────────────────────────────────────────────  │
│  CONFIGURAÇÃO DO MODO TURN-BASED                                         │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                          │
│  STT Provider: [OpenAI Whisper      ▼]                                  │
│  LLM Provider: [GPT-4o-mini         ▼]                                  │
│  TTS Provider: [OpenAI TTS          ▼]                                  │
│                                                                          │
│  [💾 Salvar]  [🔊 Testar Voz]  [📞 Testar Chamada]                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Benefícios da Escolha

| Cenário | Modo Recomendado | Justificativa |
|---------|------------------|---------------|
| IVR simples (menu numérico) | Turn-based | Custo baixo, latência aceitável |
| FAQ automatizado | Turn-based | Respostas pré-definidas |
| Atendimento premium | **Realtime** | Experiência natural |
| Suporte técnico | **Realtime** | Conversas longas, interrupções |
| Agendamento | **Realtime** | Fluxo conversacional |
| Alto volume, baixo budget | Turn-based | Custo prioritário |
| Poucos atendimentos, alta qualidade | **Realtime** | Experiência prioritária |

---

## Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|---------------|---------|-----------|
| Instabilidade mod_audio_stream | Média | Alto | Fork e manutenção própria |
| Custos elevados | Alta | Médio | Rate limiting, budgets por tenant |
| Latência de rede | Baixa | Médio | Servers próximos aos providers |
| Provider downtime | Baixa | Alto | Multi-provider fallback |
| Complexidade de debug | Média | Médio | Logging extensivo, replay de sessões |

---

## Próximos Passos

1. **Compilar mod_audio_stream** para nosso FreeSWITCH
2. **Desenvolver Bridge Python** com suporte multi-provider
3. **Integrar OpenAI Realtime API** como provider principal
4. **Adicionar ElevenLabs** como alternativa premium
5. **Criar páginas FusionPBX** para configuração
6. **Testes de latência** em ambiente de produção
7. **Documentação** e treinamento

---

## Referências

- [mod_audio_stream](https://github.com/amigniter/mod_audio_stream)
- [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime)
- [ElevenLabs Conversational AI](https://elevenlabs.io/docs/conversational-ai)
- [Google Gemini 2.0](https://ai.google.dev/gemini-api/docs/live)
- [FreeSWITCH Media Bugs](https://developer.signalwire.com/freeswitch/)
