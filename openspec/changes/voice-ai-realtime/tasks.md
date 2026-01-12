# Tasks: Voice AI Realtime Implementation

## Status Legend
- [ ] Pendente
- [x] Concluído
- [~] Em progresso
- [-] Cancelado

---

## 0. Pré-requisitos e Infraestrutura

### 0.1 mod_audio_stream
- [ ] 0.1.1 Clonar repositório https://github.com/amigniter/mod_audio_stream
- [ ] 0.1.2 Instalar dependências: libfreeswitch-dev, libssl-dev, libevent-dev
- [ ] 0.1.3 Compilar módulo com TLS: `cmake -DUSE_TLS=ON`
- [ ] 0.1.4 Instalar no FreeSWITCH: `make install`
- [ ] 0.1.5 Carregar módulo: `load mod_audio_stream`
- [ ] 0.1.6 Testar conexão WebSocket básica
- [ ] 0.1.7 Documentar processo de compilação

### 0.2 Estrutura do Projeto
- [x] 0.2.1 Criar diretório `voice-ai-service/realtime/`
- [x] 0.2.2 Criar estrutura de subdiretórios (providers, handlers, utils)
- [x] 0.2.3 Configurar dependências em `requirements.txt`
- [x] 0.2.4 Criar `realtime_settings.py` com configurações

### 0.3 Docker Updates
- [x] 0.3.1 Criar `Dockerfile.realtime` para o bridge realtime
- [x] 0.3.2 Adicionar serviço `voice-ai-realtime` ao docker-compose
- [x] 0.3.3 Configurar porta 8080 para WebSocket realtime
- [x] 0.3.4 Manter serviço `voice-ai-service` (v1) na porta 8100
- [x] 0.3.5 Configurar network compartilhada entre serviços
- [x] 0.3.6 Adicionar healthcheck específico para realtime
- [x] 0.3.7 Criar script de inicialização unificado
- [x] 0.3.8 Documentar variáveis de ambiente

### 0.4 Coexistência v1/v2
- [x] 0.4.1 Adicionar campo `processing_mode` na tabela v_voice_secretaries
- [x] 0.4.2 Criar migration para novo campo (008_add_realtime_fields.sql)
- [x] 0.4.3 Criar script Lua `get_secretary_mode.lua`
- [x] 0.4.4 Atualizar dialplan para roteamento dinâmico (900_voice_ai.xml)
- [x] 0.4.5 Implementar fallback automático (realtime → turn_based)
- [ ] 0.4.6 Testar transição entre modos
- [ ] 0.4.7 Documentar comportamento de cada modo

---

## 1. Servidor WebSocket Principal

### 1.1 Core Server
- [x] 1.1.1 Criar `realtime/server.py` com servidor WebSocket (asyncio + websockets)
- [x] 1.1.2 Implementar rota `/stream/{domain_uuid}/{call_uuid}`
- [x] 1.1.3 Implementar handshake e validação de domain_uuid
- [x] 1.1.4 Criar handler para conexões FreeSWITCH
- [x] 1.1.5 Implementar graceful shutdown

### 1.2 Session Manager
- [x] 1.2.1 Criar `realtime/session_manager.py`
- [x] 1.2.2 Implementar `RealtimeSession` class
- [x] 1.2.3 Gerenciar lifecycle de sessões (start, stop, timeout)
- [x] 1.2.4 Implementar cleanup de sessões órfãs
- [x] 1.2.5 Adicionar métricas de sessões ativas

### 1.3 Audio Processor
- [x] 1.3.1 Criar `realtime/audio_processor.py`
- [x] 1.3.2 Implementar `Resampler` class (16k↔24k)
- [x] 1.3.3 Implementar buffer circular para chunks
- [x] 1.3.4 Adicionar conversão base64↔bytes
- [ ] 1.3.5 Testes unitários de resampling

---

## 2. Providers Realtime

### 2.1 Base Provider
- [x] 2.1.1 Criar `realtime/providers/base.py`
- [x] 2.1.2 Definir `BaseRealtimeProvider` interface
- [x] 2.1.3 Definir `ProviderEvent` dataclass
- [x] 2.1.4 Criar `RealtimeProviderFactory`

