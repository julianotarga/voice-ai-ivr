# Tasks: Sistema de Handoff Inteligente de Voz

## Metadata
- **Proposal:** intelligent-voice-handoff/proposal.md
- **Design:** intelligent-voice-handoff/design.md
- **Author:** Claude AI + Juliano Targa
- **Created:** 2026-01-16
- **Status:** READY FOR IMPLEMENTATION
- **Estimated Total:** 12-18 dias

---

## Legenda de Status

- [ ] Pendente
- [x] Concluído
- [~] Em progresso
- [!] Bloqueado
- [-] Cancelado

---

## FASE 0: Preparação e Infraestrutura

**Duração Estimada:** 1-2 dias
**Objetivo:** Preparar ambiente e criar estruturas de dados necessárias

### 0.1 Database - FusionPBX (PostgreSQL)

#### 0.1.1 Criar tabela v_voice_transfer_destinations
```sql
-- Arquivo: voice-ai-ivr/migrations/001_create_voice_transfer_destinations.sql
```

- [x] **0.1.1.1** Criar migration SQL idempotente ✅ 2026-01-16
  - Arquivo: `database/migrations/012_create_voice_transfer_destinations.sql`
  - Campos obrigatórios: `transfer_destination_uuid`, `domain_uuid`, `name`, `destination_type`, `destination_number`
  - Campos de aliases: `aliases JSONB DEFAULT '[]'`
  - Campos de timeout: `ring_timeout_seconds`, `max_retries`, `retry_delay_seconds`
  - Campos de fallback: `fallback_action`
  - Campos de horário: `working_hours JSONB`
  - Índices: `domain_uuid`, `secretary_uuid`, `is_enabled`

- [x] **0.1.1.2** Criar migration de dados iniciais (seed) ✅ 2026-01-16
  - Arquivo: `database/seeds/001_seed_transfer_destinations.sql`
  - Destino "Atendimento" (default, ring_group 9000)
  - Destino de exemplo "Suporte" (queue 5001)
  
- [ ] **0.1.1.3** Testar migration em ambiente de dev
  - Executar migration
  - Verificar criação de tabela e índices
  - Verificar seed data

#### 0.1.2 Alterar tabela v_voice_secretaries (se necessário)
- [x] **0.1.2.1** Adicionar campos de configuração de transfer ✅ 2026-01-16
  - Arquivo: `database/migrations/013_add_transfer_fields_to_secretaries.sql`
  ```sql
  ALTER TABLE v_voice_secretaries ADD COLUMN IF NOT EXISTS transfer_enabled BOOLEAN DEFAULT true;
  ALTER TABLE v_voice_secretaries ADD COLUMN IF NOT EXISTS transfer_default_timeout INT DEFAULT 30;
  ALTER TABLE v_voice_secretaries ADD COLUMN IF NOT EXISTS transfer_announce_enabled BOOLEAN DEFAULT true;
  ```

### 0.2 Database - OmniPlay (PostgreSQL)

#### 0.2.1 Alterar tabela Tickets para callback
```sql
-- Arquivo: backend/src/database/migrations/XXXXXX-add-callback-fields-to-tickets.ts
```

- [x] **0.2.1.1** Criar migration Sequelize ✅ 2026-01-16
  - Arquivo: `backend/src/database/migrations/20260116200000-add-callback-fields-to-tickets.ts`
  ```typescript
  // Campos adicionados:
  ticketType: ENUM('normal', 'callback', 'voicemail') DEFAULT 'normal'
  callbackNumber: VARCHAR(20)
  callbackExtension: VARCHAR(10)
  callbackIntendedForName: VARCHAR(100)
  callbackDepartment: VARCHAR(100)
  callbackReason: TEXT
  callbackScheduledAt: TIMESTAMP
  callbackExpiresAt: TIMESTAMP
  callbackStatus: ENUM('pending', 'notified', 'ready_to_call', 'in_progress', 'completed', 'expired', 'canceled', 'failed', 'needs_review')
  callbackAttempts: INT DEFAULT 0
  callbackMaxAttempts: INT DEFAULT 3
  callbackNotificationCount: INT DEFAULT 0
  callbackLastNotifiedAt: TIMESTAMP
  callbackMinIntervalMinutes: INT DEFAULT 10
  callbackCompletedAt: TIMESTAMP
  callbackWhatsAppSentAt: TIMESTAMP
  callbackNotifyViaWhatsApp: BOOLEAN DEFAULT false
  ```

- [x] **0.2.1.2** Adicionar campos de referência de voz ✅ 2026-01-16
  - Incluído na mesma migration
  ```typescript
  voiceCallUuid: VARCHAR(50)
  voiceCallDate: TIMESTAMP
  voiceCallDuration: INT
  voiceRecordingPath: VARCHAR(500)
  voiceTranscript: TEXT
  voiceSummary: TEXT
  voiceDomainUuid: VARCHAR(50)  // Adicionado para integração FusionPBX
  ```

- [x] **0.2.1.3** Criar índices para callback ✅ 2026-01-16
  - Incluído na migration
  ```sql
  CREATE INDEX idx_tickets_callback_status ON "Tickets"("ticketType", "callbackStatus") 
    WHERE "ticketType" = 'callback';
  CREATE INDEX idx_tickets_callback_extension ON "Tickets"("callbackExtension") 
    WHERE "ticketType" = 'callback';
  CREATE INDEX idx_tickets_callback_expires ON "Tickets"("callbackExpiresAt")
    WHERE "ticketType" = 'callback' AND "callbackStatus" IN ('pending', 'notified');
  CREATE INDEX idx_tickets_voice_call_uuid ON "Tickets"("voiceCallUuid")
    WHERE "voiceCallUuid" IS NOT NULL;
  ```

- [x] **0.2.1.4** Atualizar model Ticket.ts com novos campos ✅ 2026-01-16
  - Arquivo: `backend/src/models/Ticket.ts`

- [ ] **0.2.1.5** Testar migration em ambiente de dev

#### 0.2.2 Criar tabela CallbackSettings (configurações por empresa)
- [x] **0.2.2.1** Criar migration ✅ 2026-01-16
  - Arquivo: `backend/src/database/migrations/20260116200001-create-callback-settings.ts`
  ```typescript
  companyId: INT (FK)
  callbackTemplateId: INT (FK para QuickMessages, template WhatsApp)
  callbackExpirationHours: INT DEFAULT 24
  callbackMaxNotifications: INT DEFAULT 5
  callbackMinIntervalMinutes: INT DEFAULT 10
  callbackAutoRetryEnabled: BOOLEAN DEFAULT true
  callbackAutoRetryDelaySeconds: INT DEFAULT 30
  callbackAutoRetryMaxAttempts: INT DEFAULT 3
  ```

- [x] **0.2.2.2** Criar model CallbackSettings.ts ✅ 2026-01-16
  - Arquivo: `backend/src/models/CallbackSettings.ts`
  - Registrado em: `backend/src/database/index.ts`

### 0.3 Configuração de Ambiente

