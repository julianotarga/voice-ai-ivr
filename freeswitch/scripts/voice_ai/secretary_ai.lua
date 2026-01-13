--[[
  secretary_ai.lua
  
  Script Lua para modo turn-based (v1) da secretária virtual.
  
  Referências:
  - .context/docs/data-flow.md: Fluxo Turn-based v1
  - .context/docs/architecture.md: voice-ai-service:8100
  - openspec/changes/add-voice-ai-ivr/design.md
  
  Fluxo:
  1. Carrega configuração da secretária do banco
  2. Sintetiza e reproduz saudação
  3. Loop: grava → transcreve → LLM → sintetiza → reproduz
  4. Executa ação (transfer, hangup)
--]]

-- Configuração do serviço
local VOICE_AI_URL = os.getenv("VOICE_AI_URL") or "http://127.0.0.1:8100"
local VOICE_AI_API = VOICE_AI_URL .. "/api/v1"
local MAX_TURNS = 10
local SILENCE_THRESHOLD = 3  -- segundos de silêncio
local MAX_RECORD_TIME = 30   -- segundos máximos de gravação

-- Obtém variáveis da sessão
local domain_uuid = session:getVariable("domain_uuid") or ""
local call_uuid = session:getVariable("uuid") or ""
local caller_id = session:getVariable("caller_id_number") or ""
local secretary_uuid = session:getVariable("voice_ai_secretary_uuid") or ""
local secretary_name = session:getVariable("voice_ai_secretary_name") or "Secretária"

-- Histórico de conversa
local history = {}

-- Log inicial
freeswitch.consoleLog("INFO", string.format(
    "[VoiceAI] Starting turn-based secretary: %s, caller: %s\n",
    secretary_name, caller_id
))

-- Carregar biblioteca HTTP
local http = require("voice_ai.lib.http")
local json = require("voice_ai.lib.json")

--[[
  Função principal
--]]
function main()
    -- Verificar domain_uuid (Multi-tenant obrigatório)
    if domain_uuid == "" then
        freeswitch.consoleLog("ERR", "[VoiceAI] domain_uuid not set\n")
        return
    end
    
    -- Carregar configuração
    local config = load_secretary_config()
    if not config then
        freeswitch.consoleLog("ERR", "[VoiceAI] Failed to load secretary config\n")
        play_error_message()
        return
    end
    
    -- Reproduzir saudação
    if config.greeting and config.greeting ~= "" then
        synthesize_and_play(config.greeting)
    end
    
    -- Loop de conversação
    local turn = 0
    while turn < MAX_TURNS and session:ready() do
        turn = turn + 1
        
        -- Gravar entrada do usuário
        local audio_file = record_user_input()
        if not audio_file then
            freeswitch.consoleLog("WARNING", "[VoiceAI] No audio recorded\n")
            break
        end
        
        -- Transcrever
        local user_text = transcribe_audio(audio_file)
        if not user_text or user_text == "" then
            freeswitch.consoleLog("WARNING", "[VoiceAI] Transcription empty\n")
            synthesize_and_play("Desculpe, não consegui entender. Pode repetir?")
            goto continue
        end
        
        freeswitch.consoleLog("INFO", string.format(
            "[VoiceAI] User said: %s\n", user_text
        ))
        
        -- Adicionar ao histórico
        table.insert(history, {role = "user", content = user_text})
        
        -- Enviar para LLM
        local response, action = chat_with_ai(user_text, config)
        if not response then
            freeswitch.consoleLog("ERR", "[VoiceAI] Chat failed\n")
            synthesize_and_play("Desculpe, ocorreu um erro. Tente novamente.")
            goto continue
        end
        
        -- Adicionar resposta ao histórico
        table.insert(history, {role = "assistant", content = response})
        
        -- Sintetizar e reproduzir resposta
        synthesize_and_play(response)
        
        -- Processar ação
        if action then
            if action.type == "transfer" then
                do_transfer(action.destination, config)
                return
            elseif action.type == "hangup" then
                if config.farewell then
                    synthesize_and_play(config.farewell)
                end
                save_conversation("completed")
                return
            end
        end
        
        ::continue::
        
        -- Limpar arquivo de áudio
        os.remove(audio_file)
    end
    
    -- Fim do loop
    if config.farewell then
        synthesize_and_play(config.farewell)
    end
    save_conversation("max_turns")
end

