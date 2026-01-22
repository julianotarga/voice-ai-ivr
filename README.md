# Secretária Virtual com IA para FreeSWITCH/FusionPBX

Sistema de atendimento telefônico com IA que funciona como uma secretária virtual humana.

## 🆕 Arquitetura de Controle Interno (v2 - Jan/2026)

O sistema utiliza uma arquitetura de **controle interno** que reduz a dependência do FreeSWITCH para gerenciamento de estado. A lógica de negócio é controlada 100% pelo Python.

### Vantagens

| Aspecto | Antes | Depois |
|---------|-------|--------|
| **Fonte da verdade** | FreeSWITCH ESL events | StateMachine Python |
| **Detecção de problemas** | Esperar ESL HANGUP | HeartbeatMonitor proativo |
| **Comunicação** | Callbacks acoplados | EventBus desacoplado |
| **Timeouts** | FreeSWITCH timers | TimeoutManager interno |

### Componentes Core

```
voice-ai-service/realtime/core/
├── events.py          # VoiceEventType, VoiceEvent (tipagem)
├── event_bus.py       # Pub/sub interno async
├── state_machine.py   # Estados da chamada com guards
├── heartbeat.py       # Monitor de saúde da conexão
└── timeout_manager.py # Gerenciador de timeouts
```

### Estados da Chamada (CallState)

```
IDLE → CONNECTING → CONNECTED → LISTENING ↔ SPEAKING → PROCESSING
                                    │
                                    ↓
                        TRANSFERRING_VALIDATING
                                    │
                        TRANSFERRING_DIALING
                                    │
                        TRANSFERRING_ANNOUNCING
                                    │
                        TRANSFERRING_WAITING
                                    │
                        TRANSFERRING_BRIDGING → BRIDGED
                                    │
                                    ↓
                                  ENDED
```

### Logs Estruturados

```bash
# Identificação visual por emoji
📢 [EVENT_BUS]      # Eventos emitidos
🔄 [STATE_MACHINE]  # Transições de estado
💓 [HEARTBEAT]      # Monitoramento de saúde
⏱️ [TIMEOUT_MGR]   # Timeouts
📞 [SESSION]        # Início/fim de chamada
⚠️ [CORE]          # Warnings (conexão, provider)
```

### Documentação Detalhada

- `docs/PLANO-ARQUITETURA-INTERNA.md` - Plano completo da arquitetura
- `docs/KNOWLEDGE_BASE.md` - Referências de APIs (Context7)

---

## ⚠️ REQUISITOS OBRIGATÓRIOS

### Multi-Tenant
- TODAS as tabelas MUST ter `domain_uuid NOT NULL`
- TODAS as queries MUST filtrar por `domain_uuid`
- NUNCA vazar dados entre domínios

### Compatibilidade de Linguagem
- Scripts FreeSWITCH: **Lua 5.2+** (mod_lua)
- App FusionPBX: **PHP 7.4+ / 8.x**
- Banco de Dados: **PostgreSQL** (sintaxe nativa)
- Serviço Principal: **Python 3.11+** (asyncio)

---

## Estrutura do Projeto