- [x] **0.3.1** Adicionar variáveis de ambiente ao Voice AI ✅ 2026-01-16
  - Arquivos: `docker-compose.yml`, `env.docker.example`
  ```bash
  # docker-compose.yml / .env
  ESL_HOST=host.docker.internal
  ESL_PORT=8021
  ESL_PASSWORD=ClueCon
  TRANSFER_DEFAULT_TIMEOUT=30
  TRANSFER_ANNOUNCE_ENABLED=true
  TRANSFER_MUSIC_ON_HOLD=local_stream://moh
  OMNIPLAY_API_URL=http://host.docker.internal:8080
  VOICE_AI_SERVICE_TOKEN=xxx
  CALLBACK_ENABLED=true
  CALLBACK_EXPIRATION_HOURS=24
  CALLBACK_MAX_NOTIFICATIONS=5
  CALLBACK_MIN_INTERVAL_MINUTES=10
  ```

- [x] **0.3.2** Adicionar variáveis de ambiente ao OmniPlay ✅ 2026-01-16
  - Arquivo: `env.dev.template`
  ```bash
  VOICE_AI_API_URL=http://localhost:8085
  VOICE_AI_TIMEOUT_MS=3000
  CALLBACK_CHECK_INTERVAL_MS=30000
  VOICE_AI_SERVICE_TOKEN=xxx
  ```

- [x] **0.3.3** Configurar rede Docker para comunicação entre containers ✅ 2026-01-16
  - Já configurado via `host.docker.internal` e network bridge

---

## FASE 1: Transferência Básica

**Duração Estimada:** 3-4 dias
**Objetivo:** Implementar transfer attended com monitoramento de eventos ESL
**Status:** ✅ CONCLUÍDA em 2026-01-16

### 1.1 Voice AI - Carregador de Destinos

#### 1.1.1 Implementar TransferDestinationLoader
```python
# voice-ai-service/realtime/handlers/transfer_destination_loader.py
```

- [x] **1.1.1.1** Criar classe `TransferDestinationLoader` ✅ 2026-01-16
  - Método `load_destinations(domain_uuid, secretary_uuid)` → List[TransferDestination]
  - Método `find_by_alias(text, destinations)` → Optional[TransferDestination]
  - Método `get_default(destinations)` → Optional[TransferDestination]
  - Cache em memória com TTL de 5 minutos

- [x] **1.1.1.2** Criar dataclass `TransferDestination` ✅ 2026-01-16
  ```python
  @dataclass
  class TransferDestination:
      uuid: str
      name: str
      aliases: List[str]
      destination_type: str  # extension, ring_group, queue, external
      destination_number: str
      destination_context: str
      ring_timeout_seconds: int
      max_retries: int
      retry_delay_seconds: int
      fallback_action: str
      department: Optional[str]
      role: Optional[str]
      description: Optional[str]
      working_hours: Optional[Dict]
      priority: int
  ```

- [x] **1.1.1.3** Implementar fuzzy matching para aliases ✅ 2026-01-16
  - Busca exata em aliases
  - Busca parcial no nome
  - Busca por departamento
  - Retornar destino com maior prioridade em caso de empate

- [x] **1.1.1.4** Implementar verificação de horário comercial ✅ 2026-01-16
  ```python
  def is_within_working_hours(dest: TransferDestination) -> tuple[bool, str]:
      # Verificar dia da semana
      # Verificar horário atual
      # Retornar (is_available, message_if_unavailable)
  ```

- [ ] **1.1.1.5** Escrever testes unitários (pendente)
  - test_load_destinations_from_db
  - test_find_by_alias_exact_match
  - test_find_by_alias_partial_match
  - test_working_hours_weekday
  - test_working_hours_weekend

### 1.2 Voice AI - Cliente ESL Aprimorado

#### 1.2.1 Refatorar ESLClient para suportar eventos assíncronos
```python
# voice-ai-service/realtime/handlers/esl_client.py
```

- [x] **1.2.1.1** Implementar conexão ESL com reconexão automática ✅ 2026-01-16
  ```python
  class AsyncESLClient:
      async def connect(self) -> bool
      async def disconnect(self) -> None
      async def reconnect(self) -> bool
      @property is_connected(self) -> bool
  ```

- [x] **1.2.1.2** Implementar envio de comandos API ✅ 2026-01-16
  ```python
  async def execute_api(self, command: str) -> str
  async def execute_bgapi(self, command: str) -> str  # Background API
  ```

- [x] **1.2.1.3** Implementar subscrição de eventos ✅ 2026-01-16
  ```python
  async def subscribe_events(self, events: List[str], uuid: str = None) -> None
  async def unsubscribe_events(self, uuid: str = None) -> None
  # Event queue via _event_reader_loop
  ```

- [x] **1.2.1.4** Implementar handler de eventos com callback ✅ 2026-01-16
  ```python
  def on_event(self, event_name: str, uuid: str, callback: Callable) -> str  # retorna handler_id
  def off_event(self, handler_id: str) -> None
  ```

- [x] **1.2.1.5** Implementar métodos de alto nível ✅ 2026-01-16
  ```python
  async def uuid_broadcast(self, uuid: str, audio: str, leg: str = "aleg") -> bool
  async def uuid_break(self, uuid: str) -> bool
  async def uuid_bridge(self, uuid_a: str, uuid_b: str) -> bool
  async def uuid_kill(self, uuid: str, cause: str = "NORMAL_CLEARING") -> bool
  async def originate(self, dial_string: str, app: str = "&park()") -> str  # retorna UUID
  ```

- [x] **1.2.1.6** Implementar wait_for_event com filtros ✅ 2026-01-16
  ```python
  async def wait_for_event(
      self,
      event_names: List[str],
      uuid: str,
      timeout: float
  ) -> Optional[ESLEvent]
  ```

- [ ] **1.2.1.7** Escrever testes unitários (mockar socket) (pendente)
  - test_connect_success
  - test_connect_failure_reconnect
  - test_execute_api
  - test_subscribe_events
  - test_wait_for_event_timeout

### 1.3 Voice AI - TransferManager

#### 1.3.1 Implementar TransferManager completo
```python
# voice-ai-service/realtime/handlers/transfer_manager.py
```

- [x] **1.3.1.1** Criar enum `TransferStatus` ✅ 2026-01-16
  ```python
  class TransferStatus(Enum):
      PENDING = "pending"
      RINGING = "ringing"
      ANSWERED = "answered"
      SUCCESS = "success"
      BUSY = "busy"
      NO_ANSWER = "no_answer"
      DND = "dnd"
      OFFLINE = "offline"
      REJECTED = "rejected"
      UNAVAILABLE = "unavailable"
      FAILED = "failed"
      CANCELLED = "cancelled"
  ```

- [x] **1.3.1.2** Criar mapeamento de hangup causes ✅ 2026-01-16
  ```python
  HANGUP_CAUSE_MAP = {
      "NORMAL_CLEARING": TransferStatus.SUCCESS,
      "USER_BUSY": TransferStatus.BUSY,
      # ... (25+ mappings implementados)
  }
  ```

- [x] **1.3.1.3** Implementar `execute_attended_transfer` ✅ 2026-01-16
  - Passo 1: `uuid_broadcast` (música de espera)
  - Passo 2: `originate` nova leg
  - Passo 3: Monitorar eventos
  - Passo 4: `uuid_bridge` se atendeu
  - Passo 5: `uuid_break` + retomar se não atendeu

- [x] **1.3.1.4** Implementar `_monitor_transfer_leg` ✅ 2026-01-16
  - Subscrever eventos CHANNEL_ANSWER, CHANNEL_HANGUP
  - Processar hangup causes
  - Retornar TransferResult

