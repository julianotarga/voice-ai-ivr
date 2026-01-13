--[[
Secretária Virtual com IA - Script Principal
FreeSWITCH mod_lua

⚠️ MULTI-TENANT: SEMPRE usar domain_uuid em TODAS as operações!
]]--

-- Carregar bibliotecas auxiliares
package.path = package.path .. ";/usr/share/freeswitch/scripts/lib/?.lua"

local http = require("http")
local json = require("json")
local config = require("config")
local utils = require("utils")

-- Configurações
-- Preferir variável de ambiente para compatibilidade com diferentes deploys (docker/systemd)
-- Default alinhado ao docker-compose: 8100
local AI_SERVICE_URL = os.getenv("VOICE_AI_URL") or "http://127.0.0.1:8100/api/v1"
local MAX_TURNS = 20
local SILENCE_TIMEOUT = 3  -- segundos
local MAX_RECORDING = 30   -- segundos

-- ============================================
-- FUNÇÕES AUXILIARES
-- ============================================

local function log(level, message)
    freeswitch.consoleLog(level, "[SECRETARY_AI] " .. message .. "\n")
end

local function get_domain_uuid()
    -- MULTI-TENANT: SEMPRE obter domain_uuid da sessão
    local domain_uuid = session:getVariable("domain_uuid")
    if not domain_uuid or domain_uuid == "" then
        log("ERROR", "domain_uuid not found! Multi-tenant isolation violated!")
        return nil
    end
    return domain_uuid
end

local function transcribe(audio_file, domain_uuid)
    -- Chamar serviço Python para transcrição (STT)
    local payload = json.encode({
        domain_uuid = domain_uuid,
        audio_file = audio_file,
        language = "pt",
    })
    
    local response = http.post(AI_SERVICE_URL .. "/transcribe", payload)
    if response and response.status == 200 then
        local data = json.decode(response.body)
        return data.text
    else
        log("ERROR", "Transcription failed: " .. (response and response.status or "no response"))
        return nil
    end
end

local function synthesize(text, domain_uuid, secretary_id)
    -- Chamar serviço Python para síntese (TTS)
    local payload = json.encode({
        domain_uuid = domain_uuid,
        text = text,
    })
    
    local response = http.post(AI_SERVICE_URL .. "/synthesize", payload)
    if response and response.status == 200 then
        local data = json.decode(response.body)
        return data.audio_file
    else
        log("ERROR", "Synthesis failed: " .. (response and response.status or "no response"))
        return nil
    end
end

local function chat(user_message, domain_uuid, secretary_id, session_id, history)
    -- Chamar serviço Python para processar com IA
    local payload = json.encode({
        domain_uuid = domain_uuid,
        secretary_id = secretary_id,
        session_id = session_id,
        user_message = user_message,
        conversation_history = history,
        use_rag = true,
    })
    
    local response = http.post(AI_SERVICE_URL .. "/chat", payload)
    if response and response.status == 200 then
        local data = json.decode(response.body)
        return data
    else
        log("ERROR", "Chat failed: " .. (response and response.status or "no response"))
        return nil
    end
end

local function play_tts(text, domain_uuid, secretary_id)
    -- Sintetizar e reproduzir áudio
    local audio_file = synthesize(text, domain_uuid, secretary_id)
    if audio_file then
        session:streamFile(audio_file)
        -- Limpar arquivo temporário
        os.remove(audio_file)
    else
        log("ERROR", "Failed to synthesize: " .. text)
    end
end

local function save_conversation(domain_uuid, session_id, caller_id, secretary_id, history, final_action, transfer_target)
    -- Salvar conversa no banco via serviço Python
    local payload = json.encode({
        domain_uuid = domain_uuid,
        session_id = session_id,
        caller_id = caller_id,
        secretary_uuid = secretary_id,
        messages = history,
        final_action = final_action,
        transfer_target = transfer_target,
    })
    
    local response = http.post(AI_SERVICE_URL .. "/conversations", payload)
    if response and response.status == 200 then
        local data = json.decode(response.body)
        log("INFO", "Conversation saved: " .. (data.conversation_uuid or "unknown"))
        return data.conversation_uuid
    else
        log("ERROR", "Failed to save conversation: " .. (response and response.status or "no response"))
        return nil
    end
