# AGENTS.md - Voice AI IVR

## 🏗️ Arquitetura de Controle Interno (v2 - Jan/2026)

O sistema usa uma arquitetura de **controle interno** que reduz dependência do FreeSWITCH.

### Componentes Core (realtime/core/)

| Componente | Arquivo | Responsabilidade |
|------------|---------|------------------|
| **EventBus** | `event_bus.py` | Pub/sub async de eventos tipados |
| **StateMachine** | `state_machine.py` | Estados da chamada com guards |
| **HeartbeatMonitor** | `heartbeat.py` | Detecção proativa de problemas |
| **TimeoutManager** | `timeout_manager.py` | Timeouts controlados internamente |
| **VoiceEvent** | `events.py` | Tipos de eventos (enum + dataclass) |

### Regras de Modificação

1. **SEMPRE use VoiceEventType** para novos eventos (não strings)
2. **NUNCA manipule StateMachine._state diretamente** - use `trigger()`
3. **Guards devem retornar bool** - não lançar exceções
4. **Heartbeat pause/resume** durante transferências

### Fluxo de Eventos Típico

```
┌─────────────────────────────────────────────────────────────┐
│ 1. TransferManager detecta necessidade de transferir        │
│    └─> emit(TRANSFER_REQUESTED)                             │
│                                                              │
│ 2. RealtimeSession recebe evento                            │
│    └─> state_machine.request_transfer()                     │
│    └─> state_machine.trigger("destination_validated")       │
│                                                              │
│ 3. ConferenceTransferManager executa                        │
│    └─> emit(TRANSFER_DIALING, TRANSFER_ANSWERED, etc)       │
│                                                              │
│ 4. RealtimeSession sincroniza                               │
│    └─> state_machine.trigger("bridge_complete")             │
└─────────────────────────────────────────────────────────────┘
```

### Logs Estruturados

```bash
# Identificação visual por emoji
📢 [EVENT_BUS]      # Eventos emitidos
🔄 [STATE_MACHINE]  # Transições de estado  
💓 [HEARTBEAT]      # Monitoramento de saúde
⏱️ [TIMEOUT_MGR]   # Timeouts
📞 [SESSION]        # Início/fim de chamada
⚠️ [CORE]          # Warnings

# Filtrar por componente
grep "STATE_MACHINE" logs/realtime-error.log
grep "📞" logs/realtime-error.log
```

---

## 📚 Knowledge Base (OBRIGATÓRIO)

**SEMPRE consulte a Knowledge Base antes de modificar providers de IA:**

- **Arquivo principal:** `docs/KNOWLEDGE_BASE.md`
- **Arquitetura interna:** `docs/PLANO-ARQUITETURA-INTERNA.md`
- **Context7 MCP:** Use para buscar documentação atualizada

### Context7 Library IDs
| Provider | Library ID | Snippets |
|----------|------------|----------|
| ElevenLabs | `/websites/elevenlabs_io` | 6.866 |
| OpenAI Realtime | `/websites/platform_openai` | 9.418 |
| FreeSWITCH | `/signalwire/freeswitch-docs` | 8.023 |

### Exemplo de Consulta
```python
# Antes de modificar elevenlabs_conv.py:
mcp_context7_query-docs(
    libraryId="/websites/elevenlabs_io",
    query="Conversational AI WebSocket events audio format"
)
```

## Dev environment tips
- Python 3.11+ com virtualenv
- `pip install -r requirements.txt` para dependências
- `docker-compose up -d` para PostgreSQL e Redis
- `python -m uvicorn voice_ai_service.main:app --reload` para dev

## Testing instructions
- `pytest tests/` para testes unitários
- `pytest tests/integration/` para testes de integração
- Verificar conexão com FreeSWITCH antes de testes E2E

## PR instructions
- Follow Conventional Commits (ex: `feat(providers): add gemini live support`)
- Atualizar `docs/KNOWLEDGE_BASE.md` se descobrir nova documentação
- Verificar compatibilidade com FreeSWITCH 16kHz ↔ Provider sample rate
- Testar barge-in e VAD em chamada real

## Repository map
- `database/` — Migrations SQL para FusionPBX (v_voice_secretaries, v_voice_ai_providers)
- `deploy/` — Docker Compose e scripts de deploy
- `docs/` — Documentação, **incluindo KNOWLEDGE_BASE.md**
- `freeswitch/` — Lua scripts e configurações de dialplan
- `fusionpbx-app/` — App PHP para gerenciamento via FusionPBX UI
- `voice-ai-service/` — Bridge Python (FastAPI + WebSocket)
  - `realtime/providers/` — Implementações de cada AI provider
  - `realtime/handlers/` — Handlers (handoff, function call)
  - `realtime/utils/` — Utilitários (resampler, metrics)

## AI Context References
- **Knowledge Base:** `docs/KNOWLEDGE_BASE.md` (Context7 references)
- **System Overview:** `docs/SYSTEM_OVERVIEW.md`
- **Handoff OmniPlay:** `docs/HANDOFF_OMNIPLAY.md`
- **Deploy Instructions:** `docs/DEPLOY_INSTRUCTIONS.md`

## Provider-Specific Notes

### ElevenLabs
- Sample rate: 16kHz (mesmo que FreeSWITCH, sem resample)
- Formato áudio: `user_audio_chunk` (SEM type!)
- Barge-in: `user_activity` (não `interrupt`)
- Policy violations: use `use_agent_config=true`

### OpenAI Realtime
- Sample rate: 24kHz (precisa resample de/para 16kHz)
- Formato áudio: `input_audio_buffer.append`
- Barge-in: `response.cancel`
- VAD: `turn_detection` no `session.update`

### Gemini Live
- Sample rate: Input 16kHz, Output 24kHz (precisa resample)
- Formato áudio: `realtimeInput.audio`
- Barge-in: `activityEnd`
- Setup: `systemInstruction` DEVE estar no setup inicial
