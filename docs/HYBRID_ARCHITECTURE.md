# Arquitetura Híbrida: ESL + WebSocket (mod_audio_stream)

## Resumo

Este documento descreve a arquitetura recomendada para o Voice AI IVR, utilizando:
- **ESL (Event Socket Library)** para controle de chamada
- **mod_audio_stream (WebSocket)** para transporte de áudio

Esta combinação oferece o melhor dos dois mundos: controle granular via ESL e compatibilidade universal com NAT via WebSocket.

> ⚠️ **IMPORTANTE:** `mod_audio_stream` é um módulo de terceiros, **NÃO é padrão** do FreeSWITCH/FusionPBX. Consulte a seção de instalação.

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
│  │     uuid_    │                              │           │            │   │
│  │     audio_   │                              │           ▼            │   │
│  │     stream   │                              │  ┌──────────────────┐  │   │
│  └──────────────┘                              │  │ AI Session       │  │   │
│                                                │  │ (OpenAI/Eleven)  │  │   │
│                                                │  └──────────────────┘  │   │
│                                                └────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Sobre o mod_audio_stream

### O que é?

O `mod_audio_stream` é um módulo de terceiros para FreeSWITCH que permite:
- Transmitir áudio do canal ativo para um endpoint WebSocket
- Receber respostas (JSON ou áudio)
- Suportar comunicação **bidirecional** (full-duplex desde v1.0.2+)

### Repositório Oficial

- **GitHub:** https://github.com/amigniter/mod_audio_stream
- **Autor:** amigniter
- **Status:** Módulo de terceiros (NÃO oficial do FreeSWITCH)

### Comandos da API

O módulo expõe estes comandos via API:

| Comando | Descrição |
|---------|-----------|
| `uuid_audio_stream <uuid> start <url> <mix> <rate> [metadata]` | Inicia streaming |
| `uuid_audio_stream <uuid> stop` | Para streaming |
| `uuid_audio_stream <uuid> pause` | Pausa streaming |
| `uuid_audio_stream <uuid> resume` | Retoma streaming |
| `uuid_audio_stream <uuid> send_text <texto>` | Envia texto para o WebSocket |

### Parâmetros do `uuid_audio_stream start`

| Parâmetro | Valores | Descrição |
|-----------|---------|-----------|
| `<uuid>` | `${uuid}` | UUID da chamada (variável do FreeSWITCH) |
| `<url>` | `ws://...` ou `wss://...` | URL do servidor WebSocket |
| `<mix>` | `mono`, `mixed`, `stereo` | Tipo de mixagem de áudio |
| `<rate>` | `8k`, `16k` | Taxa de amostragem |
| `[metadata]` | JSON opcional | Dados extras enviados ao WebSocket |

> ⚠️ **IMPORTANTE:** Use `8k` ou `16k` (com 'k'), NÃO `8000` ou `16000`!

### Qual Mix Type usar?

| Mix Type | O que captura | Recomendação |
|----------|---------------|--------------|
| **`mono`** | Apenas áudio do CHAMADOR (cliente) | ✅ **RECOMENDADO para IA conversacional** |
| `mixed` | Ambos os lados mixados | ⚠️ Evitar - IA pode "ouvir" a própria resposta |
| `stereo` | Canais separados (L=caller, R=callee) | 📝 Útil para gravação com separação |

> 💡 **Por que `mono`?** A resposta da IA (TTS) é reproduzida via ESL (`uuid_broadcast`, `playback`), diretamente no FreeSWITCH. Com `mono`, esse áudio NÃO volta para o WebSocket, evitando que a IA "escute a si mesma" e cause loops ou confusão no STT.

### Eventos Gerados

O módulo dispara eventos no FreeSWITCH:

| Evento | Descrição |
|--------|-----------|
| `mod_audio_stream::connect` | Conexão WebSocket estabelecida |
| `mod_audio_stream::disconnect` | Conexão encerrada |
| `mod_audio_stream::json` | Dados JSON recebidos |
| `mod_audio_stream::error` | Erro na conexão |
| `mod_audio_stream::play` | Áudio de resposta sendo reproduzido |

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

### 3. FreeSWITCH - INSTALAR mod_audio_stream (OBRIGATÓRIO)

> ⚠️ **IMPORTANTE:** `mod_audio_stream` NÃO é um módulo padrão do FreeSWITCH. Ele precisa ser instalado manualmente!

#### Passo 3.1: Verificar se já está instalado

```bash
# Verificar se módulo existe
fs_cli -x "module_exists mod_audio_stream"
# Se retornar "false", precisa instalar!

# Listar módulos carregados
fs_cli -x "show modules" | grep audio_stream
```

