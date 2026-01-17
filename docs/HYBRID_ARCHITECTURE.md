# Arquitetura Híbrida: ESL + WebSocket

## Resumo

Este documento descreve a arquitetura recomendada para o Voice AI IVR, utilizando:
- **ESL (Event Socket Library)** para controle de chamada
- **mod_audio_stream (WebSocket)** para transporte de áudio

Esta combinação oferece o melhor dos dois mundos: controle granular via ESL e compatibilidade universal com NAT via WebSocket.

## Histórico e Decisão

### Problema Identificado (2026-01-17)

Durante os testes de produção, identificamos que o modo **RTP direto** não funciona quando clientes estão atrás de NAT:

```
RTPBridge stopped: sent=2 pkts, recv=0 pkts  ← Não recebe pacotes do cliente!
```

**Causa:** O cliente em rede privada (`192.168.77.115`) envia RTP para o FreeSWITCH (`45.165.80.15:25750`), mas o Voice AI container está esperando em outra porta (`10000`). O cliente não sabe enviar para o container.

### Modos de Áudio Disponíveis

| Modo | Porta | Transporte | NAT | Latência |
|------|-------|------------|-----|----------|
| **RTP** | 10000+ UDP | UDP direto | ❌ Problemático | ⚡ Mínima |
| **WebSocket** | 8085 TCP | mod_audio_stream | ✅ Automático | +10-20ms |
| **Híbrido** | 8022 + 8085 | ESL + WebSocket | ✅ Automático | +10-20ms |

### Decisão

**Adotar arquitetura híbrida:**
- ESL Outbound (porta 8022) → Controle de chamada
- mod_audio_stream (porta 8085) → Transporte de áudio

## Arquitetura

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ARQUITETURA HÍBRIDA                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐                              ┌────────────────────────┐   │
│  │   Cliente    │                              │   Voice AI Container   │   │
│  │   (Telefone) │                              │                        │   │
│  └──────┬───────┘                              │  ┌──────────────────┐  │   │
│         │                                      │  │ ESL Server       │  │   │
│         │ SIP/RTP                              │  │ (Controle)       │  │   │
│         │                                      │  │ Porta: 8022      │  │   │
│         ▼                                      │  └────────▲─────────┘  │   │
│  ┌──────────────┐     ESL Outbound (TCP)       │           │            │   │
│  │  FreeSWITCH  │◄─────────────────────────────┼───────────┘            │   │
│  │              │                              │                        │   │
│  │  1. Recebe   │     mod_audio_stream (WS)    │  ┌──────────────────┐  │   │
│  │     chamada  │─────────────────────────────►│  │ WebSocket Server │  │   │
│  │  2. Conecta  │                              │  │ (Áudio)          │  │   │
│  │     ESL      │◄─────────────────────────────┤  │ Porta: 8085      │  │   │
│  │  3. Inicia   │                              │  └────────┬─────────┘  │   │
│  │     audio_   │                              │           │            │   │
│  │     stream   │                              │           ▼            │   │
│  └──────────────┘                              │  ┌──────────────────┐  │   │
│                                                │  │ AI Session       │  │   │
│                                                │  │ (OpenAI/Eleven)  │  │   │
│                                                │  └──────────────────┘  │   │
│                                                └────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Fluxo de Chamada

### 1. Cliente Liga

```
Cliente → SIP INVITE → FreeSWITCH
```

### 2. FreeSWITCH Executa Dialplan

```xml
<extension name="voice_ai_hybrid">
  <condition field="destination_number" expression="^8000$">
    <!-- Variáveis para identificação -->
    <action application="set" data="VOICE_AI_SECRETARY_UUID=dc923a2f-b88a-4a2f-8029-d6e0c06893c5"/>
    <action application="set" data="VOICE_AI_DOMAIN_UUID=${domain_uuid}"/>
    
    <!-- Atender chamada -->
    <action application="answer"/>
    
    <!-- 1. Conectar ESL para CONTROLE -->
    <action application="socket" data="127.0.0.1:8022 async full"/>
    
    <!-- 2. Iniciar mod_audio_stream para ÁUDIO -->
    <action application="audio_stream" data="ws://127.0.0.1:8085/ws start both"/>
    
    <!-- Manter chamada ativa -->
    <action application="park"/>
  </condition>
</extension>
```

### 3. Voice AI Recebe Conexões

1. **ESL Server (8022)** recebe conexão de controle
2. **WebSocket Server (8085)** recebe stream de áudio
3. Sistema correlaciona as conexões pelo `call_uuid`

### 4. Durante a Chamada

- **Áudio do cliente** → FreeSWITCH → WebSocket → Voice AI → IA
- **Áudio da IA** → Voice AI → WebSocket → FreeSWITCH → Cliente
- **Comandos** (transfer, hangup) → Voice AI → ESL → FreeSWITCH

### 5. Handoff/Transfer

Quando cliente pede para falar com humano:

