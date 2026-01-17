# Change: Implementar Modo Dual ESL + WebSocket

## Resumo

Implementar correlação entre ESL Outbound (porta 8022) e WebSocket Server (porta 8085) para ter controle completo de chamadas com áudio de alta qualidade.

## Status: IN_PROGRESS

## Motivação

Atualmente temos dois modos separados:
- **WebSocket Only**: Áudio via mod_audio_stream, mas sem eventos em tempo real
- **ESL + RTP**: Eventos e controle, mas RTP não funciona bem com NAT

O **modo dual** combina o melhor dos dois:
- **Áudio**: Via mod_audio_stream (WebSocket) - compatível com NAT
- **Eventos**: Via ESL Outbound - detecção imediata de hangup, DTMF
- **Controle**: Via ESL Inbound - transfer, hold, originate

## Arquitetura Proposta

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          MODO DUAL - ARQUITETURA                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│   FreeSWITCH                              Voice AI Container                  │
│   ───────────                             ──────────────────                  │
│                                                                               │
│   Dialplan:                                                                   │
│                                                                               │
│   1. set VOICE_AI_*                       ┌────────────────────────────────┐  │
│   2. uuid_audio_stream ──── WS 8085 ────► │      WebSocket Server          │  │
│      (api_on_answer)     ◄─────────────── │      - Recebe áudio caller     │  │
│   3. answer                               │      - Envia TTS de volta      │  │
│   4. socket ─────────── TCP 8022 ───────► │      - Cria RealtimeSession    │  │
│   5. park                                 └──────────────┬─────────────────┘  │
│                                                          │ get_session(uuid)  │
│                                           ┌──────────────▼─────────────────┐  │
│                                           │      ESL Event Relay           │  │
│   ◄───────── Eventos ─────────────────────│      (NOVO!)                   │  │
│   CHANNEL_HANGUP ─────────────────────────│      - Correlaciona por UUID   │  │
│   DTMF ───────────────────────────────────│      - Dispara session.stop()  │  │
│                                           │      - Processa DTMF           │  │
│                                           └──────────────┬─────────────────┘  │
│                                                          │                    │
│                                           ┌──────────────▼─────────────────┐  │
│   ◄───────── Comandos ────────────────────│      AsyncESLClient            │  │
│   uuid_transfer                           │      (Porta 8021 - Inbound)    │  │
│   uuid_hold                               │      - Envia comandos          │  │
│   originate                               └────────────────────────────────┘  │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Componentes a Implementar

### 1. DualModeEventRelay (NOVO)

Classe que recebe conexões ESL Outbound e correlaciona com sessões WebSocket existentes.

```python
class DualModeEventRelay:
    """
    Relay de eventos ESL para sessões WebSocket.
    
    Não processa áudio - apenas eventos.
    """
    
    def __init__(self, session: OutboundSession):
        self.session = session
        self.call_uuid = session.uuid
        self._realtime_session = None
    
    def run(self):
        # Conectar ao canal
        self.session.connect()
        self.session.myevents()
        self.session.linger()
        
        # Correlacionar com sessão WebSocket existente
        manager = get_session_manager()
        self._realtime_session = manager.get_session(self.call_uuid)
        
        if not self._realtime_session:
            logger.warning(f"No WebSocket session found for {self.call_uuid}")
        
        # Monitorar eventos
        self._event_loop()
    
    def _event_loop(self):
        while True:
            event = self.session.wait_for_event(timeout=1)
            if event:
                self._handle_event(event)
    
    def _handle_event(self, event):
        event_name = event.get("Event-Name")
        
        if event_name == "CHANNEL_HANGUP":
            self._on_hangup(event)
        elif event_name == "DTMF":
            self._on_dtmf(event)
    
    def _on_hangup(self, event):
        if self._realtime_session:
            # Disparar stop na thread asyncio
            asyncio.run_coroutine_threadsafe(
                self._realtime_session.stop("caller_hangup"),
                self._get_asyncio_loop()
            )
    
    def _on_dtmf(self, event):
        digit = event.get("DTMF-Digit")
        if self._realtime_session and digit:
            asyncio.run_coroutine_threadsafe(
                self._realtime_session.handle_dtmf(digit),
                self._get_asyncio_loop()
            )
```

