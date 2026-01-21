# Guia de Migração: Voice AI para Baremetal

Migração do voice-ai-ivr de Docker para execução baremetal, **co-residente com FreeSWITCH** no mesmo servidor.

## Objetivo

Eliminar latência de rede Docker entre voice-ai e FreeSWITCH para:
- Comandos ESL (hold, transfer, conference)
- Streaming de áudio WebSocket
- Anúncios em tempo real

## Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│          Servidor FreeSWITCH (Ubuntu Baremetal)         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   FreeSWITCH              Voice AI (NOVO - baremetal)   │
│   ├── SIP: 5060           ├── realtime: 8085, 8086      │
│   ├── ESL: 8021 ◄───────► ├── ESL out: 8022             │
│   └── RTP: 16384-32767    └── service: 8100 (opcional)  │
│                                                         │
│   PostgreSQL (FusionPBX)                                │
│   └── 5432                                              │
│                                                         │
└─────────────────────────────────────────────────────────┘
              │
              │ Rede (HTTPS/AMQP)
              ▼
┌─────────────────────────────────────────────────────────┐
│              Servidor Docker (outro host)               │
├─────────────────────────────────────────────────────────┤
│   Backend, Frontend, Microservices                      │
│   Redis, RabbitMQ, MinIO                                │
└─────────────────────────────────────────────────────────┘
```

---

## Passo 1: Preparar Servidor FreeSWITCH

### 1.1 Verificar FreeSWITCH

```bash
# FreeSWITCH deve estar rodando
systemctl status freeswitch

# ESL deve estar disponível
nc -zv 127.0.0.1 8021
```

### 1.2 Executar Script de Instalação

```bash
# Copiar arquivos para o servidor
scp -r deploy/baremetal/ root@SERVIDOR_FREESWITCH:/tmp/

# No servidor FreeSWITCH:
cd /tmp/baremetal
sudo ./scripts/install.sh
```

O script irá:
- Verificar FreeSWITCH está rodando
- Instalar Python 3.11 (se necessário)
- Instalar dependências (ffmpeg, libspeexdsp)
- Criar usuário `voiceai`
- Criar diretórios `/opt/voice-ai/` e `/var/log/voice-ai/`
- Instalar units systemd

---

## Passo 2: Deploy do Código

### 2.1 Copiar Código

```bash
# No servidor FreeSWITCH:
rsync -av --exclude='venv' --exclude='__pycache__' \
    /path/to/voice-ai-ivr/voice-ai-service/ /opt/voice-ai/
```

### 2.2 Criar Virtual Environment

```bash
cd /opt/voice-ai
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

### 2.3 Ajustar Permissões

```bash
chown -R voiceai:voiceai /opt/voice-ai
```

---

## Passo 3: Configurar

### 3.1 Editar Variáveis de Ambiente

```bash
sudo nano /opt/voice-ai/.env
```

**Configurações críticas:**

```bash
# FreeSWITCH ESL - LOCALHOST (mesmo servidor!)
ESL_HOST=127.0.0.1
ESL_PORT=8021
ESL_PASSWORD=ClueCon

# PostgreSQL (FusionPBX) - geralmente localhost
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=fusionpbx
DB_USER=fusionpbx
DB_PASS=SUA_SENHA

# Redis - IP do servidor Docker!
REDIS_HOST=192.168.1.10
REDIS_PORT=6379

# Backend API - URL pública!
OMNIPLAY_API_URL=https://api.exemplo.com.br
VOICE_AI_SERVICE_TOKEN=SEU_TOKEN

# OpenAI
OPENAI_API_KEY=sk-xxx
```

### 3.2 Configurar FreeSWITCH (mod_audio_stream)

Editar o dialplan para usar localhost:

```xml
<!-- /etc/freeswitch/dialplan/default.xml ou similar -->
<action application="uuid_audio_stream" data="${uuid} start ws://127.0.0.1:8085 mono 8000"/>
```

---

## Passo 4: Ativar Serviços

### 4.1 Habilitar para Boot

```bash
sudo systemctl enable voice-ai-realtime
# sudo systemctl enable voice-ai-service  # Opcional
```

### 4.2 Iniciar

```bash
sudo systemctl start voice-ai-realtime
```

### 4.3 Verificar Status

```bash
# Script de status
./scripts/status.sh

# Ou manualmente
systemctl status voice-ai-realtime
journalctl -u voice-ai-realtime -f
```

---

## Passo 5: Testar

### 5.1 Verificar Portas

```bash
ss -tlnp | grep -E '8085|8086|8022'
```

### 5.2 Fazer Chamada de Teste

1. Ligar para um número configurado com voice-ai
2. Verificar logs: `journalctl -u voice-ai-realtime -f`
3. Confirmar que ESL está usando localhost (latência < 1ms)

### 5.3 Testar Transferência Anunciada

1. Solicitar transferência durante a chamada
2. Verificar que B-leg conecta via porta 8086
3. Confirmar áudio bidirecional funcionando

---

## Passo 6: Desativar Docker (após validação)

Apenas após confirmar que tudo funciona:

```bash
# No servidor Docker onde voice-ai rodava:
cd /path/to/voice-ai-ivr
docker compose stop voice-ai-realtime
docker compose rm voice-ai-realtime
```

---

## Rollback

Se algo der errado, reverter para Docker:

```bash
# No servidor FreeSWITCH:
sudo systemctl stop voice-ai-realtime
sudo systemctl disable voice-ai-realtime

# No servidor Docker:
cd /path/to/voice-ai-ivr
docker compose up -d voice-ai-realtime
```

Ou usar o script:

```bash
sudo ./scripts/rollback-to-docker.sh /path/to/voice-ai-ivr
```

---

## Operação

### Comandos Frequentes

```bash
# Status
sudo systemctl status voice-ai-realtime
./scripts/status.sh

# Logs tempo real
journalctl -u voice-ai-realtime -f

# Logs de arquivo
tail -f /var/log/voice-ai/realtime.log

# Reiniciar
sudo systemctl restart voice-ai-realtime

# Atualizar código
sudo ./scripts/deploy.sh /path/to/voice-ai-ivr
```

### Troubleshooting

**Serviço não inicia:**
```bash
journalctl -u voice-ai-realtime -e
```

**ESL não conecta:**
```bash
# Verificar FreeSWITCH ESL
nc -zv 127.0.0.1 8021

# Verificar senha no .env
grep ESL_PASSWORD /opt/voice-ai/.env

# Verificar senha no FreeSWITCH
grep password /etc/freeswitch/autoload_configs/event_socket.conf.xml
```

**Redis não conecta:**
```bash
# Verificar IP do Redis
grep REDIS_HOST /opt/voice-ai/.env

# Testar conexão
nc -zv <IP_REDIS> 6379
```

---

## Checklist

- [ ] FreeSWITCH rodando no servidor
- [ ] Python 3.11 instalado
- [ ] Script install.sh executado
- [ ] Código copiado para /opt/voice-ai/
- [ ] Virtual environment criado
- [ ] Dependências instaladas
- [ ] .env configurado com localhost para ESL
- [ ] .env configurado com IP real para Redis
- [ ] Permissões ajustadas (voiceai:voiceai)
- [ ] Serviço habilitado (enable)
- [ ] Serviço iniciado (start)
- [ ] Portas 8085, 8086, 8022 respondendo
- [ ] Chamada de teste funcionando
- [ ] Transferência anunciada funcionando
- [ ] Docker voice-ai desativado

---

*Última atualização: Janeiro 2026*
