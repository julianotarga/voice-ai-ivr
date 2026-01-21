# Voice AI Baremetal Deployment

Deploy do voice-ai-ivr **baremetal no servidor FreeSWITCH** para latência mínima.

> **Escopo**: Apenas voice-ai-ivr. Backend, frontend e microservices continuam em Docker.

## Quick Start

```bash
# 1. No servidor FreeSWITCH, instalar (como root)
sudo ./scripts/install.sh

# 2. Copiar código
rsync -av /path/to/voice-ai-ivr/voice-ai-service/ /opt/voice-ai/

# 3. Criar venv e instalar deps
cd /opt/voice-ai
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate

# 4. Configurar
nano /opt/voice-ai/.env  # Editar ESL_HOST=127.0.0.1, REDIS_HOST=<ip_docker>

# 5. Ajustar permissões
chown -R voiceai:voiceai /opt/voice-ai

# 6. Iniciar
systemctl enable voice-ai-realtime
systemctl start voice-ai-realtime

# 7. Verificar
./scripts/status.sh
```

## Estrutura

```
deploy/baremetal/
├── README.md                    # Este arquivo
├── MIGRATION-GUIDE.md           # Guia detalhado
├── config/
│   ├── voice-ai.env.template    # Template de variáveis
│   └── logrotate-voice-ai.conf  # Rotação de logs
├── scripts/
│   ├── install.sh               # Instalação inicial
│   ├── deploy.sh                # Deploy/update código
│   ├── status.sh                # Status dos serviços
│   └── rollback-to-docker.sh    # Rollback emergencial
└── systemd/
    ├── voice-ai-realtime.service  # WebSocket/ESL bridge
    └── voice-ai-service.service   # HTTP API (opcional)
```

## Portas

| Porta | Descrição |
|-------|-----------|
| 8085 | WebSocket A-leg (mod_audio_stream) |
| 8086 | WebSocket B-leg (announced transfer) |
| 8022 | ESL Outbound |
| 8100 | HTTP API (opcional) |

## Comandos

```bash
# Status
./scripts/status.sh
systemctl status voice-ai-realtime

# Logs
journalctl -u voice-ai-realtime -f

# Controle
systemctl start voice-ai-realtime
systemctl stop voice-ai-realtime
systemctl restart voice-ai-realtime

# Deploy/Update
./scripts/deploy.sh /path/to/voice-ai-ivr

# Rollback para Docker
./scripts/rollback-to-docker.sh /path/to/voice-ai-ivr
```

## Documentação

Consulte [MIGRATION-GUIDE.md](./MIGRATION-GUIDE.md) para instruções completas.