### 2. Modificar esl/application.py

Substituir `VoiceAIApplication` por `DualModeEventRelay` quando `AUDIO_MODE=dual`.

### 3. Adicionar handle_dtmf à Session

```python
class RealtimeSession:
    async def handle_dtmf(self, digit: str):
        """Processa DTMF recebido via ESL."""
        logger.info(f"DTMF received: {digit}", extra={"call_uuid": self.call_uuid})
        
        # Mapear DTMF para ações
        if digit == "0":
            # Transferir para operador
            await self._execute_intelligent_handoff("operador", "DTMF 0")
        elif digit == "*":
            # Repetir último menu
            await self._send_text_to_provider("Você pressionou asterisco.")
```

### 4. Function Tools para AI

Adicionar ferramentas para a IA poder executar ações:

```python
HOLD_CALL_FUNCTION = {
    "type": "function",
    "name": "hold_call",
    "description": "Coloca o cliente em espera com música. Use quando precisar verificar algo.",
    "parameters": {"type": "object", "properties": {}}
}

UNHOLD_CALL_FUNCTION = {
    "type": "function",
    "name": "unhold_call", 
    "description": "Retira o cliente da espera.",
    "parameters": {"type": "object", "properties": {}}
}

CHECK_EXTENSION_FUNCTION = {
    "type": "function",
    "name": "check_extension_available",
    "description": "Verifica se um ramal está disponível para transferência.",
    "parameters": {
        "type": "object",
        "properties": {
            "extension": {"type": "string", "description": "Número do ramal"}
        },
        "required": ["extension"]
    }
}
```

## Dialplan Atualizado (FusionPBX)

```
| Ordem | Type   | Data                                                                    |
|-------|--------|-------------------------------------------------------------------------|
| 10    | set    | VOICE_AI_SECRETARY_UUID=dc923a2f-...                                    |
| 20    | set    | VOICE_AI_DOMAIN_UUID=${domain_uuid}                                     |
| 30    | set    | STREAM_PLAYBACK=true                                                    |
| 40    | set    | jitterbuffer_msec=100:300:40                                            |
| 50    | set    | api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/...   |
| 60    | answer |                                                                         |
| 70    | socket | 127.0.0.1:8022 async full                                               |
| 80    | park   |                                                                         |
```

## Variáveis de Ambiente

```env
# Modo de áudio (CRÍTICO)
AUDIO_MODE=dual

# WebSocket (áudio)
REALTIME_HOST=0.0.0.0
REALTIME_PORT=8085

# ESL Outbound (eventos)
ESL_SERVER_HOST=0.0.0.0
ESL_SERVER_PORT=8022

# ESL Inbound (comandos)
ESL_HOST=127.0.0.1
ESL_PORT=8021
ESL_PASSWORD=ClueCon
```

## Fases de Implementação

### Fase 1: DualModeEventRelay (Core)
- [ ] Criar `realtime/esl/event_relay.py`
- [ ] Implementar correlação por call_uuid
- [ ] Implementar handler de CHANNEL_HANGUP
- [ ] Implementar handler de DTMF

### Fase 2: Session Integration
- [ ] Adicionar `handle_dtmf()` à RealtimeSession
- [ ] Adicionar `handle_external_hangup()`
- [ ] Mapear DTMF para ações

### Fase 3: Function Tools
- [ ] Implementar `hold_call` function
- [ ] Implementar `unhold_call` function
- [ ] Implementar `check_extension_available` function
- [ ] Registrar tools na sessão

### Fase 4: Atualização de __main__.py
- [ ] Usar DualModeEventRelay em vez de VoiceAIApplication no modo dual
- [ ] Garantir shutdown gracioso

### Fase 5: Testes
- [ ] Testar hangup detection
- [ ] Testar DTMF handling
- [ ] Testar hold/unhold
- [ ] Testar transfer