### 2.2 OpenAI Realtime API
- [x] 2.2.1 Criar `realtime/providers/openai_realtime.py`
- [x] 2.2.2 Implementar conexão WebSocket para `wss://api.openai.com/v1/realtime`
- [x] 2.2.3 Implementar `session.update` com configuração
- [x] 2.2.4 Implementar `input_audio_buffer.append` para envio de áudio
- [x] 2.2.5 Implementar handler para `response.audio.delta`
- [x] 2.2.6 Implementar handler para `response.audio_transcript.delta`
- [x] 2.2.7 Implementar handler para VAD events (`speech_started`, `speech_stopped`)
- [x] 2.2.8 Implementar `response.cancel` para barge-in
- [x] 2.2.9 Implementar function calling com `tools`
- [ ] 2.2.10 Testes de integração com OpenAI

### 2.3 ElevenLabs Conversational AI
- [x] 2.3.1 Criar `realtime/providers/elevenlabs_conv.py`
- [x] 2.3.2 Implementar conexão WebSocket para `wss://api.elevenlabs.io/v1/convai`
- [x] 2.3.3 Implementar `conversation_config_override`
- [x] 2.3.4 Implementar envio de áudio (`user_audio_chunk`)
- [x] 2.3.5 Implementar handler para `audio` events
- [x] 2.3.6 Implementar handler para `agent_response` (transcript)
- [x] 2.3.7 Implementar interrupção
- [ ] 2.3.8 Testes de integração com ElevenLabs

### 2.4 Google Gemini 2.0 Flash
- [x] 2.4.1 Criar `realtime/providers/gemini_live.py`
- [x] 2.4.2 Instalar SDK: `google-genai`
- [x] 2.4.3 Implementar conexão via `client.aio.live.connect`
- [x] 2.4.4 Configurar `response_modalities: ["AUDIO"]`
- [x] 2.4.5 Implementar envio de áudio streaming
- [x] 2.4.6 Implementar recebimento de áudio streaming
- [ ] 2.4.7 Testes de integração com Gemini

### 2.5 Custom Pipeline (Low-cost)
- [x] 2.5.1 Criar `realtime/providers/custom_pipeline.py`
- [x] 2.5.2 Integrar Deepgram Nova STT (streaming)
- [x] 2.5.3 Integrar Groq para LLM (baixa latência)
- [x] 2.5.4 Integrar Piper TTS local (streaming)
- [x] 2.5.5 Implementar orquestração entre componentes
- [x] 2.5.6 Implementar Silero VAD local
- [ ] 2.5.7 Testes de latência end-to-end

---

## 3. Handlers e Funcionalidades

### 3.1 FreeSWITCH Handler
- [x] 3.1.1 Criar `realtime/handlers/freeswitch.py`
- [x] 3.1.2 Implementar parsing de metadata inicial
- [x] 3.1.3 Implementar handler para DTMF
- [x] 3.1.4 Implementar handler para hangup
- [x] 3.1.5 Implementar playback de áudio para FS

### 3.2 Function Call Handler
- [x] 3.2.1 Criar `realtime/handlers/function_call.py`
- [x] 3.2.2 Implementar `transfer_call` via ESL
- [x] 3.2.3 Implementar `create_ticket` webhook OmniPlay
- [x] 3.2.4 Implementar `lookup_customer` integração CRM
- [x] 3.2.5 Implementar `check_appointment` agenda
- [ ] 3.2.6 Criar registro de function calls para auditoria

### 3.3 Transfer Handler
- [ ] 3.3.1 Criar `realtime/handlers/transfer.py`
- [ ] 3.3.2 Implementar resolução de destino (ramal, departamento)
- [ ] 3.3.3 Implementar transferência via ESL
- [ ] 3.3.4 Implementar transferência com anúncio
- [ ] 3.3.5 Log de transferências

---

## 4. Database e Configuração

### 4.1 Migrations
- [ ] 4.1.1 Criar migration `007_create_secretaries_realtime.sql`
- [ ] 4.1.2 Criar migration `008_create_conversations_realtime.sql`
- [ ] 4.1.3 Criar migration `009_add_realtime_provider_config.sql`
- [ ] 4.1.4 Executar migrations no FusionPBX PostgreSQL

### 4.2 Models
- [ ] 4.2.1 Criar model `SecretaryRealtime` (SQLAlchemy/asyncpg)
- [ ] 4.2.2 Criar model `ConversationRealtime`
- [ ] 4.2.3 Criar model `RealtimeProviderConfig`
- [ ] 4.2.4 Implementar queries de lookup por domain + extension