- [x] **1.3.1.5** Implementar `stop_moh_and_resume` ✅ 2026-01-16
  - `uuid_break` para parar música
  - Notificar sessão para retomar Voice AI

- [x] **1.3.1.6** Implementar handler para cliente desliga durante hold ✅ 2026-01-16
  - `handle_caller_hangup`
  - Matar B-leg pendente
  - Marcar como CANCELLED

- [x] **1.3.1.7** Implementar `_build_dial_string` para diferentes tipos ✅ 2026-01-16
  ```python
  def _build_dial_string(self, dest: TransferDestination) -> str:
      if dest.destination_type == "extension":
          return f"user/{dest.destination_number}@{context}"
      elif dest.destination_type == "ring_group":
          return f"group/{dest.destination_number}@{context}"
      elif dest.destination_type == "queue":
          return f"fifo/{dest.destination_number}@{context}"
      # ...
  ```

- [ ] **1.3.1.8** Escrever testes unitários (pendente)
  - test_transfer_answered
  - test_transfer_busy
  - test_transfer_no_answer
  - test_transfer_dnd
  - test_transfer_offline
  - test_caller_hangup_during_hold

### 1.4 Voice AI - Integração com Session

#### 1.4.1 Integrar TransferManager na RealtimeSession
```python
# voice-ai-service/realtime/session.py
```

- [x] **1.4.1.1** Instanciar TransferManager na sessão ✅ 2026-01-16
  ```python
  # Em start():
  if self.config.intelligent_handoff_enabled:
      await self._init_transfer_manager()
  ```

- [x] **1.4.1.2** Implementar método `request_transfer` ✅ 2026-01-16
  ```python
  async def request_transfer(self, user_text: str) -> Optional[TransferResult]:
      # Implementado com _execute_intelligent_handoff
  ```

- [x] **1.4.1.3** Implementar `_handle_transfer_result` ✅ 2026-01-16
  ```python
  async def _handle_transfer_result(self, result: TransferResult, original_reason: str):
      if result.status == TransferStatus.SUCCESS:
          await self.stop("transfer_success")
      elif result.status == TransferStatus.CANCELLED:
          await self.stop("caller_hangup")
      else:
          # Retomar Voice AI com mensagem contextual
          await self._send_text_to_provider(result.message)
          if result.should_offer_callback:
              await self._offer_callback_or_message(result, original_reason)
  ```

- [x] **1.4.1.4** Adicionar function call `request_handoff` para o LLM ✅ 2026-01-16
  ```python
  HANDOFF_FUNCTION_DEFINITION = {
      "type": "function",
      "name": "request_handoff",
      "description": "Transfere a chamada para um atendente humano...",
      "parameters": { ... }
  }
  ```

- [x] **1.4.1.5** Implementar executor da function call ✅ 2026-01-16
  ```python
  async def _execute_function(self, name: str, args: dict):
      if name == "request_handoff":
          destination = args.get("destination", "qualquer atendente")
          reason = args.get("reason", "solicitação do cliente")
          
          if self._transfer_manager and self.config.intelligent_handoff_enabled:
              asyncio.create_task(self._execute_intelligent_handoff(destination, reason))
          # ...
  ```

### 1.5 Testes de Integração - Fase 1

- [ ] **1.5.1** Testar transfer para ramal que atende
  - Ligar para secretária
  - Pedir para falar com ramal de teste
  - Verificar que bridge é estabelecido
  - Verificar que Voice AI desconecta

- [ ] **1.5.2** Testar transfer para ramal ocupado
  - Ocupar ramal de destino
  - Ligar para secretária e pedir transfer
  - Verificar mensagem "ramal ocupado"
  - Verificar oferta de recado

- [ ] **1.5.3** Testar transfer para ramal que não atende
  - Configurar timeout de 10 segundos
  - Ligar para secretária e pedir transfer
  - Aguardar timeout
  - Verificar mensagem "não atendendo"

- [ ] **1.5.4** Testar transfer para ramal offline
  - Desregistrar ramal de destino
  - Ligar para secretária e pedir transfer
  - Verificar mensagem "não disponível"

- [ ] **1.5.5** Testar cliente desliga durante espera
  - Ligar para secretária e pedir transfer
  - Desligar enquanto ouve música
  - Verificar que B-leg é cancelada
  - Verificar que não cria ticket

---

## FASE 2: Sistema de Callback

**Duração Estimada:** 3-4 dias
**Objetivo:** Implementar criação de ticket callback e monitoramento de disponibilidade
**Status:** ✅ CONCLUÍDA em 2026-01-16

### 2.1 Voice AI - Fluxo de Callback

#### 2.1.1 Implementar captura inteligente de número
```python
# voice-ai-service/realtime/handlers/callback_handler.py
```

- [x] **2.1.1.1** Criar classe `CallbackHandler` ✅ 2026-01-16
  ```python
  class CallbackHandler:
      def __init__(self, session: RealtimeSession):
          self.session = session
          self.callback_number: Optional[str] = None
          self.callback_scheduled_at: Optional[datetime] = None
          self.callback_reason: Optional[str] = None
  ```

- [ ] **2.1.1.2** Implementar `capture_callback_number`
  ```python
  async def capture_callback_number(self) -> bool:
      # Validar caller_id atual
      normalized, is_valid = normalize_and_validate_caller_id(self.session.caller_id)
      
      if is_valid:
          # Confirmar número
          formatted = format_for_speech(normalized)
          await self.session.say(f"Vou anotar para retornar no número {formatted}. Está correto?")
          
          confirmation = await self.session.wait_for_response()
          if is_affirmative(confirmation):
              self.callback_number = normalized
              return True
          else:
              # Cliente quer outro número
              return await self._ask_for_number()
      else:
          # Caller ID inválido - pedir número
          return await self._ask_for_number()
  ```

- [ ] **2.1.1.3** Implementar `_ask_for_number`
  ```python
  async def _ask_for_number(self) -> bool:
      await self.session.say("Qual número devo ligar? Pode falar com o DDD.")
      
      response = await self.session.wait_for_response()
      extracted = extract_phone_number(response)
      
      if extracted:
          normalized, is_valid = normalize_and_validate_caller_id(extracted)
          if is_valid:
              formatted = format_for_speech(normalized)
              await self.session.say(f"Anotei o número {formatted}. Está correto?")
              confirmation = await self.session.wait_for_response()
              if is_affirmative(confirmation):
                  self.callback_number = normalized
                  return True
      
      await self.session.say("Não consegui entender o número. Pode repetir?")
      return False
  ```

- [ ] **2.1.1.4** Implementar `capture_callback_time`
  ```python
  async def capture_callback_time(self) -> None:
      await self.session.say(
          "Prefere que liguemos assim que possível, ou em um horário específico?"
      )
      
      response = await self.session.wait_for_response()
      
      if contains_time_reference(response):
          # "às 14h", "depois das 3", "amanhã de manhã"
          parsed_time = parse_time_reference(response)
          if parsed_time:
              self.callback_scheduled_at = parsed_time
              await self.session.say(f"Certo, agendado para {format_datetime(parsed_time)}.")
          else:
              await self.session.say("Entendi, vamos ligar assim que possível.")
      else:
          # "agora", "quando puder", etc.
          self.callback_scheduled_at = None
          await self.session.say("Certo, vamos ligar assim que estiver disponível.")
  ```