## Riscos e Mitigações

| Risco | Mitigação |
|-------|-----------|
| Race condition: WebSocket conecta depois do socket ESL | Retry com backoff na correlação |
| Thread safety: ESL (gevent) vs Session (asyncio) | Usar asyncio.run_coroutine_threadsafe() |
| Session não encontrada | Log warning, não crashar |

## Métricas de Sucesso

- [ ] Hangup detectado em < 100ms via ESL
- [ ] DTMF processado corretamente
- [ ] Zero regressão no modo WebSocket only
- [ ] Testes passando

## ⚠️ Configuração de Function Calls por Provider

### OpenAI Realtime e Gemini Live
**Configuração automática via API.** As tools são enviadas no `session.update` (OpenAI) ou `setup` (Gemini).

### ElevenLabs Conversational AI
**⚠️ REQUER CONFIGURAÇÃO MANUAL NO PAINEL!**

O ElevenLabs não recebe function calls via API WebSocket. É necessário configurar as funções diretamente no painel:

1. Acesse [elevenlabs.io/app/conversational-ai](https://elevenlabs.io/app/conversational-ai)
2. Edite o Agent configurado
3. Na aba "Tools/Functions", adicione:

| Função | Descrição | Parâmetros |
|--------|-----------|------------|
| `request_handoff` | Transfere a chamada para um atendente humano, departamento ou pessoa específica | `destination` (string, required), `reason` (string, optional) |
| `end_call` | Encerra a chamada telefônica | `reason` (string, optional) |
| `hold_call` | Coloca o cliente em espera com música | *(nenhum)* |
| `unhold_call` | Retira o cliente da espera | *(nenhum)* |
| `check_extension_available` | Verifica se um ramal está disponível | `extension` (string, required) |

### Descrições Recomendadas para ElevenLabs

```json
{
  "name": "request_handoff",
  "description": "Transfere a chamada para um atendente humano, departamento ou pessoa específica. Use quando o cliente pedir para falar com alguém ou quando não souber resolver.",
  "parameters": {
    "destination": {
      "type": "string",
      "description": "Nome da pessoa, departamento ou 'qualquer atendente'. Exemplos: 'Jeni', 'financeiro', 'suporte'"
    },
    "reason": {
      "type": "string", 
      "description": "Motivo pelo qual o cliente quer falar com alguém"
    }
  }
}
```

```json
{
  "name": "end_call",
  "description": "Encerra a chamada telefônica. Use quando a conversa chegou ao fim, o cliente se despediu, ou quando todas as dúvidas foram resolvidas e você já deu tchau.",
  "parameters": {
    "reason": {
      "type": "string",
      "description": "Motivo do encerramento: 'cliente_despediu', 'atendimento_concluido', 'timeout'"
    }
  }
}
```

```json
{
  "name": "hold_call",
  "description": "Coloca o cliente em espera com música. Use quando precisar verificar algo ou consultar informações. Lembre-se de avisar o cliente antes de colocar em espera.",
  "parameters": {}
}
```

```json
{
  "name": "unhold_call",
  "description": "Retira o cliente da espera. Use após verificar as informações necessárias.",
  "parameters": {}
}
```

```json
{
  "name": "check_extension_available",
  "description": "Verifica se um ramal ou atendente está disponível para transferência. Use antes de prometer ao cliente que vai transferir para alguém específico.",
  "parameters": {
    "extension": {
      "type": "string",
      "description": "Número do ramal para verificar (ex: '1001', '200')"
    }
  }
}
```

## Referências

- [mod_audio_stream](https://github.com/amigniter/mod_audio_stream)
- [FreeSWITCH ESL](https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_event_socket_1048924/)
- [greenswitch](https://github.com/EvoluxBR/greenswitch)
- `voice-ai-ivr/docs/HYBRID_ARCHITECTURE.md`

---

**Criado:** 2026-01-17
**Autor:** Claude AI + Juliano Targa
**Status:** IN_PROGRESS
