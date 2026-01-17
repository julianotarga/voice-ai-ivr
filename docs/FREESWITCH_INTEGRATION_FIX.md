# Correções Técnicas: Integração FreeSWITCH + Voice AI Realtime

**Data:** 2026-01-13  
**Domínio de teste:** ativo.netplay.net.br  
**Problema inicial:** `DESTINATION_OUT_OF_ORDER` ao discar para extensão 8000

---

## Resumo Executivo

A integração entre FusionPBX/FreeSWITCH e o container Voice AI Realtime apresentou 5 problemas distintos que foram corrigidos sequencialmente.

---

## 1. Dialplan XML Incorreto no Banco de Dados

### Problema
O campo `dialplan_xml` na tabela `v_dialplans` continha `audio_stream` como application, mas `audio_stream` **não é uma application válida** do FreeSWITCH - é apenas uma **API** (`uuid_audio_stream`).

```xml
<!-- ERRADO - estava no banco (path /ws/ e audio_stream como application) -->
<action application="audio_stream" data="ws://127.0.0.1:8085/ws/${domain_uuid}/${secretary_uuid}/${uuid}"/>

<!-- CORRETO - usar api_on_answer com uuid_audio_stream -->
<action application="set" data="api_on_answer=uuid_audio_stream ${uuid} start ws://127.0.0.1:8085/stream/${VOICE_AI_SECRETARY_UUID}/${uuid}/${caller_id_number} mono 16k"/>
```

### Solução
O dialplan deve chamar um **script Lua** que por sua vez usa a API `uuid_audio_stream`:

```xml
<!-- CORRETO -->
<action application="lua" data="voice_secretary.lua"/>
```

### Comando de Correção
```sql
UPDATE v_dialplans 
SET dialplan_xml = '<extension name="voice_secretary_carlos" continue="false" uuid="4f167587-f340-4e96-9850-8479730f0b19">
    <condition field="destination_number" expression="^8000$">
        <action application="set" data="domain_uuid=96f6142d-02b1-49fa-8bcb-f98658bb831f"/>
        <action application="set" data="secretary_uuid=dc923a2f-b88a-4a2f-8029-d6e0c06893c5"/>
        <action application="lua" data="voice_secretary.lua"/>
    </condition>
</extension>'
WHERE dialplan_uuid = '4f167587-f340-4e96-9850-8479730f0b19';
```

### Importante
No FusionPBX, o campo `dialplan_xml` é o que realmente é usado pelo FreeSWITCH. Os registros em `v_dialplan_details` são apenas para a interface web. **Ambos devem estar sincronizados.**

---

## 2. Cache do FusionPBX

### Problema
Mesmo após corrigir o banco, o FreeSWITCH continuava usando o XML antigo devido ao cache.

### Configuração do Cache
```ini
# /etc/fusionpbx/config.conf
cache.method = file
cache.location = /var/cache/fusionpbx
```

### Solução
Limpar o cache de arquivos:

```bash
# Limpar cache
rm -rf /var/cache/fusionpbx/*

# Recarregar XML no FreeSWITCH
fs_cli -x "reload xml"
```

### Via PHP (alternativa)
```bash
php -r "
require '/var/www/fusionpbx/resources/require.php';
\$cache = new cache;
\$cache->delete('dialplan:ativo.netplay.net.br');
echo 'Cache cleared';
"
```

---

## 3. Parâmetro Inválido no mod_audio_stream

### Problema
```
[ERR] mod_audio_stream.c:256 invalid mix type: both, must be mono, mixed, or stereo
```

O parâmetro `both` não é válido para o `mod_audio_stream`.

### Sintaxe Correta
```
uuid_audio_stream <uuid> start <url> <mix_type> <sample_rate>
```

Onde `mix_type` deve ser:
- `mono` - Apenas áudio do chamador **(RECOMENDADO para IA conversacional)**
- `mixed` - Ambos os canais mixados (cuidado: IA pode "ouvir" a própria resposta)
- `stereo` - Canais separados (útil para gravação)

> 💡 **Por que `mono`?** A resposta da IA (TTS) é reproduzida via ESL, não pelo WebSocket. Com `mono`, a IA só ouve o cliente, evitando loops de eco.

### Solução
```lua
-- ERRADO (mix type e sample rate inválidos)
local cmd = "uuid_audio_stream " .. call_uuid .. " start " .. ws_url .. " both 8000"

-- CORRETO (mono para IA, 16k com 'k')
local cmd = "uuid_audio_stream " .. call_uuid .. " start " .. ws_url .. " mono 16k"
```

**Nota:** Sample rate deve usar `16k` (com 'k'), NÃO `16000`.

---

## 4. URL do WebSocket Incorreta

### Problema
```
WARNING - Invalid path: /ws/96f6142d-02b1-49fa-8bcb-f98658bb831f/dc923a2f-b88a-4a2f-8029-d6e0c06893c5/...
```

O servidor `voice-ai-realtime` esperava um path diferente.

### Path Esperado pelo Servidor
Conforme código em `/voice-ai-service/realtime/server.py`:
```python
# URL Pattern: /stream/{secretary_uuid}/{call_uuid}/{caller_id}
# caller_id é opcional
```