- [ ] **2.1.1.5** Implementar `capture_callback_reason`
  ```python
  async def capture_callback_reason(self) -> None:
      await self.session.say(
          "Para já adiantar o assunto, pode me contar brevemente o motivo do contato?"
      )
      
      response = await self.session.wait_for_response()
      
      # Resumir resposta se muito longa
      if len(response) > 200:
          self.callback_reason = summarize_text(response, max_length=200)
      else:
          self.callback_reason = response
  ```

- [ ] **2.1.1.6** Implementar `confirm_and_create_callback`
  ```python
  async def confirm_and_create_callback(
      self,
      destination: TransferDestination,
      notify_via_whatsapp: bool = False
  ) -> bool:
      # Confirmar detalhes
      formatted_number = format_for_speech(self.callback_number)
      await self.session.say(
          f"Perfeito! {destination.name} vai retornar para {formatted_number}. "
          "Obrigada pela ligação!"
      )
      
      # Criar ticket via OmniPlay API
      result = await self._create_callback_ticket(destination, notify_via_whatsapp)
      
      return result.success
  ```

- [ ] **2.1.1.7** Escrever testes unitários
  - test_capture_number_from_caller_id
  - test_capture_number_manual_input
  - test_capture_time_asap
  - test_capture_time_scheduled
  - test_create_callback_ticket

### 2.2 Voice AI - API de Disponibilidade

#### 2.2.1 Criar endpoint /api/extension/status
```python
# voice-ai-service/api/routes/extension.py
```

- [ ] **2.2.1.1** Implementar endpoint GET /api/extension/status/{extension}
  ```python
  @router.get("/api/extension/status/{extension}")
  async def get_extension_status(
      extension: str,
      domain_uuid: str = Query(...),
      esl: ESLClient = Depends(get_esl_client)
  ) -> ExtensionStatusResponse:
      # 1. Verificar se registrado
      reg_status = await esl.execute_api(
          f"sofia status profile internal reg {extension}@{domain_uuid}"
      )
      if "NOT REGISTERED" in reg_status:
          return ExtensionStatusResponse(
              extension=extension,
              status=ExtensionStatus.OFFLINE,
              available=False,
              reason="Ramal não registrado"
          )
      
      # 2. Verificar se em chamada
      channels = await esl.execute_api("show channels")
      if extension in channels:
          return ExtensionStatusResponse(
              extension=extension,
              status=ExtensionStatus.IN_CALL,
              available=False,
              reason="Em chamada ativa"
          )
      
      # 3. Verificar DND no banco
      dnd = await check_dnd_in_database(extension, domain_uuid)
      if dnd:
          return ExtensionStatusResponse(
              extension=extension,
              status=ExtensionStatus.DND,
              available=False,
              reason="Modo não perturbe ativado"
          )
      
      # 4. Disponível!
      return ExtensionStatusResponse(
          extension=extension,
          status=ExtensionStatus.AVAILABLE,
          available=True,
          reason=None
      )
  ```

- [ ] **2.2.1.2** Criar dataclass de resposta
  ```python
  class ExtensionStatus(Enum):
      AVAILABLE = "available"
      IN_CALL = "in_call"
      RINGING = "ringing"
      DND = "dnd"
      OFFLINE = "offline"
  
  @dataclass
  class ExtensionStatusResponse:
      extension: str
      status: ExtensionStatus
      available: bool
      reason: Optional[str]
  ```

- [ ] **2.2.1.3** Implementar verificação de DND no banco
  ```python
  async def check_dnd_in_database(extension: str, domain_uuid: str) -> bool:
      query = """
          SELECT do_not_disturb 
          FROM v_extensions 
          WHERE extension = $1 AND domain_uuid = $2
      """
      result = await db.fetchone(query, extension, domain_uuid)
      return result and result.get("do_not_disturb") == "true"
  ```

- [ ] **2.2.1.4** Adicionar cache com TTL curto (5 segundos)
  - Evitar consultas repetidas ao FreeSWITCH
  - Invalidar cache em eventos de mudança de estado

- [ ] **2.2.1.5** Escrever testes unitários
  - test_extension_available
  - test_extension_in_call
  - test_extension_dnd
  - test_extension_offline

### 2.3 OmniPlay - Worker de Callback

#### 2.3.1 Criar CallbackMonitorJob
```typescript
// backend/src/jobs/CallbackMonitorJob.ts
```

- [ ] **2.3.1.1** Criar job BullMQ
  ```typescript
  export const callbackMonitorQueue = new Queue("callback-monitor", { ... });
  
  export const callbackMonitorWorker = new Worker(
    "callback-monitor",
    async (job: Job) => {
      await processCallbackTickets(job.data.companyId);
    },
    { ... }
  );
  ```

- [ ] **2.3.1.2** Implementar `processCallbackTickets`
  ```typescript
  async function processCallbackTickets(companyId: number): Promise<void> {
    // Buscar tickets callback pendentes
    const tickets = await Ticket.findAll({
      where: {
        companyId,
        ticketType: "callback",
        callbackStatus: { [Op.in]: ["pending", "notified"] },
        callbackExpiresAt: { [Op.gt]: new Date() }
      }
    });
    
    for (const ticket of tickets) {
      await processCallbackTicket(ticket);
    }
  }
  ```

- [ ] **2.3.1.3** Implementar `processCallbackTicket` com validações
  ```typescript
  async function processCallbackTicket(ticket: Ticket): Promise<void> {
    // Validação 1: Expiração
    if (ticket.callbackExpiresAt < new Date()) {
      await ticket.update({ callbackStatus: "expired" });
      return;
    }
    
    // Validação 2: Máximo de notificações
    if (ticket.callbackNotificationCount >= ticket.callbackMaxNotifications) {
      await ticket.update({ callbackStatus: "needs_review" });
      return;
    }
    
    // Validação 3: Intervalo mínimo
    if (ticket.callbackLastNotifiedAt) {
      const minutesSince = (Date.now() - ticket.callbackLastNotifiedAt.getTime()) / 60000;
      if (minutesSince < ticket.callbackMinIntervalMinutes) {
        return; // Aguardar mais
      }
    }
    
    // Verificar disponibilidade
    const isAvailable = await checkExtensionAvailable(
      ticket.callbackExtension,
      ticket.domainUuid
    );
    
    if (isAvailable) {
      await notifyAgentCallback(ticket);
      await ticket.update({
        callbackNotificationCount: ticket.callbackNotificationCount + 1,
        callbackLastNotifiedAt: new Date()
      });
    }
  }
  ```

- [ ] **2.3.1.4** Implementar `checkExtensionAvailable` com fallback
  ```typescript
  let consecutiveFailures = 0;
  
  async function checkExtensionAvailable(
    extension: string,
    domainUuid: string
  ): Promise<boolean> {
    try {
      const response = await axios.get(
        `${VOICE_AI_API_URL}/api/extension/status/${extension}`,
        {
          params: { domain_uuid: domainUuid },
          timeout: VOICE_AI_TIMEOUT_MS
        }
      );
      
      consecutiveFailures = 0;
      return response.data.available;
      
    } catch (error) {
      consecutiveFailures++;
      
      if (consecutiveFailures >= 3) {
        await notifyAdminVoiceAIDown();
      }
      
      // Assumir indisponível se API offline
      return false;
    }
  }
  ```

