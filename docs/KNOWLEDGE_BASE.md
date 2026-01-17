# 📚 Knowledge Base - Voice AI IVR

Este documento contém as referências de documentação e bibliotecas que devem ser consultadas via **Context7 MCP** ao trabalhar no projeto Voice AI IVR.

## 🔍 Como Usar com Context7

```python
# Passo 1: Resolver Library ID
mcp_context7_resolve-library-id(
    libraryName="elevenlabs",
    query="Conversational AI WebSocket API"
)

# Passo 2: Consultar documentação
mcp_context7_query-docs(
    libraryId="/websites/elevenlabs_io",
    query="WebSocket audio streaming events format"
)
```

---

## 🎙️ Provedores de IA de Voz

### ElevenLabs Conversational AI

| Recurso | Context7 Library ID | Snippets |
|---------|---------------------|----------|
| **Website Docs** | `/websites/elevenlabs_io` | 6.866 |
| **Python SDK** | `/elevenlabs/elevenlabs-python` | 629 |
| **JS SDK** | `/elevenlabs/elevenlabs-js` | 540 |
| **React SDK** | `/websites/npmjs_package__elevenlabs_react` | 3.569 |

**Documentação Oficial:**
- WebSocket API: https://elevenlabs.io/docs/agents-platform/api-reference/agents-platform/websocket
- Events: https://elevenlabs.io/docs/agents-platform/customization/events/client-events
- SDK Python: https://github.com/elevenlabs/elevenlabs-python

**Queries Úteis:**
```
- "WebSocket conversation API events audio format"
- "conversation_initiation_client_data message format"
- "client_tool_call function calling parameters"
- "ping pong keep-alive connection"
- "user_activity barge-in interrupt"
```

---

### OpenAI Realtime API

| Recurso | Context7 Library ID | Snippets |
|---------|---------------------|----------|
| **Platform Docs** | `/websites/platform_openai` | 9.418 |
| **Python SDK** | `/openai/openai-python` | 429 |
| **Node SDK** | `/openai/openai-node` | 437 |

**Documentação Oficial:**
- Guide: https://platform.openai.com/docs/guides/realtime-conversations
- API Reference: https://platform.openai.com/docs/api-reference/realtime
- SDK: https://github.com/openai/openai-python

**Queries Úteis:**
```
- "session.update turn_detection VAD configuration"
- "input_audio_buffer.append audio streaming format"
- "response.output_audio.delta audio events"
- "response.cancel interrupt barge-in"
- "function calling response.function_call_arguments"
```

---

### Google Gemini Live API (Multimodal Live)

| Recurso | Context7 Library ID | Snippets |
|---------|---------------------|----------|
| **AI Dev Docs** | `/websites/ai_google_dev_api` | - |
| **Cookbook** | GitHub (não Context7) | - |

**Documentação Oficial:**
- Live API Guide: https://ai.google.dev/gemini-api/docs/live
- Cookbook (GitHub): https://github.com/google-gemini/cookbook
- Vertex AI: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api

**Modelos:**
- `gemini-2.5-flash-live` (recomendado para baixa latência)
- `gemini-3-flash-preview` (mais recente)

**Queries Úteis:**
```
- "BidiGenerateContent WebSocket setup"
- "systemInstruction setup configuration"
- "realtimeInput audio format mimeType"
- "activityEnd interrupt barge-in"
- "serverContent modelTurn audio parts"
```

---

## 📞 Telefonia e FreeSWITCH

### FreeSWITCH

| Recurso | Context7 Library ID | Snippets |
|---------|---------------------|----------|
| **Docs** | `/signalwire/freeswitch-docs` | 8.023 |
| **Source** | `/signalwire/freeswitch` | 483 |

**Documentação Oficial:**
- Wiki: https://freeswitch.org/confluence/
- mod_audio_stream: https://github.com/drachtio/freeswitch-modules

**Queries Úteis:**
```
- "mod_audio_stream WebSocket streaming"
- "Lua script session variables"
- "uuid_audio_stream API command"
- "dialplan XML extension routing"
```

---

## 🐍 Python / Backend

### FastAPI

| Recurso | Context7 Library ID | Snippets |
|---------|---------------------|----------|
| **Docs** | `/fastapi/fastapi` | - |
| **Starlette** | `/encode/starlette` | - |

### WebSockets (Python)

| Recurso | Context7 Library ID | Snippets |
|---------|---------------------|----------|
| **websockets** | `/python-websockets/websockets` | - |

### PostgreSQL