```python
# Via ESL (controle)
await esl_client.uuid_broadcast(call_uuid, "tone_stream://%(250,0,800)", "aleg")
await esl_client.uuid_hold(call_uuid)
await esl_client.originate(f"user/{extension}@{domain}", ...)
await esl_client.uuid_bridge(call_uuid, new_call_uuid)
```

## Configuração

### 1. Variáveis de Ambiente (.env)

```env
# Modo de áudio
AUDIO_MODE=websocket

# ESL (controle)
ESL_HOST=host.docker.internal
ESL_PORT=8021
ESL_PASSWORD=ClueCon

# WebSocket (áudio)
REALTIME_HOST=0.0.0.0
REALTIME_PORT=8085
```

### 2. Docker Compose

```yaml
voice-ai-realtime:
  ports:
    # ESL Outbound (controle)
    - "8022:8022"
    # WebSocket (áudio)
    - "8085:8085"
```

### 3. FreeSWITCH - Verificar mod_audio_stream

```bash
# Verificar se módulo está carregado
fs_cli -x "module_exists mod_audio_stream"

# Se não estiver, carregar
fs_cli -x "load mod_audio_stream"
```

### 4. Dialplan no FusionPBX

Via interface web:
1. **Dialplan → Dialplan Manager → + Add**
2. **Name:** `voice_ai_hybrid`
3. **Number:** `8000`
4. **Context:** `[seu-domínio]`
5. **Enabled:** `true`

**Condition:**
- Type: `destination_number`
- Data: `^8000$`

**Actions (em ordem):**

| # | Tag | Type | Data |
|---|-----|------|------|
| 1 | action | set | `VOICE_AI_SECRETARY_UUID=seu-uuid-aqui` |
| 2 | action | set | `VOICE_AI_DOMAIN_UUID=${domain_uuid}` |
| 3 | action | answer | |
| 4 | action | socket | `127.0.0.1:8022 async full` |
| 5 | action | audio_stream | `ws://127.0.0.1:8085/ws start both` |
| 6 | action | park | |

## Vantagens da Arquitetura Híbrida

### 1. Compatibilidade com NAT ✅

O FreeSWITCH lida com toda a complexidade de NAT/firewall:
- Clientes em redes privadas funcionam automaticamente
- Não precisa de configuração de STUN/TURN
- Não precisa abrir portas UDP

### 2. Controle Granular via ESL ✅

Podemos executar comandos avançados:
- `uuid_transfer` - Transferir chamada
- `uuid_hold` - Colocar em espera
- `uuid_broadcast` - Tocar áudio
- `uuid_bridge` - Conectar chamadas
- `originate` - Originar chamadas (callback)

### 3. Transporte Confiável ✅

WebSocket sobre TCP:
- Garantia de entrega
- Ordem preservada
- Reconexão automática

### 4. Debug Facilitado ✅

- Logs separados para controle e áudio
- Fácil de inspecionar WebSocket com ferramentas padrão
- Estado da conexão ESL visível

## Comparação com Alternativas

### Modo RTP Puro (não recomendado)

```
AUDIO_MODE=rtp
```

**Problema:** Não funciona com NAT sem configuração complexa de proxy_media.

### Modo WebSocket Puro (alternativa simples)

```
AUDIO_MODE=websocket
```

**Problema:** Não usa ESL, perde controle granular para handoff.

### Modo Híbrido (recomendado) ✅

```
AUDIO_MODE=websocket + dialplan com socket + audio_stream
```

**Melhor dos dois mundos.**

## Troubleshooting

### Áudio não chega no Voice AI

```bash
# Verificar mod_audio_stream
fs_cli -x "module_exists mod_audio_stream"

# Verificar conexão WebSocket
docker compose logs voice-ai-realtime | grep -i websocket

# Testar porta
curl -i --no-buffer -H "Connection: Upgrade" -H "Upgrade: websocket" http://localhost:8085/ws
```

### ESL não conecta

```bash
# Verificar porta
netstat -tlnp | grep 8022

# Verificar logs
docker compose logs voice-ai-realtime | grep -i esl
```

### Handoff não funciona

```bash
# Verificar ESL inbound
fs_cli -x "event_socket connections"

# Testar comando manualmente
fs_cli -x "show channels"
```

## Referências

- [FreeSWITCH mod_audio_stream](https://github.com/signalwire/freeswitch/tree/master/src/mod/endpoints/mod_audio_stream)
- [FreeSWITCH ESL](https://freeswitch.org/confluence/display/FREESWITCH/Event+Socket+Library)
- [Voice AI IVR - ESL_CONNECTION_GUIDE.md](./ESL_CONNECTION_GUIDE.md)
- [Voice AI IVR - ESL_RTP_SETUP.md](./ESL_RTP_SETUP.md)

---

**Documento criado:** 2026-01-17  
**Autor:** Claude AI + Juliano Targa  
**Status:** RECOMENDADO para produção