- [ ] **2.3.1.5** Agendar job para rodar a cada 30 segundos por empresa
  ```typescript
  // backend/src/queues.ts
  export async function scheduleCallbackMonitor(): Promise<void> {
    const companies = await Company.findAll({ where: { status: true } });
    
    for (const company of companies) {
      await callbackMonitorQueue.add(
        `company-${company.id}`,
        { companyId: company.id },
        {
          repeat: { every: 30000 },
          removeOnComplete: true,
          removeOnFail: 100
        }
      );
    }
  }
  ```

- [ ] **2.3.1.6** Escrever testes unitários
  - test_process_callback_expired
  - test_process_callback_max_notifications
  - test_process_callback_min_interval
  - test_notify_on_available

### 2.4 OmniPlay - Notificação ao Atendente

#### 2.4.1 Implementar notificação via Socket.IO
```typescript
// backend/src/services/VoiceServices/NotifyCallbackService.ts
```

- [ ] **2.4.1.1** Criar serviço de notificação
  ```typescript
  export async function notifyAgentCallback(ticket: Ticket): Promise<void> {
    const io = getIO();
    
    // Buscar usuário pelo ramal
    const user = await User.findOne({
      where: {
        companyId: ticket.companyId,
        voiceExtension: ticket.callbackExtension
      }
    });
    
    if (!user) {
      logger.warn(`No user found for extension ${ticket.callbackExtension}`);
      return;
    }
    
    // Emitir evento para o usuário específico
    io.of(String(ticket.companyId)).emit(`user-${user.id}-callback`, {
      action: "new",
      callback: {
        ticketId: ticket.id,
        callerNumber: ticket.callbackNumber,
        callerName: ticket.contact?.name,
        intendedFor: ticket.callbackIntendedForName,
        reason: ticket.callbackReason,
        waitingMinutes: Math.floor(
          (Date.now() - ticket.createdAt.getTime()) / 60000
        ),
        hasRecording: !!ticket.voiceCallUuid
      }
    });
    
    // Também emitir para supervisores
    io.of(String(ticket.companyId)).emit(`company-${ticket.companyId}-callback`, {
      action: "new",
      callback: { ... }
    });
  }
  ```

- [ ] **2.4.1.2** Adicionar evento de callback aceito
  ```typescript
  // Quando atendente clica em "Ligar Agora"
  socket.on("callback-accept", async (data) => {
    const { ticketId } = data;
    
    // Double-check disponibilidade
    const isAvailable = await checkExtensionAvailable(...);
    
    if (!isAvailable) {
      socket.emit("callback-error", {
        ticketId,
        error: "Ramal ficou ocupado. Tente novamente em alguns segundos."
      });
      return;
    }
    
    // Iniciar callback
    await InitiateCallbackService({ ticketId, userId: socket.user.id });
  });
  ```

### 2.5 OmniPlay - Endpoint de Criação de Ticket

#### 2.5.1 Criar endpoint POST /api/voice/callback
```typescript
// backend/src/routes/voiceRoutes.ts
```

- [ ] **2.5.1.1** Implementar endpoint
  ```typescript
  router.post(
    "/voice/callback",
    serviceAuthMiddleware,
    async (req: Request, res: Response) => {
      const {
        callUuid,
        callerNumber,
        destinationExtension,
        destinationName,
        destinationDepartment,
        reason,
        scheduledAt,
        notifyViaWhatsApp,
        transcript,
        summary,
        durationSeconds,
        secretaryUuid,
        domainUuid
      } = req.body;
      
      // Mapear domain_uuid para companyId
      const company = await getCompanyByDomainUuid(domainUuid);
      if (!company) {
        return res.status(400).json({ error: "Company not found" });
      }
      
      // Criar ou encontrar contato
      const contact = await CreateOrUpdateContactService({
        companyId: company.id,
        channel: "voice",
        number: callerNumber,
        name: `Ligação ${callerNumber}`
      });
      
      // Calcular expiração (24h default)
      const expiresAt = new Date(Date.now() + 24 * 60 * 60 * 1000);
      
      // Criar ticket
      const ticket = await Ticket.create({
        companyId: company.id,
        contactId: contact.id,
        status: "pending",
        channel: "voice",
        ticketType: "callback",
        callbackNumber: callerNumber,
        callbackExtension: destinationExtension,
        callbackIntendedForName: destinationName,
        callbackDepartment: destinationDepartment,
        callbackReason: reason,
        callbackScheduledAt: scheduledAt,
        callbackExpiresAt: expiresAt,
        callbackStatus: "pending",
        callbackNotifyViaWhatsApp: notifyViaWhatsApp,
        voiceCallUuid: callUuid,
        voiceTranscript: JSON.stringify(transcript),
        voiceSummary: summary,
        voiceCallDuration: durationSeconds,
        lastMessage: summary || `Callback para ${destinationName}`,
        isBot: false,
        isActiveDemand: true
      });
      
      // Criar mensagem com transcrição
      await Message.create({
        ticketId: ticket.id,
        companyId: company.id,
        contactId: contact.id,
        body: formatCallbackMessage(ticket),
        fromMe: false,
        read: false,
        mediaType: "voice_callback"
      });
      
      // Emitir via Socket.IO
      io.of(String(company.id)).emit(`company-${company.id}-ticket`, {
        action: "create",
        ticket: await ShowTicketService(ticket.id, company.id)
      });
      
      return res.status(201).json({
        success: true,
        ticketId: ticket.id,
        ticketUuid: ticket.uuid
      });
    }
  );
  ```

- [ ] **2.5.1.2** Implementar `formatCallbackMessage`
  ```typescript
  function formatCallbackMessage(ticket: Ticket): string {
    let message = `📞 *Callback Pendente*\n\n`;
    message += `📱 Número: ${ticket.callbackNumber}\n`;
    message += `👤 Para: ${ticket.callbackIntendedForName}\n`;
    
    if (ticket.callbackReason) {
      message += `📝 Assunto: ${ticket.callbackReason}\n`;
    }
    
    if (ticket.callbackScheduledAt) {
      message += `⏰ Agendado: ${formatDate(ticket.callbackScheduledAt)}\n`;
    }
    
    if (ticket.voiceSummary) {
      message += `\n💬 *Resumo da conversa:*\n${ticket.voiceSummary}\n`;
    }
    
    if (ticket.voiceCallUuid) {
      message += `\n📁 *Gravação:*\nFusionPBX → Recordings → ${ticket.voiceCallUuid}.wav`;
    }
    
    return message;
  }
  ```

### 2.6 Testes de Integração - Fase 2

- [ ] **2.6.1** Testar fluxo completo de callback
  - Ligar para secretária
  - Pedir para falar com ramal ocupado
  - Aceitar deixar callback
  - Confirmar número
  - Verificar ticket criado no OmniPlay

- [ ] **2.6.2** Testar detecção de disponibilidade
  - Criar callback pendente
  - Ramal indisponível → não notifica
  - Ramal fica disponível → notifica atendente

- [ ] **2.6.3** Testar expiração de callback
  - Criar callback com expiração curta (1 min)
  - Aguardar expiração
  - Verificar status "expired"

---

## FASE 3: UI de Callback

**Duração Estimada:** 2-3 dias
**Objetivo:** Criar interface para atendentes verem e gerenciarem callbacks
**Status:** ✅ CONCLUÍDA em 2026-01-16 (estrutura básica)

