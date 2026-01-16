# Tasks: Sistema de Handoff Inteligente de Voz

## Metadata
- **Proposal:** intelligent-voice-handoff/proposal.md
- **Design:** intelligent-voice-handoff/design.md
- **Created:** 2026-01-16
- **Status:** PROPOSED

## Resumo

Tarefas para implementar o sistema de handoff inteligente com transferência de chamadas.

---

## Fase 1: Banco de Dados e Configuração (1 dia)

### 1.1 Migration: Tabela de Destinos de Transferência
- **Arquivo:** `database/migrations/002_create_voice_transfer_destinations.sql`
- **Descrição:** Criar tabela `v_voice_transfer_destinations`
- **Campos:**
  - `transfer_destination_uuid` (PK)
  - `domain_uuid` (FK)
  - `secretary_uuid` (FK opcional)
  - `name`, `aliases` (JSONB)
  - `destination_type`, `destination_number`, `destination_context`
  - `ring_timeout_seconds`, `max_retries`, `fallback_action`
  - `department`, `role`, `description`
  - `working_hours` (JSONB)
  - `priority`, `is_enabled`, `is_default`
- **Índices:** domain_uuid, secretary_uuid, is_enabled
- **Constraint:** único default por domain
- **Prioridade:** Alta
- **Estimativa:** 2h

### 1.2 Seed: Dados de Exemplo
- **Arquivo:** `database/seeds/001_sample_transfer_destinations.sql`
- **Descrição:** Inserir destinos de exemplo para testes
- **Dados:**
  - Atendimento Geral (ring_group 9000) - DEFAULT
  - Suporte Técnico (queue 5001)
  - Financeiro (extension 1004)
- **Prioridade:** Média
- **Estimativa:** 30min

### 1.3 ConfigLoader: Carregar Destinos
- **Arquivo:** `voice-ai-service/realtime/config_loader.py`
- **Descrição:** Adicionar método `get_transfer_destinations(domain_uuid, secretary_uuid)`
- **Retorno:** Lista de `TransferDestination`
- **Cache:** Redis com TTL de 5 minutos
- **Prioridade:** Alta
- **Estimativa:** 1h

---

## Fase 2: TransferManager (2 dias)

### 2.1 Modelo de Dados
- **Arquivo:** `voice-ai-service/realtime/handlers/transfer_manager.py`
- **Classes:**
  - `TransferResult` (Enum: SUCCESS, BUSY, NO_ANSWER, etc.)
  - `TransferDestination` (dataclass)
  - `TransferAttempt` (dataclass)
- **Prioridade:** Alta
- **Estimativa:** 1h

### 2.2 TransferManager Base
- **Arquivo:** `voice-ai-service/realtime/handlers/transfer_manager.py`
- **Métodos:**
  - `__init__(domain_uuid, secretary_uuid, call_uuid, esl_client)`
  - `load_destinations()` - carrega do banco
  - `find_destination(user_text)` - fuzzy match em aliases
  - `get_status_message(result, destination)` - mensagens amigáveis
  - `get_fallback_message(destination)` - oferece alternativas
- **Prioridade:** Alta
- **Estimativa:** 3h

### 2.3 Integração ESL para Transfer
- **Arquivo:** `voice-ai-service/realtime/handlers/transfer_manager.py`
- **Métodos:**
  - `execute_transfer(destination)` - executa attended transfer
  - `_hold_call()` - coloca em hold
  - `_unhold_call()` - retira do hold
  - `_originate_to_destination(dest)` - origina nova chamada
  - `_build_dial_string(dest)` - constrói dial string
- **Dependência:** ESL client (greenswitch)
- **Prioridade:** Alta
- **Estimativa:** 4h

### 2.4 Monitoramento de Resultado
- **Arquivo:** `voice-ai-service/realtime/handlers/transfer_manager.py`
- **Funcionalidade:**
  - Monitorar eventos ESL: CHANNEL_ANSWER, CHANNEL_HANGUP
  - Detectar SIP codes: 486 (Busy), 480 (Unavailable), 487 (Timeout)
  - Retornar `TransferResult` apropriado