### Solução
```lua
-- ERRADO (path /ws/ não existe)
local ws_url = "ws://127.0.0.1:8085/ws/" .. domain_uuid .. "/" .. secretary_uuid .. "/" .. call_uuid

-- CORRETO (/stream/{secretary_uuid}/{call_uuid}/{caller_id})
local ws_url = "ws://127.0.0.1:8085/stream/" .. secretary_uuid .. "/" .. call_uuid .. "/" .. caller_id
```

**Nota:** O path é `/stream/{secretary_uuid}/{call_uuid}/{caller_id}`. O `domain_uuid` NÃO faz parte do path - apenas `secretary_uuid`, `call_uuid` e opcionalmente `caller_id`.

---

## 5. Script Lua Final Corrigido

### Localização
```
/usr/share/freeswitch/scripts/voice_secretary.lua
```

### Conteúdo Correto
```lua
-- Voice Secretary AI Script
-- Integração com Voice AI Realtime via WebSocket

local domain_uuid = session:getVariable("domain_uuid") or ""
local secretary_uuid = session:getVariable("secretary_uuid") or ""
local call_uuid = session:getVariable("uuid") or ""

-- Log inicial
freeswitch.consoleLog("INFO", "[VoiceSecretary] Starting - domain: " .. domain_uuid .. ", secretary: " .. secretary_uuid .. ", call: " .. call_uuid .. "\n")

-- Atender chamada
session:answer()
session:sleep(500)

-- Montar URL do WebSocket
-- Formato esperado pelo servidor: /stream/{domain_uuid}/{call_uuid}
local ws_url = "ws://127.0.0.1:8085/stream/" .. domain_uuid .. "/" .. call_uuid

freeswitch.consoleLog("INFO", "[VoiceSecretary] Connecting to WebSocket: " .. ws_url .. "\n")

-- Iniciar audio stream via API
-- Sintaxe: uuid_audio_stream <uuid> start <url> <mix_type> <sample_rate>
-- IMPORTANTE: usar 'mono' para IA conversacional (evita eco) e '16k' (com 'k')
local api = freeswitch.API()
local cmd = "uuid_audio_stream " .. call_uuid .. " start " .. ws_url .. " mono 16k"
freeswitch.consoleLog("INFO", "[VoiceSecretary] Executing: " .. cmd .. "\n")

local result = api:executeString(cmd)
freeswitch.consoleLog("INFO", "[VoiceSecretary] Result: " .. tostring(result) .. "\n")

-- Manter a sessão ativa
while session:ready() do
    session:sleep(1000)
end

freeswitch.consoleLog("INFO", "[VoiceSecretary] Session ended\n")
```

### Permissões
```bash
chown freeswitch:freeswitch /usr/share/freeswitch/scripts/voice_secretary.lua
chmod 644 /usr/share/freeswitch/scripts/voice_secretary.lua
```

---

## 6. Módulo mod_audio_stream

### Instalação do Módulo

O `mod_audio_stream` **não vem pré-instalado** com o FreeSWITCH. É necessário compilar e instalar manualmente usando o repositório oficial do sptmru que compila como módulo isolado.

#### Instalação Automatizada (Recomendado)

Um script de instalação automatizada está disponível em `scripts/install-mod-audio-stream.sh`:

```bash
# No servidor onde o FreeSWITCH está instalado
cd /caminho/para/voice-ai-ivr
chmod +x scripts/install-mod-audio-stream.sh
sudo ./scripts/install-mod-audio-stream.sh
```

O script automaticamente:
- Instala todas as dependências necessárias
- Clona e compila o módulo
- Instala e carrega o módulo no FreeSWITCH
- Verifica se a instalação foi bem-sucedida

#### Instalação Manual

Se preferir instalar manualmente, siga os passos abaixo:

#### Passo 1: Clonar o Repositório

```bash
cd /usr/src
rm -rf freeswitch_mod_audio_stream  # Remover versão antiga se existir
git clone https://github.com/sptmru/freeswitch_mod_audio_stream.git
cd freeswitch_mod_audio_stream
git submodule init
git submodule update
```

#### Passo 2: Instalar Dependências

```bash
apt-get update
apt-get install -y libfreeswitch-dev libssl-dev zlib1g-dev libspeexdsp-dev cmake build-essential
```

**Nota:** O pacote `libfreeswitch-dev` contém os headers necessários para compilar módulos do FreeSWITCH.

#### Passo 3: Compilar o Módulo

```bash
mkdir -p build && cd build
cmake ..
make
```

**Importante:** Se ocorrerem erros de compilação, verifique:
- Versão do FreeSWITCH instalada (deve ser compatível)
- Todas as dependências instaladas
- Permissões no diretório `/usr/src`

#### Passo 4: Instalar o Módulo

```bash
# Copiar o módulo compilado para o diretório de módulos do FreeSWITCH
cp mod_audio_stream.so /usr/lib/freeswitch/mod/

# Ajustar permissões
chmod 644 /usr/lib/freeswitch/mod/mod_audio_stream.so
chown freeswitch:freeswitch /usr/lib/freeswitch/mod/mod_audio_stream.so
```

