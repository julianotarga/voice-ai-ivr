-- Voice Secretary AI Script
-- Integração com Voice AI Realtime via WebSocket (mod_audio_stream v1.0.3+ fork G.711)
-- 
-- Referências:
-- - https://github.com/amigniter/mod_audio_stream (fork com suporte G.711)
-- - https://github.com/os11k/freeswitch-elevenlabs-bridge
--
-- Configuração:
-- - URL: ws://127.0.0.1:8085/stream/{domain_uuid}/{call_uuid}
-- - Parâmetro mod_audio_stream: mono 8k pcmu (G.711 μ-law nativo)
-- - G.711 reduz latência (~50ms) eliminando resampling
-- - Formato de resposta: JSON com type="streamAudio"

local domain_uuid = session:getVariable("domain_uuid") or ""
local secretary_uuid = session:getVariable("secretary_uuid") or ""
local call_uuid = session:getVariable("uuid") or ""
local caller_id = session:getVariable("caller_id_number") or session:getVariable("ani") or "unknown"

-- Log inicial
freeswitch.consoleLog("INFO", "[VoiceSecretary] Starting - domain: " .. domain_uuid .. ", secretary: " .. secretary_uuid .. ", call: " .. call_uuid .. ", caller: " .. caller_id .. "\n")

-- =============================================================================
-- BUSCAR CONFIGURAÇÕES DE ÁUDIO DO BANCO DE DADOS
-- =============================================================================
local jitter_min = 100
local jitter_max = 300
local jitter_step = 40
-- IMPORTANTE: STREAM_BUFFER_SIZE é em MILISSEGUNDOS, não samples!
-- Default: 20ms (frame size padrão do FreeSWITCH)
-- 320 samples @ 16kHz = 20ms, mas a variável espera MS diretamente!
local stream_buffer = 20  -- 20ms = valor padrão correto

-- Tentar ler as configurações do banco se secretary_uuid existir
if secretary_uuid and secretary_uuid ~= "" then
    local dbh = freeswitch.Dbh("fusionpbx")
    if dbh:connected() then
        local sql = string.format(
            [[SELECT 
                COALESCE(jitter_buffer_min, 100) as jitter_min,
                COALESCE(jitter_buffer_max, 300) as jitter_max,
                COALESCE(jitter_buffer_step, 40) as jitter_step,
                -- STREAM_BUFFER_SIZE é em MS, não samples! Default 20ms
                COALESCE(stream_buffer_size, 20) as stream_buffer
            FROM v_voice_secretaries 
            WHERE voice_secretary_uuid = '%s']], 
            secretary_uuid:gsub("'", "''")  -- escape SQL injection
        )
        
        dbh:query(sql, function(row)
            jitter_min = tonumber(row.jitter_min) or 100
            jitter_max = tonumber(row.jitter_max) or 300
            jitter_step = tonumber(row.jitter_step) or 40
            stream_buffer = tonumber(row.stream_buffer) or 20  -- 20ms default
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
-- STREAM_SAMPLE_RATE=8000 para G.711 (8kHz nativo)
session:setVariable("STREAM_PLAYBACK", "true")
session:setVariable("STREAM_SAMPLE_RATE", "8000")
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
-- Formato: ws://127.0.0.1:8085/stream/{secretary_uuid}/{call_uuid}/{caller_id}
-- secretary_uuid é usado para buscar configuração no banco
local ws_url = "ws://127.0.0.1:8085/stream/" .. secretary_uuid .. "/" .. call_uuid .. "/" .. caller_id

freeswitch.consoleLog("INFO", "[VoiceSecretary] Connecting to WebSocket: " .. ws_url .. "\n")

-- Iniciar audio stream via API
-- Sintaxe: uuid_audio_stream <uuid> start <url> <mix-type> <sampling-rate> [audio-format] [metadata]
-- mix-type: 
--   mono   = apenas áudio do CALLER (usuário) - CORRETO para Voice AI!
--   mixed  = caller + callee (causa eco do playback voltando ao AI)
--   stereo = caller em canal 1, callee em canal 2
-- sampling-rate: "8k" ou "16k" (não 8000 ou 16000!)
-- audio-format (fork G.711):
--   l16/pcm = Linear PCM 16-bit (padrão)
--   pcmu/ulaw = G.711 μ-law (RECOMENDADO - menor latência)
--   pcma/alaw = G.711 A-law
local api = freeswitch.API()
-- IMPORTANTE: usar MONO para evitar eco do playback!
-- Usar PCMU (G.711 μ-law) para menor latência com OpenAI Realtime
local cmd = "uuid_audio_stream " .. call_uuid .. " start " .. ws_url .. " mono 8k pcmu"
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
