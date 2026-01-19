-- Voice Secretary AI Script
-- Integração com Voice AI Realtime via WebSocket (mod_audio_stream v1.0.3+)
-- Ref oficial: https://github.com/amigniter/mod_audio_stream
--
-- Este script é executado pelo FreeSWITCH quando uma chamada entra na extensão
-- configurada (ex: 8000). Ele inicia o streaming de áudio bidirecional com o
-- serviço voice-ai-realtime que por sua vez se conecta ao ElevenLabs.

local domain_uuid = session:getVariable("domain_uuid") or ""
local call_uuid = session:getVariable("uuid") or ""
local caller_id = session:getVariable("caller_id_number") or ""

freeswitch.consoleLog("INFO", "[VoiceSecretary] Starting session\n")
freeswitch.consoleLog("INFO", "[VoiceSecretary]   domain_uuid: " .. domain_uuid .. "\n")
freeswitch.consoleLog("INFO", "[VoiceSecretary]   call_uuid: " .. call_uuid .. "\n")
freeswitch.consoleLog("INFO", "[VoiceSecretary]   caller_id: " .. caller_id .. "\n")

-- Atender chamada
session:answer()
session:sleep(300) -- pequeno delay para estabilizar o canal

-- =============================================================================
-- Configuração do mod_audio_stream v1.0.3+
-- =============================================================================
-- Channel variables oficiais (ref: README.md do mod_audio_stream)
--
-- STREAM_BUFFER_SIZE: duração do buffer em ms (múltiplo de 20, default 20)
--   - Valores maiores = menos overhead de rede, mais latência
--   - 200ms é um bom equilíbrio para voz AI
--
-- STREAM_SUPPRESS_LOG: true/1 para silenciar logs de resposta WS no console
--
-- STREAM_HEART_BEAT: intervalo em segundos para keep-alive (útil com load balancers)

-- Habilitar playback bidirecional (v1.0.3+)
-- Usar 8kHz para G.711 μ-law nativo (menor latência)
session:setVariable("STREAM_PLAYBACK", "true")
session:setVariable("STREAM_SAMPLE_RATE", "8000")

session:setVariable("STREAM_BUFFER_SIZE", "100")
session:setVariable("STREAM_SUPPRESS_LOG", "false")
-- session:setVariable("STREAM_HEART_BEAT", "15") -- opcional

-- =============================================================================
-- Iniciar stream de áudio
-- =============================================================================
-- URL do WebSocket do voice-ai-realtime
-- Formato: ws://<host>:<port>/stream/<domain_uuid>/<call_uuid>
local ws_host = session:getVariable("voice_ai_ws_host") or "127.0.0.1"
local ws_port = session:getVariable("voice_ai_ws_port") or "8085"
local ws_url = "ws://" .. ws_host .. ":" .. ws_port .. "/stream/" .. domain_uuid .. "/" .. call_uuid

freeswitch.consoleLog("INFO", "[VoiceSecretary] Connecting to WebSocket: " .. ws_url .. "\n")

local api = freeswitch.API()

-- Comando: uuid_audio_stream <uuid> start <ws-url> <mix-type> <sampling-rate> [audio-format]
--   mix-type: mono (apenas caller), mixed (caller+callee), stereo
--   sampling-rate: 8k ou 16k
--   audio-format: l16 (padrão), pcmu (G.711 μ-law), pcma (G.711 A-law)
-- Usar G.711 μ-law (pcmu) para menor latência com OpenAI Realtime
local cmd = "uuid_audio_stream " .. call_uuid .. " start " .. ws_url .. " mono 8k pcmu"
freeswitch.consoleLog("INFO", "[VoiceSecretary] Executing: " .. cmd .. "\n")

local result = api:executeString(cmd)
freeswitch.consoleLog("INFO", "[VoiceSecretary] Result: " .. tostring(result) .. "\n")

-- Verificar se o stream iniciou com sucesso
local started = false
if result and string.find(result, "%+OK") then
    started = true
    freeswitch.consoleLog("INFO", "[VoiceSecretary] Audio stream started successfully\n")
else
    freeswitch.consoleLog("WARNING", "[VoiceSecretary] Failed to start audio stream: " .. tostring(result) .. "\n")
end

-- =============================================================================
-- Manter sessão ativa enquanto a chamada estiver conectada
-- =============================================================================
-- O mod_audio_stream cuida do streaming bidirecional de forma assíncrona.
-- Este loop mantém o script ativo até a chamada ser encerrada.

while session:ready() do
    session:sleep(1000)
end

-- =============================================================================
-- Encerrar stream de áudio
-- =============================================================================
if started then
    local stop_cmd = "uuid_audio_stream " .. call_uuid .. " stop"
    freeswitch.consoleLog("INFO", "[VoiceSecretary] Stopping stream: " .. stop_cmd .. "\n")
    api:executeString(stop_cmd)
end

freeswitch.consoleLog("INFO", "[VoiceSecretary] Session ended for call: " .. call_uuid .. "\n")
