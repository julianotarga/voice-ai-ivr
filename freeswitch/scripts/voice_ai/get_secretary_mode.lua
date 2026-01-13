--[[
  get_secretary_mode.lua
  
  Determina o modo de processamento (turn_based/realtime) para uma secretária.
  
  Referências:
  - .context/docs/data-flow.md: Fluxo de autenticação multi-tenant
  - .context/docs/architecture.md: processing_mode
  - openspec/changes/voice-ai-realtime/proposal.md: Coexistência v1/v2
  
  Saída (variáveis de sessão):
  - voice_ai_mode: "turn_based", "realtime", ou "fallback"
  - voice_ai_secretary_uuid: UUID da secretária
  - voice_ai_secretary_name: Nome da secretária
--]]

-- Obtém variáveis da sessão
local domain_uuid = session:getVariable("domain_uuid") or ""
local extension = session:getVariable("voice_ai_extension") or ""

-- Log
freeswitch.consoleLog("INFO", string.format(
    "[VoiceAI] get_secretary_mode: domain=%s, extension=%s\n",
    domain_uuid, extension
))

-- Valores padrão
local mode = "turn_based"
local secretary_uuid = ""
local secretary_name = "Secretária Virtual"

-- Verifica se domain_uuid está presente (Multi-tenant obrigatório)
if domain_uuid == "" then
    freeswitch.consoleLog("WARNING", "[VoiceAI] domain_uuid not set, using fallback\n")
    session:setVariable("voice_ai_mode", "fallback")
    return
end

-- Conectar ao banco PostgreSQL
local dbh = freeswitch.Dbh("pgsql://hostaddr=localhost dbname=fusionpbx user=fusionpbx password=")

if not dbh:connected() then
    freeswitch.consoleLog("ERR", "[VoiceAI] Database connection failed\n")
    session:setVariable("voice_ai_mode", "fallback")
    return
end

-- Buscar configuração da secretária
-- Multi-tenant: filtrar por domain_uuid obrigatório
local sql = string.format([[
    SELECT 
        secretary_uuid,
        name,
        processing_mode,
        is_enabled
    FROM v_voice_secretaries 
    WHERE domain_uuid = '%s' 
      AND extension = '%s'
      AND is_enabled = true
    LIMIT 1
]], domain_uuid, extension)

local found = false

dbh:query(sql, function(row)
    found = true
    secretary_uuid = row.secretary_uuid or ""
    secretary_name = row.name or "Secretária Virtual"
    
    local db_mode = row.processing_mode or "turn_based"
    
    -- Determinar modo final
    if db_mode == "realtime" then
        mode = "realtime"
    elseif db_mode == "auto" then
        -- Auto: tentar realtime, fallback para turn_based
        mode = check_realtime_available() and "realtime" or "turn_based"
    else
        mode = "turn_based"
    end
    
    freeswitch.consoleLog("INFO", string.format(
        "[VoiceAI] Secretary found: %s, mode=%s\n",
        secretary_name, mode
    ))
end)

-- Fechar conexão
dbh:release()

-- Se não encontrou secretária, usar fallback
if not found then
    freeswitch.consoleLog("WARNING", string.format(
        "[VoiceAI] No secretary found for extension %s in domain %s\n",
        extension, domain_uuid
    ))
    mode = "fallback"
end

-- Se modo realtime, verificar se bridge está disponível
if mode == "realtime" then
    if not check_realtime_bridge() then
        freeswitch.consoleLog("WARNING", "[VoiceAI] Realtime bridge not available, using turn_based\n")
        mode = "turn_based"
    end
end

-- Definir variáveis de sessão
session:setVariable("voice_ai_mode", mode)
session:setVariable("voice_ai_secretary_uuid", secretary_uuid)
session:setVariable("voice_ai_secretary_name", secretary_name)

freeswitch.consoleLog("INFO", string.format(
    "[VoiceAI] Final mode: %s for secretary %s\n",
    mode, secretary_name
))

--[[
  Funções auxiliares
--]]

-- Verifica se mod_audio_stream está carregado
function check_realtime_available()
    local api = freeswitch.API()
    local result = api:executeString("module_exists mod_audio_stream")
    return result == "true"
end

-- Verifica se o bridge WebSocket está respondendo
function check_realtime_bridge()
    -- Tenta conectar no bridge via socket simples
    -- Em produção, usar health check HTTP
    local socket = require("socket")
    local client = socket.tcp()
    client:settimeout(2)
    
    local success, err = client:connect("127.0.0.1", 8085)
    client:close()
    
    if success then
        return true
    else
        freeswitch.consoleLog("WARNING", string.format(
            "[VoiceAI] Bridge health check failed: %s\n",
            err or "unknown"
        ))
        return false
    end
end