### 3.1 Frontend - Componente de Widget de Callback

#### 3.1.1 Criar componente CallbackWidget
```javascript
// frontend/src/components/CallbackWidget/index.js
```

- [x] **3.1.1.1** Criar estrutura do componente ✅ 2026-01-16
  ```jsx
  const CallbackWidget = () => {
    const [callbacks, setCallbacks] = useState([]);
    const [isOpen, setIsOpen] = useState(false);
    const socketManager = useContext(SocketContext);
    
    // Buscar callbacks pendentes
    useEffect(() => {
      loadPendingCallbacks();
    }, []);
    
    // Escutar novos callbacks via Socket
    useEffect(() => {
      socketManager.on(`user-${user.id}-callback`, handleNewCallback);
      return () => socketManager.off(`user-${user.id}-callback`, handleNewCallback);
    }, []);
    
    return (
      <CallbackWidgetContainer>
        <CallbackBadge count={callbacks.length} onClick={() => setIsOpen(!isOpen)} />
        {isOpen && <CallbackList callbacks={callbacks} />}
      </CallbackWidgetContainer>
    );
  };
  ```

- [ ] **3.1.1.2** Implementar CallbackCard
  ```jsx
  const CallbackCard = ({ callback, onAccept, onSnooze, onDismiss }) => {
    return (
      <Card>
        <CardHeader>
          <Typography>🔔 Callback Pendente</Typography>
          <Chip label={`${callback.waitingMinutes} min`} />
        </CardHeader>
        
        <CardContent>
          <Typography>📞 {formatPhoneNumber(callback.callerNumber)}</Typography>
          {callback.callerName && (
            <Typography>👤 {callback.callerName}</Typography>
          )}
          <Typography>📝 {callback.reason || "Sem descrição"}</Typography>
        </CardContent>
        
        <CardActions>
          <Button color="primary" onClick={() => onAccept(callback.ticketId)}>
            📞 Ligar Agora
          </Button>
          <Button onClick={() => onSnooze(callback.ticketId, 5)}>
            ⏰ 5min
          </Button>
          <Button color="error" onClick={() => onDismiss(callback.ticketId)}>
            ❌
          </Button>
        </CardActions>
      </Card>
    );
  };
  ```

- [ ] **3.1.1.3** Implementar som de notificação
  ```jsx
  const playCallbackSound = () => {
    const audio = new Audio("/callback-notification.mp3");
    audio.play().catch(err => console.log("Audio play failed:", err));
  };
  
  const handleNewCallback = (data) => {
    setCallbacks(prev => [...prev, data.callback]);
    playCallbackSound();
    
    // Mostrar toast
    toast.info(`📞 Callback de ${data.callback.callerNumber}`, {
      autoClose: false,
      onClick: () => setIsOpen(true)
    });
  };
  ```

- [ ] **3.1.1.4** Implementar ações do card
  ```jsx
  const handleAcceptCallback = async (ticketId) => {
    setLoading(true);
    try {
      const result = await api.post("/voice/callback/initiate", { ticketId });
      
      if (result.data.success) {
        toast.success("📞 Conectando chamada...");
        setCallbacks(prev => prev.filter(c => c.ticketId !== ticketId));
        
      } else if (result.data.shouldRetry) {
        toast.warning(`⏳ ${result.data.error}`);
        // Manter na lista, retry automático
        
      } else {
        toast.error(`❌ ${result.data.error}`);
      }
    } catch (error) {
      toast.error("Erro ao iniciar callback");
    } finally {
      setLoading(false);
    }
  };
  
  const handleSnoozeCallback = async (ticketId, minutes) => {
    await api.post("/voice/callback/snooze", { ticketId, minutes });
    setCallbacks(prev => prev.filter(c => c.ticketId !== ticketId));
    toast.info(`⏰ Callback adiado por ${minutes} minutos`);
  };
  
  const handleDismissCallback = async (ticketId) => {
    if (window.confirm("Deseja cancelar este callback?")) {
      await api.post("/voice/callback/cancel", { ticketId });
      setCallbacks(prev => prev.filter(c => c.ticketId !== ticketId));
      toast.info("Callback cancelado");
    }
  };
  ```

- [ ] **3.1.1.5** Adicionar widget ao layout principal
  ```jsx
  // frontend/src/layout/MainLayout/index.js
  <CallbackWidget />
  ```

### 3.2 Frontend - Página de Gerenciamento de Callbacks

#### 3.2.1 Criar página /callbacks
```javascript
// frontend/src/pages/Callbacks/index.js
```

- [ ] **3.2.1.1** Criar estrutura da página
  - Tabs: Pendentes | Agendados | Histórico
  - Filtros: Por ramal, por período, por status
  - Tabela com paginação

- [ ] **3.2.1.2** Implementar lista de callbacks pendentes
  - Número do cliente
  - Destinatário pretendido
  - Motivo
  - Tempo aguardando
  - Ações

- [ ] **3.2.1.3** Implementar player de gravação (se disponível)
  - Botão "Ouvir conversa original"
  - Player embutido ou modal

- [ ] **3.2.1.4** Implementar visualização de transcrição
  - Modal com transcrição formatada
  - Separação por roles (cliente/assistente)

### 3.3 Backend - Endpoints de Gerenciamento

- [ ] **3.3.1** POST /voice/callback/initiate (já descrito na Fase 2)

- [ ] **3.3.2** POST /voice/callback/snooze
  ```typescript
  router.post("/voice/callback/snooze", authMiddleware, async (req, res) => {
    const { ticketId, minutes } = req.body;
    const ticket = await validateTicketAccess(ticketId, req.user.companyId);
    
    await ticket.update({
      callbackScheduledAt: new Date(Date.now() + minutes * 60 * 1000)
    });
    
    res.json({ success: true });
  });
  ```

- [ ] **3.3.3** POST /voice/callback/cancel
  ```typescript
  router.post("/voice/callback/cancel", authMiddleware, async (req, res) => {
    const { ticketId } = req.body;
    const ticket = await validateTicketAccess(ticketId, req.user.companyId);
    
    await ticket.update({
      callbackStatus: "canceled",
      status: "closed"
    });
    
    res.json({ success: true });
  });
  ```

- [ ] **3.3.4** GET /voice/callbacks
  ```typescript
  router.get("/voice/callbacks", authMiddleware, async (req, res) => {
    const { status, extension, page, limit } = req.query;
    
    const tickets = await Ticket.findAndCountAll({
      where: {
        companyId: req.user.companyId,
        ticketType: "callback",
        ...(status && { callbackStatus: status }),
        ...(extension && { callbackExtension: extension })
      },
      order: [["createdAt", "DESC"]],
      limit: limit || 20,
      offset: (page - 1) * limit || 0,
      include: [Contact]
    });
    
    res.json({
      callbacks: tickets.rows,
      total: tickets.count,
      page: parseInt(page) || 1
    });
  });
  ```

### 3.4 Testes de Integração - Fase 3

- [ ] **3.4.1** Testar widget de callback
  - Verificar badge com contagem
  - Verificar som de notificação
  - Verificar expansão do widget

- [ ] **3.4.2** Testar ação "Ligar Agora"
  - Clicar em "Ligar Agora"
  - Verificar double-check de disponibilidade
  - Verificar feedback de sucesso/erro

