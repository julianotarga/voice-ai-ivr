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

-- =============================================================================
-- BUSCAR CONFIGURAÇÕES DE ÁUDIO DO BANCO DE DADOS
-- =============================================================================
local jitter_min = 100
local jitter_max = 300
local jitter_step = 40
local stream_buffer = 320

-- Tentar ler as configurações do banco se secretary_uuid existir
if secretary_uuid and secretary_uuid ~= "" then
    local dbh = freeswitch.Dbh("fusionpbx")
    if dbh:connected() then
        local sql = string.format(
            [[SELECT 
                COALESCE(jitter_buffer_min, 100) as jitter_min,
                COALESCE(jitter_buffer_max, 300) as jitter_max,
                COALESCE(jitter_buffer_step, 40) as jitter_step,
                COALESCE(stream_buffer_size, 320) as stream_buffer
            FROM v_voice_secretaries 
            WHERE voice_secretary_uuid = '%s']], 
            secretary_uuid:gsub("'", "''")  -- escape SQL injection
        )
        
        dbh:query(sql, function(row)
            jitter_min = tonumber(row.jitter_min) or 100
            jitter_max = tonumber(row.jitter_max) or 300
            jitter_step = tonumber(row.jitter_step) or 40
            stream_buffer = tonumber(row.stream_buffer) or 320
            freeswitch.consoleLog("INFO", "[VoiceSecretary] Audio config from DB: jitter=" .. jitter_min .. ":" .. jitter_max .. ":" .. jitter_step .. ", buffer=" .. stream_buffer .. "\n")
        end)
        
        dbh:release()
    else
        freeswitch.consoleLog("WARNING", "[VoiceSecretary] Could not connect to database, using defaults\n")
    end
end

-- =============================================================================
-- CONFIGURAR VARIÁVEIS DE CANAL
-- =============================================================================
-- STREAM_PLAYBACK=true habilita receber áudio de volta do WebSocket
-- STREAM_SAMPLE_RATE=16000 define a taxa de amostragem
session:setVariable("STREAM_PLAYBACK", "true")
session:setVariable("STREAM_SAMPLE_RATE", "16000")
session:setVariable("STREAM_SUPPRESS_LOG", "false")  -- Habilitar logs para debug

-- CRÍTICO: Habilitar jitter buffer para evitar áudio picotado
-- Formato: jitterbuffer_msec=length:max_length:max_drift
local jitter_config = jitter_min .. ":" .. jitter_max .. ":" .. jitter_step
session:setVariable("jitterbuffer_msec", jitter_config)
freeswitch.consoleLog("INFO", "[VoiceSecretary] Jitter buffer set: " .. jitter_config .. "\n")

-- Buffer adicional do mod_audio_stream (se suportado)
session:setVariable("STREAM_BUFFER_SIZE", tostring(stream_buffer))

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