#### Passo 3.2: Instalar mod_audio_stream

O módulo está disponível em: https://github.com/amigniter/mod_audio_stream

```bash
# 1. Instalar dependências de compilação
apt-get install -y git build-essential libfreeswitch-dev libcurl4-openssl-dev \
    libssl-dev libspeexdsp-dev libjsoncpp-dev

# 2. Clonar o repositório
cd /usr/src
git clone https://github.com/amigniter/mod_audio_stream.git
cd mod_audio_stream

# 3. Compilar
make

# 4. Instalar o módulo
cp mod_audio_stream.so /usr/lib/freeswitch/mod/

# 5. Habilitar no autoload (adicionar ao modules.conf.xml)
# Edite o arquivo e adicione a linha:
nano /etc/freeswitch/autoload_configs/modules.conf.xml
# Adicione: <load module="mod_audio_stream"/>

# 6. Carregar o módulo (sem reiniciar FreeSWITCH)
fs_cli -x "load mod_audio_stream"

# 7. Verificar se carregou
fs_cli -x "module_exists mod_audio_stream"
# Deve retornar "true"
```

### 4. Dialplan no FusionPBX (Tutorial Passo a Passo)

#### Passo 1: Acessar Dialplan Manager

No menu do FusionPBX, navegue até: **Dialplan → Dialplan Manager → + Add**

#### Passo 2: Preencher Informações Básicas

| Campo | Valor | Observação |
|-------|-------|------------|
| **Name** | `voice_ai_hybrid_8000` | Nome identificador |
| **Number** | `8000` | Ramal que ativará a IA |
| **Context** | `${domain_name}` | Ou o nome do seu domínio |
| **Order** | `100` | Prioridade de execução |
| **Enabled** | `true` | Dialplan ativo |
| **Continue** | `false` | ⚠️ **CRÍTICO: DEVE SER FALSE!** |
| **Description** | `Voice AI - Secretária Virtual Híbrida` | Descrição |

> ⚠️ **IMPORTANTE:** O campo `Continue` DEVE ser `false`. Se for `true`, o FreeSWITCH continuará processando outros dialplans.

#### Passo 3: Adicionar Condição

Na seção "Dialplan Details", clique em **+ Add** e configure:

| Campo | Valor |
|-------|-------|
| **Tag** | `condition` |
| **Type** | `destination_number` |
| **Data** | `^8000$` |
| **Order** | `0` |

#### Passo 4: Adicionar Ações (ORDEM CORRETA!)

> ⚠️ **CRÍTICO:** A ordem das ações é fundamental! `api_on_answer` é definido ANTES do `answer`, mas executado DEPOIS.

Adicione as seguintes ações **na ordem exata**:

| Ordem | Tag | Type | Data | Função |
|-------|-----|------|------|--------|
| 1 | action | `set` | `VOICE_AI_SECRETARY_UUID=SEU-UUID-AQUI` | 🔑 Identifica a secretária |
| 2 | action | `set` | `VOICE_AI_DOMAIN_UUID=${domain_uuid}` | 🏢 Passa o domínio |
| 3 | action | `set` | `STREAM_PLAYBACK=true` | 🔊 **CRÍTICO: Habilita playback bidirecional** |
| 4 | action | `set` | `jitterbuffer_msec=100:300:40` | 🎚️ Configura jitter buffer (evita áudio picotado) |
| 5 | action | `set` | `api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/stream/${VOICE_AI_SECRETARY_UUID}/${uuid}/${caller_id_number} mono 16k` | 🎙️ Configura streaming (executa após answer) |
| 6 | action | `answer` | *(vazio)* | 📞 Atende a chamada (dispara api_on_answer) |
| 7 | action | `socket` | `127.0.0.1:8022 async full` | 🔌 Conecta ESL (controle) |
| 8 | action | `park` | *(vazio)* | ⏸️ Mantém chamada ativa |

> 💡 **Como obter o UUID da Secretária:** Vá em Voice Secretary → Secretaries, clique para editar, e o UUID está na URL: `/secretary_edit.php?id=UUID-AQUI`

#### Passo 5: Salvar e Recarregar

1. Clique em **Save**
2. No terminal do servidor, execute:
```bash
fs_cli -x "reloadxml"
```

#### XML Gerado (Referência)

O FusionPBX gera automaticamente este XML:

```xml
<extension name="voice_ai_hybrid_8000" continue="false">
  <condition field="destination_number" expression="^8000$">
    <!-- 1. Identificação da secretária e domínio -->
    <action application="set" data="VOICE_AI_SECRETARY_UUID=dc923a2f-b88a-4a2f-8029-d6e0c06893c5"/>
    <action application="set" data="VOICE_AI_DOMAIN_UUID=${domain_uuid}"/>
    
    <!-- 2. CRÍTICO: Habilitar playback bidirecional -->
    <action application="set" data="STREAM_PLAYBACK=true"/>
    
    <!-- 3. Configurar jitter buffer para evitar áudio picotado -->
    <action application="set" data="jitterbuffer_msec=100:300:40"/>
    
    <!-- 4. Configurar streaming via api_on_answer -->
    <!-- Este comando será executado APÓS o answer -->
    <action application="set" data="api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/stream/${VOICE_AI_SECRETARY_UUID}/${uuid}/${caller_id_number} mono 16k"/>
    
    <!-- 5. Atender a chamada (dispara api_on_answer automaticamente) -->
    <action application="answer"/>
    
    <!-- 6. ESL para CONTROLE (transferências, hangup, hold) -->
    <action application="socket" data="127.0.0.1:8022 async full"/>
    
    <!-- 7. Manter chamada ativa enquanto IA processa -->
    <action application="park"/>
  </condition>
</extension>
```

## Variáveis de Canal Opcionais

O `mod_audio_stream` suporta variáveis de canal para configuração avançada:

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `STREAM_BUFFER_SIZE` | Tamanho do buffer em ms | `20` |
| `STREAM_SAMPLE_RATE` | Taxa de amostragem | `16000` |
| `STREAM_PLAYBACK` | Habilitar playback bidirecional | `true` |
| `STREAM_MESSAGE_DEFLATE` | Compressão de mensagens | `true` |

Exemplo de uso:

```xml
<action application="set" data="STREAM_BUFFER_SIZE=20"/>
<action application="set" data="STREAM_PLAYBACK=true"/>
<action application="set" data="api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/stream/${VOICE_AI_SECRETARY_UUID}/${uuid}/${caller_id_number} mono 16k"/>
<action application="answer"/>
```

## Fluxo de Chamada

### 1. Cliente Liga

```
Cliente → SIP INVITE → FreeSWITCH
```

### 2. FreeSWITCH Executa Dialplan

1. Define variáveis (`VOICE_AI_SECRETARY_UUID`, etc.)
2. Configura `api_on_answer` com `uuid_audio_stream`
3. Atende a chamada (`answer`) - dispara `api_on_answer`
4. Conecta ESL para controle (`socket`)
5. Mantém chamada ativa (`park`)

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
AUDIO_MODE=websocket + dialplan com socket + uuid_audio_stream
```

**Melhor dos dois mundos.**

## Troubleshooting

### Módulo não encontrado

```bash
# Verificar se mod_audio_stream está instalado
fs_cli -x "module_exists mod_audio_stream"
# Se "false", precisa instalar!

# Ver lista de aplicações disponíveis
fs_cli -x "show applications" | grep audio
```

### Áudio não chega no Voice AI

```bash
# Verificar se módulo está carregado
fs_cli -x "show modules" | grep audio_stream

# Verificar conexão WebSocket
docker compose logs voice-ai-realtime | grep -i websocket

# Testar porta
nc -zv 127.0.0.1 8085
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
# Verificar ESL inbound (para comandos do Voice AI)
fs_cli -x "event_socket connections"

# Testar comando manualmente
fs_cli -x "show channels"
```

### Erro "Invalid Application"

Se aparecer erro "Invalid Application audio_stream":
- Você está tentando usar `audio_stream` como aplicação direta
- O correto é usar `uuid_audio_stream` via `api_on_answer`
- Verifique se o dialplan está usando a sintaxe correta

## Referências

- [mod_audio_stream (GitHub)](https://github.com/amigniter/mod_audio_stream) - Repositório oficial do módulo
- [FreeSWITCH ESL](https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_event_socket_1048924/) - Documentação do Event Socket
- [FreeSWITCH XML Dialplan](https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Dialplan/XML-Dialplan-archive_6586601) - Documentação do Dialplan
- [FusionPBX Dialplan](https://docs.fusionpbx.com/en/latest/dialplan/dialplan_application.html) - Aplicações disponíveis no FusionPBX
- [Voice AI IVR - ESL_CONNECTION_GUIDE.md](./ESL_CONNECTION_GUIDE.md)

---

**Documento criado:** 2026-01-17  
**Última revisão:** 2026-01-17 (com base na documentação oficial)  
**Autor:** Claude AI + Juliano Targa  
**Status:** RECOMENDADO para produção