| Recurso | Context7 Library ID | Snippets |
|---------|---------------------|----------|
| **Docs** | `/postgres/postgres` | - |
| **asyncpg** | `/MagicStack/asyncpg` | - |

---

## 🔧 FusionPBX / PHP

### FusionPBX

**Documentação Oficial:**
- Docs: https://docs.fusionpbx.com/
- GitHub: https://github.com/fusionpbx/fusionpbx

**Estrutura de Apps:**
```php
/app/{app_name}/
├── app_config.php          # Configuração do app
├── app_defaults.php        # Valores padrão
├── app_languages.php       # Traduções
├── resources/
│   └── classes/           # Classes PHP
├── {entity}_edit.php      # Formulário de edição
├── {entity}_list.php      # Listagem
└── {entity}_delete.php    # Exclusão
```

---

## 📊 Prometheus / Métricas

| Recurso | Context7 Library ID | Snippets |
|---------|---------------------|----------|
| **Client Python** | `/prometheus/client_python` | - |

**Queries Úteis:**
```
- "Counter Gauge Histogram metrics"
- "push_to_gateway pushgateway"
```

---

## 🚀 Comandos Rápidos para Consulta

### Atualizar conhecimento sobre ElevenLabs:
```
mcp_context7_query-docs(
    libraryId="/websites/elevenlabs_io",
    query="Conversational AI WebSocket real-time voice streaming"
)
```

### Atualizar conhecimento sobre OpenAI Realtime:
```
mcp_context7_query-docs(
    libraryId="/websites/platform_openai",
    query="Realtime API WebSocket session.update VAD turn_detection"
)
```

### Atualizar conhecimento sobre FreeSWITCH:
```
mcp_context7_query-docs(
    libraryId="/signalwire/freeswitch-docs",
    query="mod_audio_stream WebSocket audio streaming Lua"
)
```

---

## 📝 Notas de Atualização

### Jan/2026
- **ElevenLabs**: Formato de eventos WebSocket atualizado. `audio_event.audio_base_64` para áudio.
- **OpenAI Realtime**: Novo formato de `session.update` com `audio.input/output` aninhados.
- **Gemini Live**: Modelo `gemini-2.5-flash-live` recomendado para Voice AI.

---

## ⚠️ Problemas Conhecidos e Soluções

### ElevenLabs Policy Violation (1008)
```
"Override for field 'voice_id' is not allowed by config."
```
**Solução:** Use `use_agent_config=true` ou habilite `allow_voice_id_override=true`.

### OpenAI Rate Limit
```
"rate_limit_exceeded"
```
**Solução:** Implementar retry com backoff exponencial.

### Gemini Setup Failed
```
"Gemini setup failed, got: {...}"
```
**Solução:** Verificar se `systemInstruction` está no setup inicial.

### ElevenLabs Function Calls Não Funcionam
```
A IA não consegue desligar, transferir ou colocar em espera
```
**Solução:** O ElevenLabs **NÃO recebe function calls via API**. Configure as funções diretamente no painel:

1. Acesse [elevenlabs.io/app/conversational-ai](https://elevenlabs.io/app/conversational-ai)
2. Edite o Agent
3. Na aba "Tools/Functions", adicione:

| Função | Descrição |
|--------|-----------|
| `request_handoff` | Transfere para humano. Params: `destination` (required), `reason` |
| `end_call` | Encerra a chamada. Params: `reason` (optional) |
| `hold_call` | Coloca em espera. Sem parâmetros |
| `unhold_call` | Retira da espera. Sem parâmetros |
| `check_extension_available` | Verifica ramal. Params: `extension` (required) |

> **Nota:** OpenAI Realtime e Gemini Live recebem function calls automaticamente via API.

---

## 🔗 Links Rápidos

| Tecnologia | Docs | GitHub |
|------------|------|--------|
| ElevenLabs | [docs](https://elevenlabs.io/docs) | [repo](https://github.com/elevenlabs/elevenlabs-python) |
| OpenAI | [docs](https://platform.openai.com/docs) | [repo](https://github.com/openai/openai-python) |
| Gemini | [docs](https://ai.google.dev/gemini-api/docs) | [cookbook](https://github.com/google-gemini/cookbook) |
| FreeSWITCH | [wiki](https://freeswitch.org/confluence/) | [repo](https://github.com/signalwire/freeswitch) |
| FusionPBX | [docs](https://docs.fusionpbx.com/) | [repo](https://github.com/fusionpbx/fusionpbx) |
