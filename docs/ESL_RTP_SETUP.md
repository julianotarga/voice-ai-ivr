# ESL + RTP Setup Guide

## Overview

Este documento descreve como configurar FreeSWITCH para usar ESL (Event Socket Library) + RTP direto com o Voice AI Service.

## Arquitetura

```
┌─────────────────┐     ESL (TCP:8022)     ┌─────────────────────┐
│   FreeSWITCH    │◄──────────────────────►│   Voice AI Service  │
│                 │                        │   (Python)          │
│   Channel       │     RTP (UDP:10000+)   │                     │
│   ↓ ↑           │◄──────────────────────►│   ├─ ESLController  │
│   RTP Media     │                        │   ├─ RTPBridge      │
└─────────────────┘                        │   └─ AISession      │
                                           └─────────────────────┘
```

## FreeSWITCH Configuration

### 1. Habilitar ESL (event_socket.conf.xml)

O ESL já deve estar habilitado no FreeSWITCH. Verifique:

```bash
# Verificar se ESL está habilitado
fs_cli -x "module_exists mod_event_socket"

# Verificar portas
netstat -tlnp | grep 8021
```

Arquivo de configuração padrão:

```xml
<!-- /etc/freeswitch/autoload_configs/event_socket.conf.xml -->
<configuration name="event_socket.conf" description="Socket Client">
  <settings>
    <param name="nat-map" value="false"/>
    <param name="listen-ip" value="0.0.0.0"/>
    <param name="listen-port" value="8021"/>
    <param name="password" value="ClueCon"/>
    <!-- Permitir múltiplas conexões -->
    <param name="apply-inbound-acl" value="lan"/>
  </settings>
</configuration>
```

### 2. Dialplan para ESL Outbound

O dialplan deve usar `socket` application para conectar ao nosso servidor ESL:

```xml
<!-- /etc/freeswitch/dialplan/default/900_voice_ai_esl.xml -->
<extension name="voice_ai_esl" continue="false">
  <condition field="destination_number" expression="^8000$">
    <action application="set" data="domain_uuid=${domain_uuid}"/>
    <action application="set" data="secretary_uuid=${secretary_uuid}"/>
    <action application="answer"/>
    <!-- Conectar ao ESL Server do Voice AI -->
    <action application="socket" data="127.0.0.1:8022 async full"/>
  </condition>
</extension>
```

**Parâmetros do socket:**
- `async`: Execução assíncrona (não bloqueia FreeSWITCH)
- `full`: Envia todas as variáveis do canal

### 3. Configuração de Rede para RTP

#### Firewall

```bash
# Permitir ESL (TCP)
ufw allow 8021/tcp  # ESL Inbound (se precisar)
ufw allow 8022/tcp  # ESL Outbound (Voice AI)

# Permitir RTP (UDP)
ufw allow 10000:10100/udp
```

#### NAT (se aplicável)

Se o Voice AI está em rede diferente do FreeSWITCH:

```xml
<!-- sip_profiles/internal.xml -->
<param name="ext-rtp-ip" value="$${external_rtp_ip}"/>
<param name="ext-sip-ip" value="$${external_sip_ip}"/>
```

### 4. Codec Configuration

Para RTP direto, forçar PCMU para simplicidade:

```xml
<!-- No dialplan antes do socket -->
<action application="set" data="absolute_codec_string=PCMU"/>
<action application="set" data="rtp_use_timer_name=none"/>
```

## Voice AI Service Configuration

### Environment Variables

```bash
# ESL Mode
ESL_SERVER_HOST=0.0.0.0
ESL_SERVER_PORT=8022

# RTP Pool
RTP_PORT_MIN=10000
RTP_PORT_MAX=10100

# Jitter Buffer
RTP_JITTER_MIN_MS=60
RTP_JITTER_MAX_MS=200

# Audio Mode
AUDIO_MODE=rtp  # "websocket" ou "rtp"
```

### Docker Compose

```yaml
voice-ai-realtime:
  build:
    context: ./voice-ai-service
    dockerfile: Dockerfile.realtime
  ports:
    - "8085:8085"      # WebSocket (fallback)
    - "8022:8022"      # ESL Outbound
    - "10000-10100:10000-10100/udp"  # RTP
  environment:
    - AUDIO_MODE=rtp
    - ESL_SERVER_PORT=8022
    - RTP_PORT_MIN=10000
    - RTP_PORT_MAX=10100
  # Se mesmo host que FreeSWITCH:
  network_mode: host
```

## Testing

### 1. Testar Conexão ESL

```bash
# Do host do Voice AI, conectar ao FreeSWITCH ESL
telnet <freeswitch_ip> 8021

# Digitar:
auth ClueCon
status
```

### 2. Testar ESL Outbound

```bash
# Iniciar o Voice AI em modo debug
python -m realtime.esl.server --debug

# Fazer uma chamada para 8000
# Verificar logs do Voice AI
```

### 3. Testar RTP

```bash
# Verificar se portas UDP estão abertas
ss -ulnp | grep 1000

# Usar wireshark ou tcpdump para capturar RTP
tcpdump -i any udp port 10000-10100 -w rtp.pcap
```

## Troubleshooting

### ESL Connection Refused

```bash
# Verificar se FreeSWITCH ESL está rodando
fs_cli -x "module_exists mod_event_socket"

# Verificar firewall
iptables -L -n | grep 8021
```

### RTP Not Receiving Audio

```bash
# Verificar NAT
fs_cli -x "sofia status profile internal"

# Verificar se RTP está sendo enviado
tcpdump -i any udp port 10000-10100 -c 10
```

### Fallback para WebSocket

Se ESL falhar, o sistema automaticamente usa WebSocket:

```yaml
# docker-compose.yml
environment:
  - AUDIO_MODE=websocket  # Fallback mode
```

## References

- [FreeSWITCH ESL](https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Client-and-Developer-Interfaces/Event-Socket-Library/)
- [greenswitch](https://github.com/EvoluxBR/greenswitch)
- [OpenSpec: refactor-esl-rtp-bridge](../../../openspec/changes/refactor-esl-rtp-bridge/proposal.md)