#### Passo 5: Carregar o Módulo

```bash
# Carregar manualmente para testar
fs_cli -x "load mod_audio_stream"

# Verificar se carregou corretamente
fs_cli -x "module_exists mod_audio_stream"
# Deve retornar: true
```

#### Passo 6: Configurar Autoload (Opcional)

Para carregar automaticamente ao iniciar o FreeSWITCH, adicionar em `/etc/freeswitch/autoload_configs/modules.conf.xml`:

```xml
<load module="mod_audio_stream"/>
```

**Nota:** Após adicionar, é necessário reiniciar o FreeSWITCH ou executar `fs_cli -x "reload mod_audio_stream"`.

### Verificação

#### Verificar se o Módulo está Carregado
```bash
fs_cli -x "module_exists mod_audio_stream"
# Deve retornar: true
```

#### Verificar API Disponível
```bash
fs_cli -x "show api" | grep audio_stream
# Deve mostrar: uuid_audio_stream,...
```

#### Testar a API
```bash
# Verificar sintaxe da API
fs_cli -x "uuid_audio_stream help"
# Deve mostrar a ajuda da API
```

### Troubleshooting da Instalação

#### Erro: "mod_audio_stream.so: cannot open shared object file"
- Verificar se o arquivo existe em `/usr/lib/freeswitch/mod/`
- Verificar permissões do arquivo
- Verificar se todas as dependências estão instaladas

#### Erro: "undefined symbol" durante carregamento
- Verificar versão do FreeSWITCH (deve ser compatível com o módulo)
- Recompilar o módulo se necessário
- Verificar se `libfreeswitch-dev` corresponde à versão instalada

#### Erro de Compilação: "freeswitch.h not found"
- Instalar `libfreeswitch-dev`: `apt-get install -y libfreeswitch-dev`
- Verificar se os headers estão em `/usr/include/freeswitch/`

#### Módulo não aparece após compilação
- Verificar se o arquivo `.so` foi gerado em `build/`
- Verificar logs do FreeSWITCH: `tail -f /var/log/freeswitch/freeswitch.log`
- Tentar carregar manualmente e verificar erros

---

## 7. Checklist de Validação

### Antes de Testar
- [ ] `mod_audio_stream` carregado
- [ ] Script Lua em `/usr/share/freeswitch/scripts/voice_secretary.lua`
- [ ] `dialplan_xml` correto no banco (usando `lua voice_secretary.lua`)
- [ ] Cache do FusionPBX limpo
- [ ] Container `voice-ai-realtime` rodando na porta 8085

### Comandos de Teste
```bash
# 1. Verificar módulo
fs_cli -x "module_exists mod_audio_stream"

# 2. Verificar container
docker ps | grep voice-ai-realtime

# 3. Testar conectividade WebSocket
curl -v -H "Connection: Upgrade" -H "Upgrade: websocket" \
  http://127.0.0.1:8085/stream/test/test 2>&1 | head -10

# 4. Fazer chamada de teste
fs_cli -x "originate {domain_uuid=SEU_DOMAIN_UUID,domain_name=SEU_DOMINIO}loopback/8000/SEU_DOMINIO &park"

# 5. Verificar logs
tail -f /var/log/freeswitch/freeswitch.log | grep VoiceSecretary
docker logs -f voice-ai-realtime
```

---

## 8. Arquitetura da Integração

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│   Telefone/     │     │   FreeSWITCH     │     │  voice-ai-realtime  │
│   Softphone     │────▶│   (FusionPBX)    │────▶│    Container        │
│                 │ SIP │                  │ WS  │    :8085            │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
                              │                           │
                              │ Lua Script                │ WebSocket
                              │ voice_secretary.lua       │ /stream/{domain}/{call}
                              │                           │
                              ▼                           ▼
                        uuid_audio_stream ──────▶  Audio Streaming
                        (mod_audio_stream)        (16kHz, mixed)
```

---

## 9. Troubleshooting

### Erro: DESTINATION_OUT_OF_ORDER
- Verificar `dialplan_xml` no banco
- Limpar cache do FusionPBX
- Verificar se script Lua existe

### Erro: Invalid Application
- O dialplan está chamando uma application que não existe
- Usar `lua script.lua` em vez de `audio_stream` diretamente

### Erro: connection closed (WebSocket)
- Verificar URL do WebSocket (deve ser `/stream/`, não `/ws/`)
- Verificar se container está rodando
- Verificar formato do path (apenas domain_uuid e call_uuid)

### Erro: invalid mix type
- Usar `mono`, `mixed` ou `stereo` (não `both`)

---

## 10. Referências

- **mod_audio_stream:** https://github.com/drachtio/drachtio-freeswitch-modules
- **FusionPBX Dialplan:** https://docs.fusionpbx.com/en/latest/dialplan/
- **FreeSWITCH mod_lua:** https://freeswitch.org/confluence/display/FREESWITCH/mod_lua

---

**Documento criado em:** 2026-01-13  
**Última atualização:** 2026-01-13  
**Autor:** Claude (Assistente AI)