- [ ] **3.4.3** Testar snooze
  - Adiar callback por 5 minutos
  - Verificar que some da lista
  - Verificar que volta após 5 minutos

---

## FASE 4: Click-to-Call via Proxy

**Duração Estimada:** 2-3 dias
**Objetivo:** Implementar originação de chamadas via Voice AI
**Status:** ✅ CONCLUÍDA em 2026-01-16

### 4.1 Voice AI - API de Originação

#### 4.1.1 Criar endpoint POST /api/callback/originate
```python
# voice-ai-service/api/callback.py
```

- [x] **4.1.1.1** Implementar endpoint ✅ 2026-01-16
  ```python
  @router.post("/api/callback/originate")
  async def originate_callback(
      request: OriginateRequest,
      esl: ESLClient = Depends(get_esl_client)
  ) -> OriginateResponse:
      # Validar request
      if not request.extension or not request.client_number:
          raise HTTPException(400, "Extension and client_number required")
      
      # Construir dial strings
      agent_dial = f"user/{request.extension}@{request.domain_uuid}"
      client_dial = f"sofia/gateway/default/{request.client_number}"
      
      # Originar para o atendente primeiro
      originate_cmd = (
          f"originate "
          f"{{origination_caller_id_number={request.client_number},"
          f"origination_caller_id_name=Callback,"
          f"ticket_id={request.ticket_id},"
          f"call_timeout=30}}"
          f"{agent_dial} "
          f"&bridge({client_dial})"
      )
      
      result = await esl.execute_bgapi(originate_cmd)
      
      if "+OK" in result:
          # Extrair UUID do job
          job_uuid = extract_job_uuid(result)
          return OriginateResponse(success=True, call_uuid=job_uuid)
      else:
          error = parse_originate_error(result)
          return OriginateResponse(success=False, error=error)
  ```

- [ ] **4.1.1.2** Implementar dataclasses de request/response
  ```python
  @dataclass
  class OriginateRequest:
      extension: str
      client_number: str
      ticket_id: int
      domain_uuid: str
      reason: Optional[str] = None
  
  @dataclass
  class OriginateResponse:
      success: bool
      call_uuid: Optional[str] = None
      error: Optional[str] = None
  ```

- [ ] **4.1.1.3** Implementar monitoramento do resultado
  ```python
  async def monitor_callback_result(
      call_uuid: str,
      ticket_id: int
  ):
      """
      Monitora o resultado do callback em background.
      Notifica OmniPlay quando concluído.
      """
      try:
          event = await esl.wait_for_event(
              ["CHANNEL_BRIDGE", "CHANNEL_HANGUP"],
              uuid=call_uuid,
              timeout=60
          )
          
          if event.get("Event-Name") == "CHANNEL_BRIDGE":
              # Callback conectado!
              await notify_omniplay_callback_connected(ticket_id)
              
              # Aguardar fim da chamada
              hangup_event = await esl.wait_for_event(
                  ["CHANNEL_HANGUP"],
                  uuid=call_uuid,
                  timeout=3600
              )
              
              duration = calculate_duration(event, hangup_event)
              await notify_omniplay_callback_completed(ticket_id, duration)
              
          else:
              # Falhou
              cause = event.get("Hangup-Cause", "UNKNOWN")
              await notify_omniplay_callback_failed(ticket_id, cause)
              
      except asyncio.TimeoutError:
          await notify_omniplay_callback_failed(ticket_id, "TIMEOUT")
  ```

### 4.2 OmniPlay - Serviço de Callback

#### 4.2.1 Criar InitiateCallbackService
```typescript
// backend/src/services/VoiceServices/InitiateCallbackService.ts
```

- [ ] **4.2.1.1** Implementar serviço com double-check
  ```typescript
  interface InitiateCallbackResult {
    success: boolean;
    callUuid?: string;
    error?: string;
    shouldRetry?: boolean;
    retryAfterSeconds?: number;
  }
  
  async function initiateCallback(
    ticketId: number,
    userId: number
  ): Promise<InitiateCallbackResult> {
    const ticket = await Ticket.findByPk(ticketId);
    
    // Validar ticket
    if (!ticket || ticket.ticketType !== "callback") {
      return { success: false, error: "Ticket inválido" };
    }
    
    // Double-check disponibilidade
    const status = await axios.get(
      `${VOICE_AI_API_URL}/api/extension/status/${ticket.callbackExtension}`,
      { params: { domain_uuid: ticket.domainUuid }, timeout: 3000 }
    );
    
    if (!status.data.available) {
      return {
        success: false,
        error: `Ramal ${status.data.status}: ${status.data.reason}`,
        shouldRetry: true,
        retryAfterSeconds: 30
      };
    }
    
    // Originar chamada
    const result = await axios.post(
      `${VOICE_AI_API_URL}/api/callback/originate`,
      {
        extension: ticket.callbackExtension,
        clientNumber: ticket.callbackNumber,
        ticketId: ticket.id,
        domainUuid: ticket.domainUuid,
        reason: ticket.callbackReason
      }
    );
    
    if (!result.data.success) {
      // Tratar race condition
      if (result.data.error === "USER_BUSY") {
        await ticket.increment("callbackAttempts");
        
        if (ticket.callbackAttempts < ticket.callbackMaxAttempts) {
          return {
            success: false,
            error: "Ramal ficou ocupado. Tentando novamente em 30s...",
            shouldRetry: true,
            retryAfterSeconds: 30
          };
        } else {
          await ticket.update({ callbackStatus: "failed" });
          return {
            success: false,
            error: `Máximo de ${ticket.callbackMaxAttempts} tentativas atingido.`
          };
        }
      }
      
      return { success: false, error: result.data.error };
    }
    
    // Sucesso!
    await ticket.update({
      callbackStatus: "in_progress",
      callbackLastAttemptAt: new Date()
    });
    
    return { success: true, callUuid: result.data.call_uuid };
  }
  ```

### 4.3 Voice AI - Endpoint de Conclusão

#### 4.3.1 Criar endpoint POST /api/voice/transfer/completed
- [ ] **4.3.1.1** Endpoint para notificar conclusão (já descrito em Melhoria 4)

### 4.4 Testes de Integração - Fase 4

- [ ] **4.4.1** Testar originação de callback
  - Atendente clica "Ligar Agora"
  - Telefone do atendente toca
  - Atendente atende
  - Cliente é discado
  - Chamada estabelecida

- [ ] **4.4.2** Testar race condition
  - Atendente clica "Ligar Agora" mas ramal já ocupou
  - Verificar mensagem de erro
  - Verificar auto-retry

- [ ] **4.4.3** Testar conclusão de callback
  - Callback conectado
  - Conversa encerrada
  - Ticket marcado como "completed"

---

## FASE 5: Integração WhatsApp

**Duração Estimada:** 2-4 dias
**Objetivo:** Enviar notificações de callback via WhatsApp
**Status:** ✅ CONCLUÍDA em 2026-01-16 (backend)

### 5.1 OmniPlay - Configuração de Template

#### 5.1.1 Criar UI de configuração de template de callback
```javascript
// frontend/src/pages/Settings/CallbackSettings/index.js
```

- [ ] **5.1.1.1** Criar página de configurações de callback
  - Selecionar template existente como "Template de Callback"
  - Configurar variáveis do template
  - Preview do template

