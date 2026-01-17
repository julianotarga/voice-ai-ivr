# Tasks: Modo Dual ESL + WebSocket

## Fase 1: DualModeEventRelay (Core)

### 1.1 Criar event_relay.py
- [ ] **1.1.1** Criar arquivo `realtime/esl/event_relay.py`
- [ ] **1.1.2** Implementar classe `DualModeEventRelay`
- [ ] **1.1.3** Implementar `run()` com linger e myevents
- [ ] **1.1.4** Implementar correlação com session_manager

### 1.2 Event Handlers
- [ ] **1.2.1** Implementar `_handle_channel_hangup()`
- [ ] **1.2.2** Implementar `_handle_dtmf()`
- [ ] **1.2.3** Implementar `_handle_channel_bridge()` (para saber quando transferência conectou)

### 1.3 Thread Safety
- [ ] **1.3.1** Implementar `_get_asyncio_loop()` para obter loop da thread principal
- [ ] **1.3.2** Usar `asyncio.run_coroutine_threadsafe()` para chamar métodos da session

---

## Fase 2: Session Integration

### 2.1 Novos Métodos na Session
- [ ] **2.1.1** Adicionar `handle_dtmf(digit: str)` à RealtimeSession
- [ ] **2.1.2** Adicionar `handle_external_hangup()` para cleanup
- [ ] **2.1.3** Adicionar `set_esl_connected(connected: bool)` para saber que ESL está ativo

### 2.2 DTMF Actions
- [ ] **2.2.1** Mapear DTMF "0" → Transferir para operador
- [ ] **2.2.2** Mapear DTMF "#" → Repetir menu
- [ ] **2.2.3** Mapear DTMF "*" → Encerrar chamada
- [ ] **2.2.4** Configurar mapeamento via banco de dados

---

## Fase 3: Function Tools para AI

### 3.1 Definir Tools
- [ ] **3.1.1** Criar `HOLD_CALL_FUNCTION_DEFINITION`
- [ ] **3.1.2** Criar `UNHOLD_CALL_FUNCTION_DEFINITION`
- [ ] **3.1.3** Criar `CHECK_EXTENSION_FUNCTION_DEFINITION`

### 3.2 Implementar Execução
- [ ] **3.2.1** Implementar `_execute_hold_call()` na session
- [ ] **3.2.2** Implementar `_execute_unhold_call()` na session
- [ ] **3.2.3** Implementar `_execute_check_extension()` na session

### 3.3 Registrar Tools
- [ ] **3.3.1** Adicionar tools no `server.py` junto com handoff e end_call

---

## Fase 4: Atualização de __main__.py

### 4.1 Factory Pattern
- [ ] **4.1.1** Criar factory para escolher entre `VoiceAIApplication` e `DualModeEventRelay`
- [ ] **4.1.2** Modificar `run_dual_mode()` para usar `DualModeEventRelay`

### 4.2 Compartilhar Asyncio Loop
- [ ] **4.2.1** Passar referência do asyncio loop para a thread ESL
- [ ] **4.2.2** Garantir que o loop está rodando antes de iniciar ESL

---

## Fase 5: Testes

### 5.1 Unit Tests
- [ ] **5.1.1** Testar correlação de sessões
- [ ] **5.1.2** Testar handler de hangup
- [ ] **5.1.3** Testar handler de DTMF

### 5.2 Integration Tests
- [ ] **5.2.1** Testar fluxo completo: ligar → conversar → desligar
- [ ] **5.2.2** Testar DTMF durante conversa
- [ ] **5.2.3** Testar hold/unhold

---

## Estimativa

| Fase | Horas | Prioridade |
|------|-------|------------|
| Fase 1 | 3-4h | P0 |
| Fase 2 | 2-3h | P0 |
| Fase 3 | 2-3h | P1 |
| Fase 4 | 1-2h | P0 |
| Fase 5 | 2-3h | P1 |
| **Total** | **10-15h** | |

---

## Ordem de Execução

1. **Fase 1.1** - Criar estrutura base do EventRelay
2. **Fase 4.1** - Integrar com __main__.py (testar conexão)
3. **Fase 1.2** - Implementar handlers de eventos
4. **Fase 1.3** - Thread safety
5. **Fase 2.1** - Métodos na session
6. **Fase 2.2** - DTMF actions
7. **Fase 3** - Function tools
8. **Fase 5** - Testes

---

**Atualizado:** 2026-01-17
