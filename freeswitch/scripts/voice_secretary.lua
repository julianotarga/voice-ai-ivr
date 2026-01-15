-- Voice Secretary AI Script
-- Integração com Voice AI Realtime via WebSocket (mod_audio_stream v1.0.3+)
-- 
-- Referências:
-- - https://github.com/amigniter/mod_audio_stream
-- - https://github.com/os11k/freeswitch-elevenlabs-bridge
--
-- Configuração:
-- - URL: ws://127.0.0.1:8085/stream/{domain_uuid}/{call_uuid}
-- - Parâmetro mod_audio_stream: mixed 16k
-- - Formato de resposta: JSON com type="streamAudio"

local domain_uuid = session:getVariable("domain_uuid") or ""
local secretary_uuid = session:getVariable("secretary_uuid") or ""
local call_uuid = session:getVariable("uuid") or ""

-- Log inicial
freeswitch.consoleLog("INFO", "[VoiceSecretary] Starting - domain: " .. domain_uuid .. ", secretary: " .. secretary_uuid .. ", call: " .. call_uuid .. "\n")

-- Configurar variáveis de canal para mod_audio_stream
-- STREAM_PLAYBACK=true habilita receber áudio de volta do WebSocket
-- STREAM_SAMPLE_RATE=16000 define a taxa de amostragem
session:setVariable("STREAM_PLAYBACK", "true")
session:setVariable("STREAM_SAMPLE_RATE", "16000")
session:setVariable("STREAM_SUPPRESS_LOG", "false")  -- Habilitar logs para debug

-- CRÍTICO: Habilitar jitter buffer para evitar áudio picotado
-- Formato: jitterbuffer_msec=length:max_length:max_drift
-- 100ms inicial, 300ms máximo, 40ms drift - valores conservadores para Voice AI
session:setVariable("jitterbuffer_msec", "100:300:40")

-- Buffer adicional do mod_audio_stream (se suportado)
session:setVariable("STREAM_BUFFER_SIZE", "320")  -- 320 bytes = 20ms @ 16kHz

-- Atender chamada
session:answer()
session:sleep(500)

-- Montar URL do WebSocket
-- Formato: ws://127.0.0.1:8085/stream/{domain_uuid}/{call_uuid}
local ws_url = "ws://127.0.0.1:8085/stream/" .. domain_uuid .. "/" .. call_uuid

freeswitch.consoleLog("INFO", "[VoiceSecretary] Connecting to WebSocket: " .. ws_url .. "\n")

-- Iniciar audio stream via API
-- Sintaxe: uuid_audio_stream <uuid> start <url> <mix-type> <sampling-rate> [metadata]
-- mix-type: mono, mixed, stereo
-- sampling-rate: "8k" ou "16k" (não 8000 ou 16000!)
local api = freeswitch.API()
local cmd = "uuid_audio_stream " .. call_uuid .. " start " .. ws_url .. " mixed 16k"
freeswitch.consoleLog("INFO", "[VoiceSecretary] Executing: " .. cmd .. "\n")

local result = api:executeString(cmd)
freeswitch.consoleLog("INFO", "[VoiceSecretary] Result: " .. tostring(result) .. "\n")

-- Verificar se o stream iniciou com sucesso
if result and string.find(result, "Success") then
    freeswitch.consoleLog("INFO", "[VoiceSecretary] Audio stream started successfully\n")
else
    freeswitch.consoleLog("ERR", "[VoiceSecretary] Failed to start audio stream: " .. tostring(result) .. "\n")
end

-- Manter a sessão ativa enquanto o áudio é processado
while session:ready() do
    session:sleep(1000)
end

-- Parar o stream ao encerrar
local stop_cmd = "uuid_audio_stream " .. call_uuid .. " stop"
api:executeString(stop_cmd)

freeswitch.consoleLog("INFO", "[VoiceSecretary] Session ended\n")