end

local function send_omniplay_webhook(domain_uuid, conversation_data, secretary)
    -- Enviar webhook para OmniPlay se configurado
    if not secretary.webhook_url or secretary.webhook_url == "" then
        return
    end
    
    local payload = json.encode({
        event = "voice_ai_conversation",
        domain_uuid = domain_uuid,
        conversation_uuid = conversation_data.conversation_uuid,
        caller_id = conversation_data.caller_id,
        secretary_name = secretary.secretary_name,
        summary = conversation_data.summary or "",
        action = conversation_data.final_action,
        transfer_target = conversation_data.transfer_target,
        duration_seconds = conversation_data.duration_seconds,
        messages = conversation_data.messages,
        timestamp = os.date("!%Y-%m-%dT%H:%M:%SZ"),
    })
    
    local response = http.post(secretary.webhook_url, payload, {
        ["Content-Type"] = "application/json",
        ["X-Webhook-Source"] = "voice-ai-secretary",
    })
    
    if response and response.status >= 200 and response.status < 300 then
        log("INFO", "OmniPlay webhook sent successfully")
    else
        log("ERROR", "Failed to send webhook: " .. (response and response.status or "no response"))
    end
end

local function record_audio(session_id, turn)
    -- Gravar áudio do cliente
    local recording_path = "/tmp/voice-ai/call_" .. session_id .. "_" .. turn .. ".wav"
    
    -- Configurar parâmetros de gravação
    -- record <path> <time_limit_secs> <silence_thresh> <silence_hits>
    session:execute("record", recording_path .. " " .. MAX_RECORDING .. " 40 " .. SILENCE_TIMEOUT)
    
    return recording_path
end

-- ============================================
-- FLUXO PRINCIPAL
-- ============================================