### 4.3 Configuration Loader
- [ ] 4.3.1 Criar `realtime/config_loader.py`
- [ ] 4.3.2 Implementar cache de configurações
- [ ] 4.3.3 Implementar reload de config sem restart
- [ ] 4.3.4 Implementar validação de config

---

## 5. FusionPBX Integration

### 5.1 Dialplan
- [ ] 5.1.1 Criar XML dialplan `900_voice_ai_realtime.xml`
- [ ] 5.1.2 Configurar extensões 8XXX para secretária realtime
- [ ] 5.1.3 Testar roteamento de chamadas
- [ ] 5.1.4 Documentar configuração

### 5.2 PHP Pages - Secretárias (Unificado v1/v2)
- [x] 5.2.1 Atualizar `secretary_edit.php` - Adicionar seletor de modo
- [x] 5.2.2 Implementar radio buttons: Turn-based / Realtime / Auto
- [x] 5.2.3 Implementar campos condicionais por modo
- [ ] 5.2.4 Mostrar estimativa de custo por modo
- [x] 5.2.5 Mostrar recursos disponíveis por modo (barge-in, etc)
- [x] 5.2.6 Implementar seletor de provider realtime (OpenAI, ElevenLabs, Gemini, Custom)
- [ ] 5.2.7 Implementar seletor de voz por provider
- [ ] 5.2.8 Implementar configuração de VAD (threshold, silence)
- [ ] 5.2.9 Implementar preview de voz (testar TTS)
- [ ] 5.2.10 Implementar botão "Testar Chamada" para validação

### 5.3 PHP Pages - Conversas Realtime
- [ ] 5.3.1 Criar `conversations_realtime.php` - Listagem
- [ ] 5.3.2 Criar `conversation_realtime_detail.php` - Detalhes
- [ ] 5.3.3 Implementar visualização de transcript em tempo real
- [ ] 5.3.4 Implementar métricas de latência
- [ ] 5.3.5 Implementar player de áudio (se gravado)

### 5.4 PHP Pages - Providers Realtime
- [ ] 5.4.1 Criar `providers_realtime.php` - Listagem
- [x] 5.4.2 Criar `providers_realtime_edit.php` - Configurar
- [x] 5.4.3 Implementar formulário por tipo de provider
- [ ] 5.4.4 Implementar teste de conexão
- [ ] 5.4.5 Implementar configuração de budgets

### 5.5 PHP Classes
- [ ] 5.5.1 Criar `resources/classes/voice_secretary_realtime.php`
- [ ] 5.5.2 Implementar CRUD para secretárias realtime
- [ ] 5.5.3 Implementar integração com bridge via API
- [ ] 5.5.4 Implementar validação de domain_uuid

---

## 6. Métricas e Observabilidade

### 6.1 Prometheus Metrics
- [x] 6.1.1 Criar `realtime/utils/metrics.py`
- [x] 6.1.2 Implementar `voice_ai_realtime_calls_total`
- [x] 6.1.3 Implementar `voice_ai_realtime_response_latency_seconds`
- [x] 6.1.4 Implementar `voice_ai_realtime_active_sessions`
- [x] 6.1.5 Implementar `voice_ai_realtime_audio_chunks_total`
- [ ] 6.1.6 Expor endpoint `/metrics`

### 6.2 Logging
- [ ] 6.2.1 Configurar structlog para logging estruturado
- [ ] 6.2.2 Implementar log de início/fim de sessão
- [ ] 6.2.3 Implementar log de latência por turno
- [ ] 6.2.4 Implementar log de erros com contexto
- [ ] 6.2.5 Configurar log rotation

### 6.3 Alertas
- [ ] 6.3.1 Criar regras Prometheus para latência alta
- [ ] 6.3.2 Criar alerta para taxa de erro alta
- [ ] 6.3.3 Criar alerta para sessões órfãs
- [ ] 6.3.4 Integrar com sistema de notificação

---

## 7. Testes

### 7.1 Testes Unitários
- [ ] 7.1.1 Testes para Resampler
- [ ] 7.1.2 Testes para SessionManager
- [ ] 7.1.3 Testes para ProviderFactory
- [ ] 7.1.4 Testes para FunctionCallHandler