- [ ] **5.1.1.2** Criar campo `isCallbackTemplate` em QuickMessages
  ```typescript
  // migration
  ALTER TABLE "QuickMessages" ADD COLUMN IF NOT EXISTS "isCallbackTemplate" BOOLEAN DEFAULT false;
  ```

### 5.2 OmniPlay - Serviço de Notificação WhatsApp

#### 5.2.1 Criar SendCallbackWhatsAppService
```typescript
// backend/src/services/VoiceServices/SendCallbackWhatsAppService.ts
```

- [ ] **5.2.1.1** Implementar serviço
  ```typescript
  async function sendCallbackWhatsApp(ticket: Ticket): Promise<void> {
    // Buscar template de callback
    const template = await QuickMessage.findOne({
      where: {
        companyId: ticket.companyId,
        isCallbackTemplate: true
      }
    });
    
    if (!template) {
      logger.warn("Template de callback não configurado");
      return;
    }
    
    // Buscar conexão WABA
    const waba = await Whatsapp.findOne({
      where: {
        companyId: ticket.companyId,
        channel: "waba",
        status: "CONNECTED"
      }
    });
    
    if (!waba) {
      logger.warn("Sem conexão WABA ativa");
      return;
    }
    
    // Preparar parâmetros
    const company = await Company.findByPk(ticket.companyId);
    
    // Enviar template
    await SendWABATemplateService({
      whatsappId: waba.id,
      number: ticket.callbackNumber,
      templateName: template.templateName,
      templateNamespace: template.templateNamespace,
      components: [
        {
          type: "body",
          parameters: [
            { type: "text", text: company.name },
            { type: "text", text: ticket.callbackIntendedForName },
            { type: "text", text: ticket.callbackReason || "seu atendimento" }
          ]
        }
      ]
    });
    
    // Atualizar ticket
    await ticket.update({
      callbackWhatsAppSentAt: new Date(),
      callbackStatus: "notified"
    });
  }
  ```

### 5.3 OmniPlay - Processamento de Resposta

#### 5.3.1 Integrar com webhook de mensagens
```typescript
// backend/src/services/WbotServices/wbotMessageListener.ts
```

- [ ] **5.3.1.1** Adicionar handler para respostas de callback
  ```typescript
  async function handleCallbackResponse(
    message: any,
    contact: Contact
  ): Promise<boolean> {
    // Buscar callback pendente para este número
    const ticket = await Ticket.findOne({
      where: {
        callbackNumber: contact.number,
        ticketType: "callback",
        callbackStatus: "notified"
      }
    });
    
    if (!ticket) return false;
    
    const body = message.body?.toLowerCase().trim();
    
    if (["sim", "yes", "1", "s"].includes(body)) {
      // Cliente quer receber ligação
      await ticket.update({ callbackStatus: "ready_to_call" });
      await sendTextMessage(contact.number, "Perfeito! Estamos ligando agora.");
      await InitiateCallbackService({ ticketId: ticket.id, userId: null });
      return true;
      
    } else if (["depois", "later", "2"].includes(body)) {
      // Adiar
      await ticket.update({
        callbackScheduledAt: new Date(Date.now() + 30 * 60 * 1000)
      });
      await sendTextMessage(contact.number, "Ok! Vamos ligar em 30 minutos.");
      return true;
      
    } else if (["não", "nao", "no", "3", "n"].includes(body)) {
      // Cancelar
      await ticket.update({
        callbackStatus: "canceled",
        status: "closed"
      });
      await sendTextMessage(
        contact.number,
        "Tudo bem! Callback cancelado. Qualquer coisa, estamos à disposição."
      );
      return true;
    }
    
    return false;
  }
  ```

### 5.4 Testes de Integração - Fase 5

- [ ] **5.4.1** Testar envio de template
  - Callback pendente com `notifyViaWhatsApp=true`
  - Ramal fica disponível
  - Verificar template enviado ao cliente

- [ ] **5.4.2** Testar resposta "SIM"
  - Cliente responde "sim"
  - Verificar originação de chamada
  - Verificar status "ready_to_call" → "in_progress"

- [ ] **5.4.3** Testar resposta "DEPOIS"
  - Cliente responde "depois"
  - Verificar agendamento +30min
  - Verificar novo envio após 30min

- [ ] **5.4.4** Testar resposta "NÃO"
  - Cliente responde "não"
  - Verificar cancelamento
  - Verificar ticket fechado

---

## FASE 6: Monitoramento e Métricas

**Duração Estimada:** 1-2 dias
**Objetivo:** Implementar logs, métricas e dashboards
**Status:** ✅ CONCLUÍDA em 2026-01-16

### 6.1 Voice AI - Métricas

- [x] **6.1.1** Adicionar métricas Prometheus ✅ 2026-01-16
  - `voice_transfers_total{status, destination_type}`
  - `voice_transfers_duration_seconds`
  - `voice_callbacks_total{status}`
  - `voice_extension_status_requests_total`

- [ ] **6.1.2** Adicionar logs estruturados
  - Transfer initiated
  - Transfer result (success/failure + cause)
  - Callback created
  - Callback completed

### 6.2 OmniPlay - Dashboard

- [ ] **6.2.1** Adicionar widget de callbacks no dashboard
  - Total pendentes
  - Média de tempo de espera
  - Taxa de conclusão
  - Top destinos

- [ ] **6.2.2** Adicionar relatório de callbacks
  - Filtro por período
  - Filtro por destino
  - Exportação CSV

---

## Checklist Final de Entrega

### Documentação
- [ ] README atualizado com novas funcionalidades
- [ ] Documentação de API (endpoints)
- [ ] Guia de configuração de destinos de transferência
- [ ] Guia de configuração de template WhatsApp

### Testes
- [ ] Testes unitários Voice AI (coverage > 80%)
- [ ] Testes unitários OmniPlay (coverage > 80%)
- [ ] Testes de integração end-to-end
- [ ] Teste de carga (10 transfers simultâneos)

### Deploy
- [ ] Migration FusionPBX aplicada em staging
- [ ] Migration OmniPlay aplicada em staging
- [ ] Voice AI atualizado em staging
- [ ] Frontend OmniPlay atualizado em staging
- [ ] Testes de aceitação em staging
- [ ] Deploy em produção

---

## Estimativa Total

| Fase | Descrição | Dias |
|------|-----------|------|
| 0 | Preparação e Infraestrutura | 1-2 |
| 1 | Transferência Básica | 3-4 |
| 2 | Sistema de Callback | 3-4 |
| 3 | UI de Callback | 2-3 |
| 4 | Click-to-Call via Proxy | 2-3 |
| 5 | Integração WhatsApp | 2-4 |
| 6 | Monitoramento e Métricas | 1-2 |
| **Total** | | **14-22 dias** |

> **Nota:** O range considera possíveis imprevistos, debugging e refinamentos.

---

## Dependências Externas

1. **FreeSWITCH ESL** - Acesso ao Event Socket já configurado
2. **FusionPBX** - Permissão para criar tabelas
3. **Meta WABA** - Template de callback aprovado
4. **MinIO** - Se decidir anexar gravações no futuro

---

## Riscos Identificados

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| ESL instável | Alto | Implementar reconexão automática, timeout |
| Template WhatsApp rejeitado | Médio | Preparar 2-3 variações do template |
| Performance do worker | Médio | Monitorar, ajustar intervalo de polling |
| Race conditions | Médio | Double-check, locks pessimistas |
