# Voice AI IVR - Project Overview

## Description

Sistema de Secretária Virtual com IA para atendimento telefônico integrado ao FusionPBX/FreeSWITCH.

## Tech Stack

### Core
- **Python 3.11+** - Linguagem principal do serviço Voice AI
- **FreeSWITCH** - Plataforma de telefonia
- **FusionPBX** - Interface web de gerenciamento
- **PostgreSQL** - Banco de dados (compartilhado com FusionPBX)
- **Redis** - Cache e filas (opcional)

### AI Providers
- **OpenAI Realtime API** - Conversação em tempo real
- **OpenAI Whisper** - Speech-to-Text
- **ElevenLabs** - Text-to-Speech
- **Google Cloud Speech** - Alternativa STT

### Protocolos
- **ESL (Event Socket Layer)** - Comunicação com FreeSWITCH
- **WebSocket** - Comunicação com OpenAI Realtime
- **G.711 μ-law (PCMU)** - Codec de áudio telefônico

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      FusionPBX Web UI                       │
│                  (PHP - /app/voice_secretary)               │
├─────────────────────────────────────────────────────────────┤
│                      PostgreSQL                             │
│                   (v_voice_* tables)                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐           ┌─────────────────────────┐  │
│  │   FreeSWITCH    │◄─────────►│    Voice AI Service     │  │
│  │   (ESL:8021)    │   ESL     │    (Python:8022/8085)   │  │
│  │                 │  Outbound │                         │  │
│  └─────────────────┘           └──────────┬──────────────┘  │
│                                           │                 │
│                                           │ WebSocket       │
│                                           ▼                 │
│                                  ┌─────────────────────┐    │
│                                  │  OpenAI Realtime    │    │
│                                  │  (wss://api.openai) │    │
│                                  └─────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Conventions

### Multi-Tenant
- TODAS as tabelas DEVEM ter `domain_uuid` como coluna obrigatória
- TODAS as queries DEVEM filtrar por `domain_uuid`
- Prefixo `v_` em todas as tabelas (padrão FusionPBX)

### Migrations
- DEVEM ser idempotentes (usar `IF NOT EXISTS`, `DO $$ BEGIN ... EXCEPTION ... END $$`)
- Nome: `NNN_description.sql` (ex: `001_create_providers.sql`)
- Incluir comentários explicativos

### Logging
- Usar emojis para facilitar filtragem:
  - 📢 EVENT_BUS
  - 🔄 STATE_MACHINE
  - 💓 HEARTBEAT
  - ⏱️ TIMEOUT_MGR
  - 📞 SESSION
  - ❌ ERROR

### Código Python
- Type hints obrigatórios
- Docstrings em funções públicas
- Async/await para I/O

### Código PHP (FusionPBX App)
- Seguir padrões do FusionPBX
- Usar classe `database` para queries
- Sempre verificar `permission_exists()`

## Directory Structure

```
voice-ai-ivr/
├── database/
│   └── migrations/          # SQL migrations
├── deploy/
│   └── installer/           # Scripts de instalação
├── fusionpbx-app/
│   └── voice_secretary/     # App PHP para FusionPBX
├── voice-ai-service/
│   ├── config/              # Configurações
│   ├── core/                # Componentes core
│   ├── handlers/            # ESL handlers
│   ├── providers/           # AI providers
│   └── realtime/            # Realtime processing
├── docs/                    # Documentação
└── openspec/                # Especificações
```

## Key Files

- `voice-ai-service/realtime/__main__.py` - Entry point do serviço
- `fusionpbx-app/voice_secretary/app_config.php` - Configuração do app PHP
- `database/migrations/*.sql` - Schema do banco
- `deploy/baremetal/scripts/install.sh` - Instalador atual

## Links

- [FusionPBX Documentation](https://docs.fusionpbx.com/)
- [FreeSWITCH ESL](https://freeswitch.org/confluence/display/FREESWITCH/Event+Socket+Library)
- [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime)
