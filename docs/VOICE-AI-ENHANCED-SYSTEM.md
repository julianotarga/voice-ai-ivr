# Voice AI Enhanced System - Documentação Completa

> Documentação do novo sistema Voice AI com melhorias inspiradas em projetos open-source (AVA, sip-to-ai).

## Índice

1. [Visão Geral](#visão-geral)
2. [Arquitetura](#arquitetura)
3. [Fase 0: G.711 Nativo](#fase-0-g711-nativo)
4. [Fase 1: Tool Registry](#fase-1-tool-registry)
5. [Fase 2: RCA (Root Cause Analysis)](#fase-2-rca-root-cause-analysis)
6. [Fase 3: Templates](#fase-3-templates)
7. [Fase 4: Métricas e Dashboard](#fase-4-métricas-e-dashboard)
8. [Fluxo de uma Chamada](#fluxo-de-uma-chamada)
9. [API Reference](#api-reference)
10. [Configuração](#configuração)
11. [Troubleshooting](#troubleshooting)

---

## Visão Geral

O Voice AI Enhanced System é uma evolução do sistema de secretária virtual que adiciona:

| Funcionalidade | Descrição | Benefício |
|----------------|-----------|-----------|
| **G.711 Nativo** | Áudio sem resampling | Menor latência, melhor qualidade |
| **Tool Registry** | Sistema plugável de funções | Fácil extensão e manutenção |
| **RCA** | Logging estruturado por chamada | Debugging em 5-10 min |
| **Templates** | Configurações pré-definidas | Onboarding em 30 min |
| **Dashboard** | Métricas em tempo real | Visibilidade operacional |

### Tecnologias

```
┌─────────────────────────────────────────────────────────────┐
│                     VOICE AI SYSTEM                         │
├─────────────────────────────────────────────────────────────┤
│  FreeSWITCH          │  Python Service      │  Backend      │
│  ├─ mod_audio_stream │  ├─ RealtimeSession  │  ├─ Node.js   │
│  ├─ mod_conference   │  ├─ ToolRegistry     │  ├─ Sequelize │
│  └─ ESL              │  ├─ CallLogger       │  └─ PostgreSQL│
│                      │  └─ OpenAI Realtime  │               │
└─────────────────────────────────────────────────────────────┘
```

---

## Arquitetura

### Componentes Principais

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  FreeSWITCH  │────▶│ Python Voice │────▶│  OpenAI      │
│  (SIP/RTP)   │◀────│    AI        │◀────│  Realtime    │
└──────────────┘     └──────────────┘     └──────────────┘
       │                    │
       │                    ▼
       │             ┌──────────────┐
       │             │ CallLogger   │
       │             └──────┬───────┘
       │                    │
       ▼                    ▼
┌──────────────┐     ┌──────────────┐
│   Cliente    │     │   Backend    │
│   (Telefone) │     │   OmniPlay   │
└──────────────┘     └──────────────┘
```

### Fluxo de Dados

1. **Entrada de Áudio**: Cliente → FreeSWITCH → Python (G.711 8kHz)
2. **Processamento IA**: Python → OpenAI Realtime → Python
3. **Saída de Áudio**: Python → FreeSWITCH → Cliente (G.711 8kHz)
4. **Logs/Métricas**: Python → Backend (webhook)

---

## Fase 0: G.711 Nativo

### Problema Anterior

```
Cliente ──G.711──▶ FreeSWITCH ──PCM16──▶ Python ──resample──▶ OpenAI
                                                   24kHz
```

Cada resampling adicionava ~50ms de latência e artifacts de áudio.

### Solução

```
Cliente ──G.711──▶ FreeSWITCH ──G.711──▶ Python ──G.711──▶ OpenAI
          8kHz                   8kHz             8kHz
```

### Configuração OpenAI

```python
# GA Models
audio_config = {
    "input": {"format": {"type": "audio/pcmu"}},   # G.711 μ-law
    "output": {"format": {"type": "audio/pcmu"}}   # G.711 μ-law
}

# Preview Models
session_config = {
    "input_audio_format": "g711_ulaw",
    "output_audio_format": "g711_ulaw"
}
```

### Benefícios

- **Latência**: Reduzida em ~100ms
- **Qualidade**: Sem artifacts de resampling
- **CPU**: Menor uso (sem conversões)

---

## Fase 1: Tool Registry

### Estrutura de Arquivos

```
voice-ai-service/realtime/tools/
├── __init__.py          # Exports
├── base.py              # VoiceAITool, ToolResult, ToolContext
├── registry.py          # ToolRegistry singleton
├── transfer.py          # RequestHandoffTool
├── message.py           # TakeMessageTool
├── decision.py          # AcceptTransferTool, RejectTransferTool
├── call_control.py      # EndCallTool, GetBusinessInfoTool
└── integration.py       # Compatibilidade com session.py
```

### Criando um Novo Tool

```python
from realtime.tools import VoiceAITool, ToolCategory, ToolContext, ToolResult

class MeuNovoTool(VoiceAITool):
    name = "meu_tool"
    description = "Descrição do que o tool faz"
    category = ToolCategory.CUSTOM
    
    parameters = {
        "type": "object",
        "properties": {
            "parametro1": {
                "type": "string",
                "description": "Descrição do parâmetro"
            }
        },
        "required": ["parametro1"]
    }
    
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        param1 = kwargs["parametro1"]
        
        # Sua lógica aqui
        resultado = fazer_algo(param1)
        
        return ToolResult.ok(
            data={"resultado": resultado},
            instruction="Diga ao cliente: Operação concluída!"
        )

# Registrar
from realtime.tools import ToolRegistry
ToolRegistry.register(MeuNovoTool())
```

### Tools Disponíveis

| Tool | Categoria | Descrição |
|------|-----------|-----------|
| `request_handoff` | transfer | Transferir para atendente |
| `take_message` | message | Anotar recado |
| `accept_transfer` | decision | Atendente aceita chamada |
| `reject_transfer` | decision | Atendente recusa chamada |
| `end_call` | call_control | Encerrar chamada |
| `get_business_info` | info | Informações da empresa |

---

## Fase 2: RCA (Root Cause Analysis)

### Como Funciona

```
┌─────────────────────────────────────────────────────────────┐
│                     DURANTE A CHAMADA                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   CallLogger.log_event(SESSION_START)                       │
│   CallLogger.log_metric("latency_ms", 150)                  │
│   CallLogger.log_tool("take_message", {...}, {...})         │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                     FIM DA CHAMADA                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   CallLogger.flush() ──webhook──▶ Backend ──▶ PostgreSQL    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Eventos Disponíveis

```python
class EventType(Enum):
    # Sessão
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_ERROR = "session_error"
    
    # OpenAI
    OPENAI_CONNECTED = "openai_connected"
    OPENAI_DISCONNECTED = "openai_disconnected"
    OPENAI_ERROR = "openai_error"
    
    # Áudio
    AUDIO_FIRST_INPUT = "audio_first_input"
    AUDIO_FIRST_OUTPUT = "audio_first_output"
    
    # Transferência
    TRANSFER_INITIATED = "transfer_initiated"
    TRANSFER_ANSWERED = "transfer_answered"
    TRANSFER_REJECTED = "transfer_rejected"
    TRANSFER_NO_ANSWER = "transfer_no_answer"
    
    # Recado
    MESSAGE_TAKEN = "message_taken"
```

### Estrutura do Log (JSON)

```json
{
  "call_uuid": "abc-123",
  "company_id": 42,
  "secretary_id": "sec-456",
  "caller_id": "+5511999999999",
  "caller_name": "João",
  "started_at": "2026-01-19T10:00:00Z",
  "ended_at": "2026-01-19T10:02:30Z",
  "duration_ms": 150000,
  "final_state": "ended",
  "outcome": "message_taken",
  "events": [
    {"type": "session_start", "timestamp": "...", "data": {}},
    {"type": "openai_connected", "timestamp": "...", "data": {}},
    {"type": "tool_called", "timestamp": "...", "data": {"tool": "take_message"}},
    {"type": "session_end", "timestamp": "...", "data": {}}
  ],
  "metrics": {
    "ai_response_time_ms": 150,
    "duration_seconds": 150
  },
  "tools_executed": [
    {
      "name": "take_message",
      "input": {"caller_name": "João", "message": "..."},
      "output": {"status": "success"},
      "duration_ms": 50,
      "success": true
    }
  ]
}
```

### API de RCA

```bash
# Detalhes de uma chamada
GET /api/voice-ai/calls/{call_uuid}

# Resposta inclui:
# - Timeline de eventos
# - Métricas
# - Tools executados
# - Erros (se houver)
```

---

## Fase 3: Templates

### Templates Disponíveis

#### 1. Recepção Simples

```yaml
Categoria: recepcao
VAD: semantic_vad, eagerness=medium

Funções:
  - Atender ligações
  - Identificar cliente (nome)
  - Identificar motivo
  - Transferir para setor correto
  - Anotar recados

Tools: request_handoff, take_message, end_call

Destinos de Transferência:
  - Suporte (keywords: problema, ajuda)
  - Comercial (keywords: comprar, preço)
  - Financeiro (keywords: boleto, pagamento)
```

#### 2. Suporte Técnico

```yaml
Categoria: suporte
VAD: semantic_vad, eagerness=high (respostas rápidas)

Funções:
  - Triagem técnica
  - Classificar urgência
  - Perguntas de diagnóstico
  - Encaminhar para técnico

Tools: request_handoff, take_message, end_call

Destinos de Transferência:
  - Suporte N1 (simples)
  - Suporte N2 (complexo)
  - Urgência (serviço parado)
```

#### 3. SAC / FAQ

```yaml
Categoria: sac
VAD: semantic_vad, eagerness=low (aguarda cliente terminar)

Funções:
  - Responder dúvidas frequentes
  - Informar horários e contatos
  - Encaminhar reclamações
  - Anotar sugestões

Tools: request_handoff, take_message, end_call, get_business_info

Destinos de Transferência:
  - Atendimento
  - Financeiro
  - Ouvidoria
```

### API de Templates

```bash
# Listar templates
GET /api/voice-ai/templates

# Categorias
GET /api/voice-ai/templates/categories

# Detalhes de um template
GET /api/voice-ai/templates/{id}

# Preview antes de aplicar
GET /api/voice-ai/templates/{id}/preview
```

---

## Fase 4: Métricas e Dashboard

### Métricas Coletadas

| Métrica | Descrição | Agregação |
|---------|-----------|-----------|
| `totalCalls` | Total de chamadas | Soma |
| `completedCalls` | Chamadas finalizadas com sucesso | Soma |
| `transferredCalls` | Chamadas transferidas | Soma |
| `messagesTaken` | Recados anotados | Soma |
| `errorCalls` | Chamadas com erro | Soma |
| `avgDurationSeconds` | Duração média | Média |
| `avgAiResponseTimeMs` | Tempo de resposta IA | Média |
| `transferSuccessRate` | Taxa de sucesso em transferências | % |
| `errorRate` | Taxa de erros | % |
| `topTransferReasons` | Top 5 motivos de transferência | Array |

### API de Métricas

```bash
# Métricas por período
GET /api/voice-ai/metrics?startDate=2026-01-01&endDate=2026-01-19

# Resumo do dia (para cards)
GET /api/voice-ai/metrics/summary

# Lista de chamadas
GET /api/voice-ai/calls?page=1&limit=20&outcome=transferred

# Detalhes de uma chamada (RCA)
GET /api/voice-ai/calls/{uuid}
```

### Exemplo de Resposta - Summary

```json
{
  "today": {
    "totalCalls": 45,
    "transferredCalls": 30,
    "messagesTaken": 8,
    "avgDurationSeconds": 120,
    "errorRate": "2.2"
  },
  "variations": {
    "totalCalls": "+15.3",
    "transferredCalls": "+10.0",
    "messagesTaken": "-5.0"
  },
  "topTransferReasons": [
    {"reason": "Suporte", "count": 15},
    {"reason": "Comercial", "count": 10},
    {"reason": "Financeiro", "count": 5}
  ]
}
```

---

## Fluxo de uma Chamada

```
┌─────────────────────────────────────────────────────────────┐
│  1. CLIENTE LIGA                                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  FreeSWITCH recebe chamada SIP                             │
│  ↓                                                         │
│  Dialplan identifica secretária virtual                    │
│  ↓                                                         │
│  uuid_audio_stream inicia WebSocket para Python            │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  2. SESSÃO INICIA                                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  RealtimeSession.__init__()                                │
│  ├─ Inicializa CallLogger                                  │
│  ├─ Inicializa ToolRegistry                                │
│  └─ Conecta ao OpenAI Realtime                             │
│                                                             │
│  CallLogger.log_event(SESSION_START)                       │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  3. CONVERSA                                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Loop:                                                     │
│  ├─ Áudio cliente → OpenAI (G.711 8kHz)                    │
│  ├─ OpenAI → Resposta IA                                   │
│  ├─ Resposta IA → Cliente                                  │
│  │                                                         │
│  └─ Se function_call:                                      │
│      ├─ ToolRegistry.execute(tool_name, context, args)     │
│      ├─ CallLogger.log_tool(...)                           │
│      └─ Retorna resultado para OpenAI                      │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  4. AÇÃO FINAL (exemplo: transferência)                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  OpenAI chama request_handoff(destination="suporte")       │
│  ↓                                                         │
│  TransferManager.execute_announced_transfer()              │
│  ├─ Origina B-leg para ramal                              │
│  ├─ Anuncia chamada ao atendente                          │
│  ├─ Aguarda accept_transfer ou reject_transfer             │
│  └─ Faz bridge A↔B ou oferece recado                       │
│                                                             │
│  CallLogger.log_event(TRANSFER_COMPLETED)                  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  5. FIM                                                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  RealtimeSession.stop(reason)                              │
│  ├─ CallLogger.set_final_state()                           │
│  ├─ CallLogger.set_outcome()                               │
│  └─ CallLogger.flush() → Backend                           │
│                                                             │
│  Backend salva em voice_ai_call_logs                       │
│  Métricas agregadas em voice_ai_daily_metrics              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## API Reference

### Webhooks (Sem Autenticação)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| POST | `/api/voice-ai/webhook` | Webhook principal |
| POST | `/api/voice-ai/webhook/message` | Recados |
| POST | `/api/voice-ai/webhook/logs` | Logs RCA |
| GET | `/api/voice-ai/webhook/health` | Health check |

### Templates (Autenticado)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/voice-ai/templates` | Lista templates |
| GET | `/api/voice-ai/templates/categories` | Categorias |
| GET | `/api/voice-ai/templates/:id` | Detalhes |
| GET | `/api/voice-ai/templates/:id/preview` | Preview |

### Métricas (Autenticado)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/voice-ai/metrics` | Métricas por período |
| GET | `/api/voice-ai/metrics/summary` | Resumo do dia |
| GET | `/api/voice-ai/calls` | Lista chamadas |
| GET | `/api/voice-ai/calls/:uuid` | Detalhes/RCA |

---

## Configuração

### Variáveis de Ambiente (Python)

```bash
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-realtime-preview

# Áudio
AUDIO_FORMAT=g711_ulaw  # ou pcm16
FREESWITCH_SAMPLE_RATE=8000

# Backend
OMNIPLAY_WEBHOOK_URL=https://api.empresa.com/api/voice-ai/webhook

# RCA
RCA_ENABLED=true
RCA_LOG_LEVEL=INFO
```

### Configuração da Secretária (FusionPBX)

```json
{
  "name": "Ana",
  "voice": "coral",
  "language": "pt-BR",
  "system_prompt": "Você é Ana, secretária virtual...",
  "greeting": "Olá! Aqui é a Ana, como posso ajudar?",
  "tools_enabled": ["request_handoff", "take_message", "end_call"],
  "vad_config": {
    "type": "semantic_vad",
    "eagerness": "medium"
  },
  "transfer_destinations": [
    {"name": "Suporte", "extension": "1001"},
    {"name": "Comercial", "extension": "1002"}
  ]
}
```

---

## Troubleshooting

### Problema: Áudio Picotando

**Causas Possíveis**:
1. Buffer muito pequeno no `mod_audio_stream`
2. Latência de rede alta
3. CPU sobrecarregada

**Solução**:
```bash
# Verificar latência OpenAI
grep "ai_response_time_ms" /var/log/voice-ai/realtime.log

# Aumentar buffer no FreeSWITCH
# Em vars.xml: <X-PRE-PROCESS cmd="set" data="rtp_jitter_buffer_during_bridge=true"/>
```

### Problema: Transferência Falha

**Diagnóstico via RCA**:
```bash
# Buscar logs da chamada
GET /api/voice-ai/calls/{uuid}

# Verificar eventos:
# - TRANSFER_INITIATED (iniciou?)
# - TRANSFER_RINGING (ramal tocou?)
# - TRANSFER_NO_ANSWER / TRANSFER_REJECTED (resultado?)
```

### Problema: Tool Não Executado

**Verificar**:
1. Tool está registrado? `ToolRegistry.has("nome_do_tool")`
2. Parâmetros corretos? Verificar logs de validação
3. OpenAI está chamando? Verificar transcript

---

## Referências

- [OpenSpec Proposal](../openspec/changes/add-voice-ai-enhancements/proposal.md)
- [Tasks Checklist](../openspec/changes/add-voice-ai-enhancements/tasks.md)
- [Design Document](../openspec/changes/add-voice-ai-enhancements/design.md)
- [AVA Project](https://github.com/hkjarral/Asterisk-AI-Voice-Agent)
- [sip-to-ai Project](https://github.com/aicc2025/sip-to-ai)

---

*Última atualização: Janeiro 2026*