### 7.2 Testes de Integração
- [ ] 7.2.1 Teste WebSocket FreeSWITCH → Bridge
- [ ] 7.2.2 Teste Bridge → OpenAI Realtime
- [ ] 7.2.3 Teste Bridge → ElevenLabs
- [ ] 7.2.4 Teste end-to-end completo

### 7.3 Testes de Performance
- [ ] 7.3.1 Benchmark de latência por provider
- [ ] 7.3.2 Teste de carga: 10 chamadas simultâneas
- [ ] 7.3.3 Teste de carga: 50 chamadas simultâneas
- [ ] 7.3.4 Teste de carga: 100 chamadas simultâneas
- [ ] 7.3.5 Análise de memory leaks em sessões longas

---

## 8. Documentação

### 8.1 Documentação Técnica
- [ ] 8.1.1 Documentar arquitetura do sistema realtime
- [ ] 8.1.2 Documentar protocolo WebSocket
- [ ] 8.1.3 Documentar API de cada provider
- [ ] 8.1.4 Documentar configuração do mod_audio_stream

### 8.2 Guias de Usuário
- [ ] 8.2.1 Guia de configuração de secretária realtime
- [ ] 8.2.2 Guia de configuração de providers
- [ ] 8.2.3 Guia de troubleshooting
- [ ] 8.2.4 FAQ

### 8.3 API Documentation
- [ ] 8.3.1 Documentar endpoints do bridge
- [ ] 8.3.2 Documentar eventos WebSocket
- [ ] 8.3.3 Documentar function calls disponíveis

---

## 9. Deploy e Operações

### 9.1 Deploy
- [ ] 9.1.1 Script de deploy do mod_audio_stream
- [ ] 9.1.2 Script de deploy do realtime bridge
- [ ] 9.1.3 Configuração de nginx/reverse proxy
- [ ] 9.1.4 Configuração de SSL/TLS para WSS

### 9.2 Operações
- [ ] 9.2.1 Runbook de troubleshooting
- [ ] 9.2.2 Procedimento de restart sem downtime
- [ ] 9.2.3 Procedimento de rollback
- [ ] 9.2.4 Monitoramento de custos por provider

---

## Dependências Entre Tasks

```
0.1.* (mod_audio_stream) ─────────────────────────────────────┐
                                                               │
0.2.* (Estrutura) ──────────────────────────┐                 │
                                             │                 │
                                             ▼                 ▼
1.1.* (Core Server) ◀───────────────────────────────────────────
        │
        ▼
1.2.* (Session Manager) ◀─────┐
        │                      │
        ▼                      │
1.3.* (Audio Processor) ◀─────┼─────────────────────┐
        │                      │                     │
        ▼                      │                     │
2.1.* (Base Provider) ◀───────┘                     │
        │                                            │
        ├──────────────────────────────────────────┐│
        │                                          ││
        ▼                                          ▼▼
2.2.* (OpenAI) ────────────────────────────────▶ 3.* (Handlers)
2.3.* (ElevenLabs) ─────────────────────────────▶     │
2.4.* (Gemini) ─────────────────────────────────▶     │
2.5.* (Custom) ─────────────────────────────────▶     │
                                                       │
4.* (Database) ◀───────────────────────────────────────┘
        │
        ▼
5.* (FusionPBX) ◀──────────────────────────────────────
        │
        ▼
6.* (Métricas) ◀───────────────────────────────────────
        │
        ▼
7.* (Testes) ──────────────────────────────────────────
        │
        ▼
8.* (Documentação) ────────────────────────────────────
        │
        ▼
9.* (Deploy) ──────────────────────────────────────────
```

---

## Estimativas de Tempo

| Fase | Tarefas | Estimativa |
|------|---------|------------|
| 0. Infraestrutura | 0.* | 2 dias |
| 1. WebSocket Server | 1.* | 3 dias |
| 2. Providers | 2.* | 5 dias |
| 3. Handlers | 3.* | 3 dias |
| 4. Database | 4.* | 1 dia |
| 5. FusionPBX | 5.* | 4 dias |
| 6. Métricas | 6.* | 2 dias |
| 7. Testes | 7.* | 3 dias |
| 8. Documentação | 8.* | 2 dias |
| 9. Deploy | 9.* | 2 dias |
| **Total** | | **~27 dias** |
