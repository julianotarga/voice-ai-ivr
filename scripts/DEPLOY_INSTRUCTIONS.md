# Deploy: FreeSWITCH ↔ ElevenLabs Audio Bidirecional

## Objetivo

Este guia configura o sistema para que:
- **Caller seja ouvido**: FreeSWITCH → mod_audio_stream → voice-ai-realtime → ElevenLabs
- **Caller ouça o agent**: ElevenLabs → voice-ai-realtime → mod_audio_stream → FreeSWITCH

## Pré-requisitos

- FreeSWITCH instalado (Debian 12)
- Docker e Docker Compose
- Conta ElevenLabs com Conversational AI Agent configurado
- FusionPBX (opcional, para gerenciar dialplans via UI)

## Passos

### 1. Instalar mod_audio_stream v1.0.3+

O módulo v1.0.3+ suporta streaming bidirecional com playback automático. Abaixo está o passo a passo completo para repetir a instalação no futuro.

```bash
# No servidor FreeSWITCH (modo automatizado)
cd /tmp
wget https://raw.githubusercontent.com/julianotarga/voice-ai-ivr/main/scripts/install-mod-audio-stream-v103.sh
chmod +x install-mod-audio-stream-v103.sh
sudo bash install-mod-audio-stream-v103.sh
```

#### 1.1 Passo a passo manual (recomendado para repetição)

```bash
# 1) Dependências do build
sudo apt-get update
sudo apt-get install -y git cmake build-essential \
  libfreeswitch-dev libssl-dev zlib1g-dev \
  libevent-dev libspeexdsp-dev pkg-config

# 2) Backup do módulo atual (se existir)
if [ -f /usr/lib/freeswitch/mod/mod_audio_stream.so ]; then
  sudo cp /usr/lib/freeswitch/mod/mod_audio_stream.so \
    /usr/lib/freeswitch/mod/mod_audio_stream.so.bak.$(date +%Y%m%d_%H%M%S)
fi

# 3) Clonar e inicializar submodules
cd /usr/src
sudo rm -rf mod_audio_stream
sudo git clone https://github.com/amigniter/mod_audio_stream.git
cd mod_audio_stream
sudo git submodule init
sudo git submodule update

# 4) Build (TLS opcional, mas recomendado para wss://)
sudo mkdir -p build && cd build
sudo cmake -DCMAKE_BUILD_TYPE=Release -DUSE_TLS=ON ..
sudo make -j$(nproc)
sudo make install

# 5) Recarregar no FreeSWITCH
fs_cli -x "unload mod_audio_stream" || true
fs_cli -x "load mod_audio_stream"
```

#### 1.2 Verificações obrigatórias

```bash
# Deve retornar: true
fs_cli -x "module_exists mod_audio_stream"

# Verificar se carregou na lista
fs_cli -x "show modules" | grep -i audio_stream
```

#### 1.3 Checar versão (v1.0.3+)

```bash
strings /usr/lib/freeswitch/mod/mod_audio_stream.so | grep -i "1.0.3" || true
```

#### 1.4 Reversão (rollback)

```bash
# Descarregar módulo atual
fs_cli -x "unload mod_audio_stream"

# Restaurar backup (se necessário)
sudo cp /usr/lib/freeswitch/mod/mod_audio_stream.so.bak.<DATA> \
  /usr/lib/freeswitch/mod/mod_audio_stream.so

# Recarregar
fs_cli -x "load mod_audio_stream"
```

Ou manualmente:

```bash
# Dependências
sudo apt-get install -y git cmake build-essential \
    libfreeswitch-dev libssl-dev zlib1g-dev \
    libevent-dev libspeexdsp-dev pkg-config

# Clonar e compilar
cd /usr/src
sudo git clone https://github.com/amigniter/mod_audio_stream.git
cd mod_audio_stream
sudo git submodule init && sudo git submodule update
sudo mkdir build && cd build
sudo cmake -DCMAKE_BUILD_TYPE=Release -DUSE_TLS=ON ..
sudo make -j$(nproc)
sudo make install

# Recarregar no FreeSWITCH
fs_cli -x "unload mod_audio_stream"
fs_cli -x "load mod_audio_stream"
```

Verificar:
```bash
fs_cli -x "module_exists mod_audio_stream"
# Deve retornar: true
```

Verificar versão (v1.0.3+ recomendado):
```bash
strings /usr/lib/freeswitch/mod/mod_audio_stream.so | grep -i "1.0.3" || true
```

### 2. Copiar script Lua