- **Prioridade:** Alta
- **Estimativa:** 3h

### 2.5 Testes Unitários TransferManager
- **Arquivo:** `tests/test_transfer_manager.py`
- **Casos:**
  - find_destination com diferentes inputs
  - get_status_message para cada TransferResult
  - Mocking de ESL para execute_transfer
- **Prioridade:** Média
- **Estimativa:** 2h

---

## Fase 3: Integração com Session (1 dia)

### 3.1 Inicialização do TransferManager
- **Arquivo:** `voice-ai-service/realtime/session.py`
- **Modificações:**
  - Instanciar `TransferManager` no `__init__`
  - Carregar destinos no `start()`
- **Prioridade:** Alta
- **Estimativa:** 1h

### 3.2 Handler de Intent de Transfer
- **Arquivo:** `voice-ai-service/realtime/session.py`
- **Método:** `_handle_transfer_intent(user_text)`
- **Fluxo:**
  1. Encontrar destino
  2. Informar cliente
  3. Pausar agente IA
  4. Executar transfer
  5. Processar resultado
  6. Retomar agente ou criar ticket
- **Prioridade:** Alta
- **Estimativa:** 3h

### 3.3 Function Calling para Transfer
- **Arquivo:** `voice-ai-service/realtime/session.py`
- **Modificações em `_execute_function()`:**
  - Tratar `transfer_call(destination: str)`
  - Chamar `_handle_transfer_intent()`
- **Prioridade:** Alta
- **Estimativa:** 1h

### 3.4 System Prompt com Destinos
- **Arquivo:** `voice-ai-service/realtime/config_loader.py`
- **Novo método:** `build_transfer_destinations_context(destinations)`
- **Formato:** Tabela markdown com nome, departamento, função
- **Injetar no system prompt da secretária**
- **Prioridade:** Alta
- **Estimativa:** 1h

---

## Fase 4: Gravação de Chamadas (1 dia)

### 4.1 RecordingManager
- **Arquivo:** `voice-ai-service/realtime/handlers/recording_manager.py`
- **Métodos:**
  - `get_recording_path()` - busca arquivo por call_uuid
  - `upload_recording()` - upload para MinIO
  - `start_recording()` - inicia via ESL (opcional)
  - `stop_recording()` - para via ESL (opcional)
- **Prioridade:** Alta
- **Estimativa:** 2h

### 4.2 Modificar Dialplan para Gravar
- **Arquivo:** `freeswitch/scripts/voice_secretary.lua`
- **Modificações:**
  - Adicionar `session:execute("record_session", path)`
  - Criar diretório de gravação
  - Garantir formato WAV 16kHz
- **Prioridade:** Alta
- **Estimativa:** 1h

### 4.3 Integrar Gravação no Handoff
- **Arquivo:** `voice-ai-service/realtime/session.py`
- **Modificações em `_initiate_handoff()`:**
  - Instanciar `RecordingManager`
  - Upload da gravação
  - Passar `recording_url` para `create_fallback_ticket()`
- **Prioridade:** Alta
- **Estimativa:** 2h

### 4.4 Exibir Áudio no Ticket (OmniPlay)
- **Arquivo:** `backend/src/services/VoiceServices/VoiceHandoffService.ts`
- **Modificações:**
  - Criar Message com `mediaType: "audio"`
  - Anexar URL da gravação
- **Prioridade:** Alta
- **Estimativa:** 1h

---

## Fase 5: Interface FusionPBX (1 dia)

### 5.1 App Config
- **Arquivo:** `fusionpbx-app/voice_transfer_destinations/app_config.php`
- **Conteúdo:**
  - Nome do app
  - Permissões
  - Menu
- **Prioridade:** Média
- **Estimativa:** 30min