--[[
  Carrega configuração da secretária do banco
  
  ⚠️ SEGURANÇA: Usa queries parametrizadas para prevenir SQL injection
--]]
function load_secretary_config()
    -- Carregar DSN do ambiente (nunca hardcode credenciais!)
    local db_host = os.getenv("FUSIONPBX_DB_HOST") or "127.0.0.1"
    local db_name = os.getenv("FUSIONPBX_DB_NAME") or "fusionpbx"
    local db_user = os.getenv("FUSIONPBX_DB_USER") or "fusionpbx"
    local db_pass = os.getenv("FUSIONPBX_DB_PASS") or ""
    
    local dsn = string.format(
        "pgsql://hostaddr=%s dbname=%s user=%s password=%s",
        db_host, db_name, db_user, db_pass
    )
    
    local dbh = freeswitch.Dbh(dsn)
    
    if not dbh:connected() then
        freeswitch.consoleLog("ERR", "[VoiceAI] Database connection failed\n")
        return nil
    end
    
    local config = nil
    
    -- ⚠️ SEGURANÇA: Validar UUIDs antes de usar (prevenir injection)
    if not validate_uuid(secretary_uuid) or not validate_uuid(domain_uuid) then
        freeswitch.consoleLog("ERR", "[VoiceAI] Invalid UUID format\n")
        dbh:release()
        return nil
    end
    
    -- Query parametrizada (FreeSWITCH Dbh suporta $1, $2 para PostgreSQL)
    -- Nota: FreeSWITCH Lua Dbh não suporta prepared statements nativamente,
    -- então usamos escape manual + validação de UUID
    local sql = string.format([[
        SELECT 
            personality_prompt,
            greeting_message,
            farewell_message,
            tts_voice_id,
            transfer_extension,
            max_turns,
            language,
            processing_mode
        FROM v_voice_secretaries 
        WHERE voice_secretary_uuid = '%s' 
          AND domain_uuid = '%s'
          AND is_enabled = true
    ]], escape_sql(secretary_uuid), escape_sql(domain_uuid))
    
    dbh:query(sql, function(row)
        config = {
            system_prompt = row.personality_prompt or "",
            greeting = row.greeting_message or "",
            farewell = row.farewell_message or "",
            voice = row.tts_voice_id or "alloy",
            transfer_extension = row.transfer_extension or "200",
            max_turns = tonumber(row.max_turns) or 20,
            language = row.language or "pt-BR",
            processing_mode = row.processing_mode or "turn_based"
        }
    end)
    
    dbh:release()
    return config
end

--[[
  Valida formato UUID (segurança)
--]]
function validate_uuid(uuid)
    if not uuid or uuid == "" then return false end
    -- UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    local pattern = "^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$"
    return string.match(uuid, pattern) ~= nil
end

--[[
  Escape SQL para prevenir injection (fallback)
--]]
function escape_sql(str)
    if not str then return "" end
    -- Remove caracteres perigosos para SQL
    return str:gsub("'", "''"):gsub("\\", "\\\\"):gsub(";", "")
end

--[[
  Grava entrada de áudio do usuário
--]]
function record_user_input()
    local filename = string.format("/tmp/voice_ai_%s_%d.wav", call_uuid, os.time())
    
    -- Gravar até silêncio ou tempo máximo
    session:recordFile(filename, MAX_RECORD_TIME, SILENCE_THRESHOLD, 3)
    
    -- Verificar se arquivo foi criado
    local file = io.open(filename, "r")
    if file then
        file:close()
        return filename
    end
    
    return nil
end

--[[
  Transcreve áudio usando voice-ai-service
--]]
function transcribe_audio(audio_file)
    -- Ler arquivo em base64
    local file = io.open(audio_file, "rb")
    if not file then return nil end
    
    local audio_data = file:read("*all")
    file:close()
    
    local audio_base64 = base64_encode(audio_data)
    
    -- Chamar API de transcrição
    local payload = json.encode({
        domain_uuid = domain_uuid,
        audio_base64 = audio_base64,
        format = "wav"
    })
    
    local response = http.post(VOICE_AI_API .. "/transcribe", payload, {
        ["Content-Type"] = "application/json"
    })
    
    if response and response.status == 200 then
        local data = json.decode(response.body)
        return data and data.text or nil
    end
    
    return nil
end

