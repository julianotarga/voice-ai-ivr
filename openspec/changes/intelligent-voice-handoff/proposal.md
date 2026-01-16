# Proposal: Sistema de Handoff Inteligente de Voz

## Metadata
- **Author:** Claude AI + Juliano Targa
- **Created:** 2026-01-16
- **Status:** PROPOSED
- **Priority:** HIGH
- **Estimated Effort:** 5-7 dias

## Resumo Executivo

Implementar um sistema de transferência de chamadas inteligente onde o agente IA atua como uma **secretária eletrônica real**:

1. **Tenta transferir** a chamada para o destino solicitado
2. **Monitora o resultado** (atendeu, ocupado, não atendeu)
3. **Retorna ao cliente** informando o status
4. **Cria ticket/recado** apenas quando não há atendimento disponível

## Problema Atual

Atualmente, quando o cliente pede para falar com um atendente:
- ❌ O agente cria um ticket imediatamente
- ❌ Não tenta transferir a chamada
- ❌ O ticket fica vazio (sem áudio, sem contexto útil)
- ❌ O cliente é abandonado sem resolução

## Solução Proposta

### Fluxo Principal

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    FLUXO DE SECRETÁRIA INTELIGENTE                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐                                                        │
│  │ Cliente Liga│                                                        │
│  └──────┬──────┘                                                        │
│         ▼                                                               │
│  ┌─────────────────────────┐                                            │
│  │ Agente IA Atende        │◄──────────────────────────────┐            │
│  │ (Gravação inicia)       │                               │            │
│  └──────┬──────────────────┘                               │            │
│         ▼                                                  │            │
│  ┌─────────────────────────┐                               │            │
│  │ Conversa Normal         │                               │            │
│  └──────┬──────────────────┘                               │            │
│         ▼                                                  │            │
│  ┌─────────────────────────┐    NÃO                        │            │
│  │ Cliente quer falar      ├─────────► Continua conversa   │            │
│  │ com atendente?          │                               │            │
│  └──────┬──────────────────┘                               │            │
│         │ SIM                                              │            │
│         ▼                                                  │            │
│  ┌─────────────────────────┐                               │            │
│  │ Identificar Destino     │                               │            │
│  │ - "alguém" → Fila       │                               │            │
│  │ - "Jeni" → Ramal 1004   │                               │            │
│  │ - "financeiro" → 1004   │                               │            │
│  └──────┬──────────────────┘                               │            │
│         ▼                                                  │            │
│  ┌─────────────────────────┐                               │            │
│  │ "Um momento, vou        │                               │            │
│  │  transferir..."         │                               │            │
│  └──────┬──────────────────┘                               │            │
│         ▼                                                  │            │
│  ┌─────────────────────────┐                               │            │
│  │ FreeSWITCH toca o ramal │                               │            │
│  │ (Attended Transfer)     │                               │            │
│  └──────┬──────────────────┘                               │            │
│         │                                                  │            │
│    ┌────┴────┬─────────────┐                               │            │
│    ▼         ▼             ▼                               │            │
│ ATENDEU   OCUPADO      TIMEOUT                             │            │
│    │         │             │                               │            │
│    ▼         ▼             ▼                               │            │
│ Bridge    "Ramal        "Não está                          │            │
│ Completo  ocupado"      disponível"                        │            │
│    │         │             │                               │            │
│    ▼         └─────┬───────┘                               │            │
│ Agente            ▼                                        │            │
│ desconecta   ┌─────────────────────────┐                   │            │
│              │ "Quer deixar recado?"   │                   │            │
│              └──────┬──────────────────┘                   │            │
│                     │                                      │            │
│               ┌─────┴─────┐                                │            │
│               ▼           ▼                                │            │
│              SIM         NÃO                               │            │
│               │           │                                │            │
│               ▼           └────────────────────────────────┘            │
│        ┌─────────────────────────┐                                      │
│        │ Criar Ticket/Recado     │                                      │
│        │ - Áudio da conversa     │                                      │
│        │ - Transcrição           │                                      │
│        │ - Resumo                │                                      │
│        │ - Destino pretendido    │                                      │
│        └─────────────────────────┘                                      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Componentes Necessários

#### 1. Tabela de Destinos de Transferência (FusionPBX)
```sql
CREATE TABLE v_voice_transfer_destinations (
    transfer_destination_uuid UUID PRIMARY KEY,
    domain_uuid UUID NOT NULL,
    secretary_uuid UUID REFERENCES v_voice_secretaries,
    
    -- Identificação por voz/texto
    name VARCHAR(100) NOT NULL,           -- "Jeni", "financeiro", "suporte"
    aliases TEXT[],                        -- ["jeni", "jeniffer", "financeiro"]
    
    -- Destino FreeSWITCH
    destination_type VARCHAR(20),          -- extension, queue, ring_group, external
    destination_number VARCHAR(50),        -- 1004, 5001, 9000
    destination_context VARCHAR(50),       -- default, public
    
    -- Configurações
    ring_timeout_seconds INT DEFAULT 30,
    fallback_action VARCHAR(20),           -- voicemail, ticket, retry, hangup
    
    -- Metadados
    department VARCHAR(100),               -- "Financeiro", "Suporte"
    description TEXT,
    
    is_enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### 2. Lógica de Transferência (FreeSWITCH + ESL)
- Attended transfer com monitoramento
- Callback para Voice AI com resultado
- Retorno ao agente se falhar

#### 3. Gravação de Chamada
- Gravar desde o início
- Upload para MinIO após handoff
- Anexar ao ticket

#### 4. Interface FusionPBX
- CRUD de destinos de transferência
- Associação com secretárias
- Configuração de timeouts e fallbacks

## Escopo

### Incluído
- [x] Tabela de destinos de transferência
- [x] Lógica de attended transfer via ESL
- [x] Detecção de resultado (atendeu/ocupado/timeout)
- [x] Retorno ao agente IA com status
- [x] Mensagens contextuais ao cliente
- [x] Criação de ticket/recado com áudio
- [x] Interface FusionPBX para gerenciamento
- [x] Gravação de chamada completa

### Excluído (futuro)
- [ ] Integração com sistema de presença BLF
- [ ] Fila de callback (retornar ligação)
- [ ] Transcrição em tempo real durante transfer
- [ ] Dashboard de métricas de transferência

## Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|---------------|---------|-----------|
| FreeSWITCH não suportar attended transfer via ESL | Baixa | Alto | Usar bridge com monitoramento de eventos |
| Latência na detecção de ocupado | Média | Médio | Usar SIP response codes diretamente |
| Gravação não iniciar antes do handoff | Média | Alto | Iniciar gravação no atendimento da chamada |

## Dependências

- FreeSWITCH com mod_commands e mod_dptools
- ESL (Event Socket Library) configurado
- MinIO para armazenamento de gravações
- OmniPlay backend com VoiceHandoffService

## Métricas de Sucesso

1. **Taxa de transferência bem-sucedida** > 70%
2. **Tempo médio de espera** < 30 segundos
3. **Taxa de tickets/recados** < 30% das solicitações de handoff
4. **Satisfação do cliente** (qualitativo)

## Próximos Passos

1. ✅ Aprovar este proposal
2. 📝 Criar design.md com detalhes técnicos
3. 📋 Criar tasks.md com tarefas de implementação
4. 🚀 Implementar em fases