### 5.2 Listagem de Destinos
- **Arquivo:** `fusionpbx-app/voice_transfer_destinations/voice_transfer_destinations.php`
- **Funcionalidades:**
  - Listar todos os destinos do domain
  - Toggle enabled/disabled
  - Marcar default
  - Botões edit/delete
- **Prioridade:** Média
- **Estimativa:** 2h

### 5.3 Formulário de Edição
- **Arquivo:** `fusionpbx-app/voice_transfer_destinations/voice_transfer_destination_edit.php`
- **Campos:**
  - Nome e aliases (tags)
  - Tipo de destino (select)
  - Número do destino
  - Timeout e retries
  - Fallback action
  - Departamento, função, descrição
  - Horário de funcionamento
- **Prioridade:** Média
- **Estimativa:** 3h

### 5.4 Languages
- **Arquivo:** `fusionpbx-app/voice_transfer_destinations/app_languages.php`
- **Idiomas:** pt-br, en-us
- **Prioridade:** Baixa
- **Estimativa:** 30min

### 5.5 Vincular à Secretária
- **Arquivo:** `fusionpbx-app/voice_secretaries/voice_secretary_edit.php`
- **Modificações:**
  - Adicionar seção "Destinos de Transferência"
  - Lista de destinos vinculados
  - Botão para gerenciar destinos
- **Prioridade:** Média
- **Estimativa:** 1h

---

## Fase 6: Testes e Documentação (1 dia)

### 6.1 Testes de Integração
- **Arquivo:** `tests/integration/test_transfer_flow.py`
- **Cenários:**
  - Transfer bem-sucedido (mock ESL)
  - Transfer ocupado → oferece ticket
  - Transfer timeout → cria ticket
  - Gravação anexada ao ticket
- **Prioridade:** Alta
- **Estimativa:** 3h

### 6.2 Teste Manual End-to-End
- **Descrição:** Testar fluxo completo em ambiente de staging
- **Checklist:**
  - [ ] Ligar para secretária
  - [ ] Pedir para falar com destino configurado
  - [ ] Verificar transfer (ou fallback)
  - [ ] Verificar ticket criado com áudio
  - [ ] Verificar transcrição no ticket
- **Prioridade:** Alta
- **Estimativa:** 2h

### 6.3 Documentação
- **Arquivos:**
  - `docs/TRANSFER_DESTINATIONS.md` - guia de configuração
  - `docs/HANDOFF_FLOW.md` - diagrama de fluxo
- **Prioridade:** Média
- **Estimativa:** 1h

---

## Dependências Entre Tarefas

```
1.1 ─────► 1.3 ─────► 2.2 ─────► 2.3 ─────► 3.1 ─────► 3.2
                        │                      │
                        ▼                      ▼
                       2.4                    3.3
                        │                      │
                        └──────────┬───────────┘
                                   ▼
4.1 ─────► 4.2 ─────► 4.3 ─────► 6.1 ─────► 6.2
                        │
                        ▼
                       4.4

5.1 ─────► 5.2 ─────► 5.3 ─────► 5.5
             │
             ▼
            5.4
```

---

## Resumo de Estimativas

| Fase | Descrição | Estimativa |
|------|-----------|------------|
| 1 | Banco de Dados | 3.5h |
| 2 | TransferManager | 13h |
| 3 | Integração Session | 6h |
| 4 | Gravação | 6h |
| 5 | Interface FusionPBX | 7h |
| 6 | Testes e Docs | 6h |
| **Total** | | **41.5h** (~5-6 dias) |

---

## Checklist de Entrega

### MVP (Mínimo Viável)
- [ ] Migration executada
- [ ] TransferManager funcionando
- [ ] Transfer via ESL funcionando
- [ ] Fallback para ticket funcionando
- [ ] Gravação anexada ao ticket

### Completo
- [ ] Interface FusionPBX
- [ ] Todos os testes passando
- [ ] Documentação completa
- [ ] Deploy em produção