--[[
  Envia mensagem para o LLM
--]]
function chat_with_ai(message, config)
    local payload = json.encode({
        domain_uuid = domain_uuid,
        secretary_uuid = secretary_uuid,
        message = message,
        history = history,
        system_prompt = config.system_prompt
    })
    
    local response = http.post(VOICE_AI_API .. "/chat", payload, {
        ["Content-Type"] = "application/json"
    })
    
    if response and response.status == 200 then
        local data = json.decode(response.body)
        if data then
            return data.response, data.action
        end
    end
    
    return nil, nil
end

--[[
  Sintetiza texto e reproduz
  
  A API retorna audio_file (caminho no servidor FreeSWITCH)
--]]
function synthesize_and_play(text)
    if not text or text == "" then return end
    
    local payload = json.encode({
        domain_uuid = domain_uuid,
        text = text,
        voice_id = config and config.voice or "alloy"
    })
    
    local response = http.post(VOICE_AI_API .. "/synthesize", payload, {
        ["Content-Type"] = "application/json"
    })
    
    if response and response.status == 200 then
        local data = json.decode(response.body)
        if data and data.audio_file then
            -- A API retorna o caminho do arquivo de áudio
            -- Formato: /var/lib/freeswitch/sounds/voice_ai/...
            local audio_path = data.audio_file
            
            -- Verificar se arquivo existe
            local file = io.open(audio_path, "r")
            if file then
                file:close()
                
                -- Reproduzir
                session:streamFile(audio_path)
                
                -- Limpar arquivo temporário após reprodução
                os.remove(audio_path)
            else
                freeswitch.consoleLog("WARNING", 
                    string.format("[VoiceAI] Audio file not found: %s\n", audio_path))
            end
        else
            freeswitch.consoleLog("WARNING", "[VoiceAI] TTS response missing audio_file\n")
        end
    else
        freeswitch.consoleLog("ERR", 
            string.format("[VoiceAI] TTS failed: status=%s\n", 
                response and response.status or "nil"))
    end
end

--[[
  Transfere chamada
--]]
function do_transfer(destination, config)
    freeswitch.consoleLog("INFO", string.format(
        "[VoiceAI] Transferring to: %s\n", destination
    ))
    
    -- Anunciar transferência
    synthesize_and_play("Vou transferir você agora. Um momento.")
    
    -- Salvar conversa antes de transferir
    save_conversation("transferred:" .. destination)
    
    -- Executar transferência
    session:transfer(destination)
end

--[[
  Salva conversa no banco
--]]
function save_conversation(resolution)
    -- Enviar para API (que salva no banco)
    local payload = json.encode({
        domain_uuid = domain_uuid,
        secretary_uuid = secretary_uuid,
        call_uuid = call_uuid,
        caller_id = caller_id,
        resolution = resolution,
        transcript = history
    })
    
    http.post(VOICE_AI_API .. "/conversations", payload, {
        ["Content-Type"] = "application/json"
    })
end

--[[
  Reproduz mensagem de erro
--]]
function play_error_message()
    session:speak("Desculpe, o sistema está temporariamente indisponível. Tente mais tarde.")
end

--[[
  Utilitários Base64
--]]
function base64_encode(data)
    local b = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    return ((data:gsub('.', function(x)
        local r, b = '', x:byte()
        for i = 8, 1, -1 do r = r .. (b % 2 ^ i - b % 2 ^ (i - 1) > 0 and '1' or '0') end
        return r
    end) .. '0000'):gsub('%d%d%d?%d?%d?%d?', function(x)
        if #x < 6 then return '' end
        local c = 0
        for i = 1, 6 do c = c + (x:sub(i, i) == '1' and 2 ^ (6 - i) or 0) end
        return b:sub(c + 1, c + 1)
    end) .. ({'', '==', '='})[#data % 3 + 1])
end

function base64_decode(data)
    local b = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    data = string.gsub(data, '[^' .. b .. '=]', '')
    return (data:gsub('.', function(x)
        if x == '=' then return '' end
        local r, f = '', (b:find(x) - 1)
        for i = 6, 1, -1 do r = r .. (f % 2 ^ i - f % 2 ^ (i - 1) > 0 and '1' or '0') end
        return r
    end):gsub('%d%d%d?%d?%d?%d?%d?%d?', function(x)
        if #x ~= 8 then return '' end
        local c = 0
        for i = 1, 8 do c = c + (x:sub(i, i) == '1' and 2 ^ (8 - i) or 0) end
        return string.char(c)
    end))
end

-- Executar
main()