```bash
# Copiar o script Lua para o FreeSWITCH
sudo cp voice_secretary.lua /usr/share/freeswitch/scripts/
sudo chown freeswitch:freeswitch /usr/share/freeswitch/scripts/voice_secretary.lua
```

### 3. Configurar Dialplan

Opção A - Via SQL (recomendado):
```bash
sudo -u postgres psql fusionpbx < fix_dialplan_8000.sql
fs_cli -x "reloadxml"
```

Opção B - Via FusionPBX UI:
1. Dialplan > Dialplan Manager
2. Localizar ou criar dialplan para extensão 8000
3. Configurar:
   - Order: 5
   - Continue: false
   - XML:
   ```xml
   <extension name="voice_secretary_8000" continue="false">
     <condition field="destination_number" expression="^8000$">
       <action application="lua" data="voice_secretary.lua"/>
     </condition>
   </extension>
   ```

### 4. Deploy do voice-ai-realtime

```bash
cd ~/voice-ai-ivr
git pull origin main
docker compose up -d --build voice-ai-realtime
```

Verificar logs:
```bash
docker compose logs -f voice-ai-realtime
```

### 5. Configurar ElevenLabs Agent

No painel ElevenLabs:
1. Criar ou editar um Conversational AI Agent
2. Em Audio Settings, configurar:
   - **Output Format**: PCM 16000 Hz
3. Copiar o Agent ID
4. Configurar no FusionPBX:
   - Voice Secretary > AI Providers
   - Criar provider "elevenlabs_conversational"
   - Adicionar `api_key` e `agent_id` nas credentials

### 6. Testar

Ligar para extensão 8000:
```bash
# Monitorar logs do FreeSWITCH
tail -f /var/log/freeswitch/freeswitch.log | grep -i "VoiceSecretary\|mod_audio_stream\|audio_streamer"

# Monitorar logs do bridge
docker compose logs -f voice-ai-realtime
```

Critérios de sucesso:
- Você ouve o greeting do agent nos primeiros segundos
- O agent responde quando você fala
- Não há erros de policy violation (1008) nos logs

### Ajuste de qualidade/latência (opcional)

Se o áudio estiver com lag perceptível, ajuste:

1) **Warmup do bridge** (reduz buffer inicial):
```bash
export FS_WARMUP_CHUNKS=5   # 5 chunks = 100ms
```

2) **Buffer do FreeSWITCH** (reduz buffer de captura):
```lua
-- no voice_secretary.lua
session:setVariable("STREAM_BUFFER_SIZE", "100")
```

Depois reinicie o container do bridge e recarregue o dialplan.

## Troubleshooting

### "No audio heard"

1. Verificar se mod_audio_stream v1.0.3+ está instalado:
```bash
fs_cli -x "show modules" | grep audio_stream
```

2. Verificar se o WebSocket está conectando:
```bash
docker compose logs voice-ai-realtime | grep -i "connection\|audio"
```

3. Testar playback manual:
```bash
# Com chamada ativa
fs_cli -x "uuid_broadcast <UUID> /tmp/test.r16 aleg"
```

### "Policy violation (1008)"

O ElevenLabs está rejeitando overrides. Verificar:
1. No FusionPBX, editar o provider
2. Remover voice_id, first_message, prompt das credentials
3. Ou definir `use_agent_config: true`

### "Session creation failed"

1. Verificar se há uma secretária configurada para o domain:
```sql
SELECT * FROM v_voice_secretaries WHERE is_enabled = true;
```

2. Verificar se há um provider realtime vinculado:
```sql
SELECT s.*, p.provider_name 
FROM v_voice_secretaries s 
JOIN v_voice_ai_providers p ON p.voice_ai_provider_uuid = s.realtime_provider_uuid;
```

## Arquivos de Referência

- `voice_secretary.lua` - Script Lua do FreeSWITCH
- `install-mod-audio-stream-v103.sh` - Script de instalação do módulo
- `fix_dialplan_8000.sql` - SQL para corrigir dialplan
- `dialplan_8000_voice_secretary.xml` - Exemplo de dialplan XML

## Referências

- [mod_audio_stream README](https://github.com/amigniter/mod_audio_stream)
- [os11k/freeswitch-elevenlabs-bridge](https://github.com/os11k/freeswitch-elevenlabs-bridge)
- [Add AI Voice Agent to FreeSWITCH](https://www.cyberpunk.tools/jekyll/update/2025/11/18/add-ai-voice-agent-to-freeswitch.html)
- [ElevenLabs Conversational AI](https://elevenlabs.io/docs/agents-platform/)