-- Verificar se a sessão está pronta
if session:ready() then
    -- Atender a chamada
    session:answer()
    session:sleep(500)  -- Pequena pausa para estabilizar
    
    -- MULTI-TENANT: Obter domain_uuid (OBRIGATÓRIO)
    local domain_uuid = get_domain_uuid()
    if not domain_uuid then
        log("ERROR", "Cannot proceed without domain_uuid! Hanging up.")
        session:hangup("NORMAL_TEMPORARY_FAILURE")
        return
    end
    
    -- Obter informações da chamada
    local session_id = session:getVariable("uuid")
    local caller_id_number = session:getVariable("caller_id_number")
    local caller_id_name = session:getVariable("caller_id_name") or ""
    local call_start_time = os.time()
    
    log("INFO", "New call from " .. caller_id_number .. " in domain " .. domain_uuid)
    
    -- Carregar configuração da secretária (MULTI-TENANT: filtrar por domain_uuid)
    local secretary = config.load_secretary(domain_uuid)
    if not secretary then
        log("ERROR", "No secretary configured for domain " .. domain_uuid)
        session:hangup("UNALLOCATED_NUMBER")
        return
    end
    
    local secretary_id = secretary.voice_secretary_uuid
    local conversation_history = {}
    
    -- Reproduzir saudação inicial
    log("INFO", "Playing greeting...")
    play_tts(secretary.greeting_message or "Olá! Como posso ajudar?", domain_uuid, secretary_id)
    
    -- Loop de conversa
    for turn = 1, (secretary.max_turns or MAX_TURNS) do
        log("INFO", "Turn " .. turn .. " starting...")
        
        -- Gravar fala do cliente
        local recording = record_audio(session_id, turn)
        
        -- Verificar se a sessão ainda está ativa
        if not session:ready() then
            log("INFO", "Session ended by caller")
            break
        end
        
        -- Transcrever áudio
        local transcript = transcribe(recording, domain_uuid)
        
        -- Limpar arquivo de gravação
        os.remove(recording)
        
        if not transcript or transcript == "" then
            -- Silêncio detectado
            play_tts("Você ainda está aí? Posso ajudar com mais alguma coisa?", domain_uuid, secretary_id)
            goto continue
        end
        
        log("INFO", "User said: " .. transcript)
        
        -- Adicionar ao histórico
        table.insert(conversation_history, {role = "user", content = transcript})
        
        -- Processar com IA
        local response = chat(transcript, domain_uuid, secretary_id, session_id, conversation_history)
        
        if not response then
            play_tts("Desculpe, houve um erro. Vou transferir você para um atendente.", domain_uuid, secretary_id)
            session:execute("transfer", (secretary.transfer_extension or "200") .. " XML default")
            break
        end
        
        log("INFO", "AI response: " .. response.text .. " (action: " .. response.action .. ")")
        
        -- Adicionar resposta ao histórico
        table.insert(conversation_history, {role = "assistant", content = response.text})
        
        -- Reproduzir resposta
        play_tts(response.text, domain_uuid, secretary_id)
        
        -- Verificar ação
        if response.action == "transfer" then
            local extension = response.transfer_extension or secretary.transfer_extension or "200"
            local department = response.transfer_department or "atendimento"
            
            log("INFO", "Transferring to " .. extension .. " (" .. department .. ")")
            play_tts("Vou transferir você para " .. department .. ". Um momento.", domain_uuid, secretary_id)
            
            -- Salvar conversa no banco
            local conv_uuid = save_conversation(
                domain_uuid, session_id, caller_id_number, secretary_id,
                conversation_history, "transfer", extension
            )
            
            -- Enviar webhook para OmniPlay se configurado
            if conv_uuid then
                send_omniplay_webhook(domain_uuid, {
                    conversation_uuid = conv_uuid,
                    caller_id = caller_id_number,
                    final_action = "transfer",
                    transfer_target = extension,
                    duration_seconds = os.time() - call_start_time,
                    messages = conversation_history,
                }, secretary)
            end
            
            session:execute("transfer", extension .. " XML default")
            break
            
        elseif response.action == "hangup" then
            log("INFO", "Ending call")
            play_tts(secretary.farewell_message or "Foi um prazer ajudar! Até logo!", domain_uuid, secretary_id)
            
            -- Salvar conversa no banco
            local conv_uuid = save_conversation(
                domain_uuid, session_id, caller_id_number, secretary_id,
                conversation_history, "hangup", nil
            )
            
            -- Enviar webhook para OmniPlay se configurado
            if conv_uuid then
                send_omniplay_webhook(domain_uuid, {
                    conversation_uuid = conv_uuid,
                    caller_id = caller_id_number,
                    final_action = "hangup",
                    transfer_target = nil,
                    duration_seconds = os.time() - call_start_time,
                    messages = conversation_history,
                }, secretary)
            end
            
            session:hangup("NORMAL_CLEARING")
            break
        end
        
        ::continue::
    end
    
    -- Se atingiu limite de turnos, transferir para fallback
    if session:ready() then
        log("INFO", "Max turns reached, transferring to fallback")
        play_tts("Vou transferir você para um atendente. Um momento.", domain_uuid, secretary_id)
        
        local fallback_extension = secretary.transfer_extension or "200"
        
        -- Salvar conversa no banco
        save_conversation(
            domain_uuid, session_id, caller_id_number, secretary_id,
            conversation_history, "max_turns", fallback_extension
        )
        
        session:execute("transfer", fallback_extension .. " XML default")
    end
    
    log("INFO", "Call ended for " .. caller_id_number)
    
else
    log("ERROR", "Session not ready")
end