```
voice-ai-ivr/
├── README.md                      # Este arquivo
│
├── voice-ai-service/              # Serviço Python (STT/TTS/LLM/RAG)
│   ├── main.py                    # FastAPI application
│   ├── requirements.txt           # Dependências Python
│   ├── config/                    # Configurações
│   │   └── settings.py
│   ├── api/                       # Endpoints REST
│   │   ├── __init__.py
│   │   ├── transcribe.py          # POST /transcribe
│   │   ├── synthesize.py          # POST /synthesize
│   │   ├── chat.py                # POST /chat
│   │   └── documents.py           # POST /documents
│   ├── services/                  # Lógica de negócio
│   │   ├── stt/                   # Speech-to-Text providers
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # Interface base
│   │   │   ├── whisper_local.py   # Whisper.cpp/faster-whisper
│   │   │   ├── whisper_api.py     # OpenAI Whisper API
│   │   │   ├── azure_speech.py    # Azure Speech-to-Text
│   │   │   ├── google_speech.py   # Google Cloud STT
│   │   │   ├── aws_transcribe.py  # AWS Transcribe
│   │   │   └── deepgram.py        # Deepgram
│   │   ├── tts/                   # Text-to-Speech providers
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── piper_local.py     # Piper TTS local
│   │   │   ├── openai_tts.py      # OpenAI TTS
│   │   │   ├── elevenlabs.py      # ElevenLabs
│   │   │   ├── azure_neural.py    # Azure Neural TTS
│   │   │   └── ...
│   │   ├── llm/                   # LLM providers
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── openai.py          # OpenAI GPT-4
│   │   │   ├── azure_openai.py    # Azure OpenAI
│   │   │   ├── anthropic.py       # Claude
│   │   │   ├── groq.py            # Groq (ultra-rápido)
│   │   │   ├── ollama_local.py    # Ollama local
│   │   │   └── ...
│   │   ├── embeddings/            # Embeddings providers
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── openai.py
│   │   │   └── local.py
│   │   └── rag/                   # Retrieval Augmented Generation
│   │       ├── __init__.py
│   │       ├── document_processor.py
│   │       ├── vector_store.py
│   │       └── retriever.py
│   ├── models/                    # Pydantic models
│   │   ├── __init__.py
│   │   ├── request.py
│   │   └── response.py
│   ├── data/                      # Dados locais
│   │   ├── whisper/               # Modelos Whisper
│   │   ├── piper/                 # Vozes Piper
│   │   └── embeddings/            # Cache de embeddings
│   └── tests/                     # Testes
│       ├── unit/
│       └── integration/
│
├── freeswitch/                    # Scripts FreeSWITCH (Lua)
│   ├── scripts/
│   │   ├── secretary_ai.lua       # Script principal
│   │   ├── lib/
│   │   │   ├── http.lua           # Cliente HTTP
│   │   │   ├── json.lua           # Parser JSON
│   │   │   ├── config.lua         # Carrega config do banco
│   │   │   └── utils.lua          # Utilitários
│   │   └── handlers/
│   │       ├── stt.lua            # Handler STT
│   │       ├── tts.lua            # Handler TTS
│   │       └── chat.lua           # Handler chat
│   ├── dialplan/
│   │   └── secretary.xml          # Roteamento de chamadas
│   └── sounds/
│       └── .gitkeep               # Áudios gerados
│
├── fusionpbx-app/                 # App FusionPBX (PHP)
│   └── voice_secretary/
│       ├── app_config.php         # Schema e permissões
│       ├── app_defaults.php       # Valores padrão
│       ├── app_languages.php      # Traduções
│       ├── app_menu.php           # Menu
│       ├── secretary.php          # Lista secretárias
│       ├── secretary_edit.php     # Editar secretária
│       ├── providers.php          # Lista providers
│       ├── providers_edit.php     # Configurar provider
│       ├── documents.php          # Lista documentos
│       ├── documents_edit.php     # Upload documento
│       ├── transfer_rules.php     # Regras de transferência
│       ├── transfer_rules_edit.php
│       ├── conversations.php      # Histórico
│       ├── conversation_detail.php
│       ├── settings.php           # Configurações
│       ├── resources/
│       │   ├── classes/
│       │   │   ├── voice_secretary.php
│       │   │   └── voice_ai_provider.php
│       │   ├── dashboard/
│       │   │   └── voice_secretary.php
│       │   └── functions/
│       └── languages/
│           └── pt-br/
│               └── app_languages.php
│
├── database/                      # Migrations
│   ├── migrations/
│   │   ├── 001_create_providers.sql
│   │   ├── 002_create_secretaries.sql
│   │   ├── 003_create_documents.sql
│   │   ├── 004_create_conversations.sql
│   │   └── 005_create_transfer_rules.sql
│   └── seeds/
│       └── default_providers.sql
│
├── deploy/                        # Scripts de deploy
│   ├── install.sh                 # Instalação completa
│   ├── systemd/
│   │   └── voice-ai-service.service
│   └── nginx/
│       └── voice-ai.conf
│
└── docs/                          # Documentação
    ├── installation.md
    ├── configuration.md
    ├── providers.md
    └── api.md
```

## Quick Start

### 1. Instalar Serviço Python

```bash
cd voice-ai-service
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configurar Providers

Edite `voice-ai-service/config/settings.py` com suas API keys.

### 3. Iniciar Serviço

```bash
# Desenvolvimento
uvicorn main:app --host 127.0.0.1 --port 8100 --reload

# Produção
systemctl start voice-ai-service
```

### 4. Instalar App FusionPBX

```bash
cp -r fusionpbx-app/voice_secretary /var/www/fusionpbx/app/
chown -R www-data:www-data /var/www/fusionpbx/app/voice_secretary
```

### 5. Instalar Scripts Lua

```bash
cp -r freeswitch/scripts/* /usr/share/freeswitch/scripts/
```

### 6. Rodar Migrations

```bash
# Via FusionPBX ou psql
psql -U fusionpbx -d fusionpbx -f database/migrations/001_create_providers.sql
```

## Providers Suportados

### STT (Speech-to-Text)
- ✅ Whisper Local (grátis)
- ✅ OpenAI Whisper API
- ✅ Azure Speech
- ✅ Google Speech
- ✅ AWS Transcribe
- ✅ Deepgram

### TTS (Text-to-Speech)
- ✅ Piper Local (grátis)
- ✅ OpenAI TTS
- ✅ ElevenLabs
- ✅ Azure Neural TTS
- ✅ Google Cloud TTS
- ✅ AWS Polly

### LLM (Language Models)
- ✅ OpenAI (GPT-4o, GPT-4o-mini)
- ✅ Azure OpenAI
- ✅ Anthropic Claude
- ✅ Google Gemini
- ✅ Groq (ultra-rápido)
- ✅ Ollama Local (grátis)
- ✅ LM Studio Local

### Embeddings (RAG)
- ✅ OpenAI
- ✅ Azure OpenAI
- ✅ Cohere
- ✅ sentence-transformers Local (grátis)

## Licença

Proprietário - OmniPlay
