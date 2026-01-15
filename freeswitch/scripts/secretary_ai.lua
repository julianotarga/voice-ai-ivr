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
local presence = require("presence")
local time_conditions = require("time_conditions")

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

-- ============================================
-- FALLBACK TICKET CREATION
-- Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (7.4, 9.3, 9.4)
-- ============================================

local OMNIPLAY_API_URL = os.getenv("OMNIPLAY_API_URL") or "http://host.docker.internal:8080"
local OMNIPLAY_API_TOKEN = os.getenv("OMNIPLAY_API_TOKEN") or ""

local function create_fallback_ticket(domain_uuid, caller_id, conversation_history, handoff_reason, secretary, recording_url)
    -- Criar ticket pending no OmniPlay quando transfer falha
    -- Ref: POST /api/tickets/realtime-handoff
    -- Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (12.5, 12.6)
    
    if OMNIPLAY_API_TOKEN == "" then
        log("WARNING", "OMNIPLAY_API_TOKEN not configured, skipping fallback ticket")
        return nil
    end
    
    -- Construir transcript no formato esperado
    local transcript = {}
    for i, msg in ipairs(conversation_history) do
        table.insert(transcript, {
            role = msg.role == "user" and "user" or "assistant",
            text = msg.content or "",
            timestamp = os.time() * 1000  -- milliseconds
        })
    end
    
    -- Gerar resumo simples
    local last_user_msg = ""
    for i = #conversation_history, 1, -1 do
        if conversation_history[i].role == "user" then
            last_user_msg = conversation_history[i].content or ""
            break
        end
    end
    local summary = "Conversa via voz (" .. #conversation_history .. " turnos). "
    if last_user_msg ~= "" then
        summary = summary .. "Última mensagem: \"" .. string.sub(last_user_msg, 1, 100) .. "\""
    end
    
    -- Obter domain_name para busca de gravação
    local domain_name = session:getVariable("domain_name") or session:getVariable("domain")
    
    -- Buscar URL da gravação do FreeSWITCH (se disponível)
    -- A gravação pode estar em: /var/lib/freeswitch/recordings/{domain}/{YYYY}/{MM}/{DD}/{uuid}.wav
    local call_uuid = session:getVariable("uuid")
    local recording_path = nil
    
    -- Verificar se há gravação configurada para esta chamada
    local record_path = session:getVariable("record_path")
    if record_path and record_path ~= "" then
        recording_path = record_path
    end
    
    local payload = json.encode({
        call_uuid = call_uuid,
        caller_id = caller_id,
        transcript = transcript,
        summary = summary,
        provider = "freeswitch_lua",
        language = "pt-BR",
        duration_seconds = os.time() - (session:getVariable("start_epoch") or os.time()),
        turns = #conversation_history,
        handoff_reason = handoff_reason,
        secretary_uuid = secretary.voice_secretary_uuid,
        -- Campos para anexar gravação (Seção 12)
        domain = domain_name,
        recording_url = recording_url or recording_path,
        attach_recording = true,
    })
    
    local response = http.post(OMNIPLAY_API_URL .. "/api/tickets/realtime-handoff", payload, {
        ["Content-Type"] = "application/json",
        ["Authorization"] = "Bearer " .. OMNIPLAY_API_TOKEN,
    })
    
    if response and (response.status == 200 or response.status == 201) then
        local data = json.decode(response.body)
        log("INFO", "Fallback ticket created: " .. (data.ticket_id or "unknown"))
        return data.ticket_id
    else
        log("ERROR", "Failed to create fallback ticket: " .. (response and response.status or "no response"))
        return nil
    end
end

local function attempt_transfer_with_fallback(extension, department, domain_uuid, caller_id, conversation_history, secretary, transfer_message)
    -- Tenta transferir e cria ticket se falhar
    -- Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (7.3, 7.4)
    -- Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (10.5) - presence check
    
    local secretary_id = secretary.voice_secretary_uuid
    local domain_name = session:getVariable("domain_name") or session:getVariable("domain")
    
    -- ==========================================
    -- 10.5: VERIFICAÇÃO DE PRESENÇA VIA ESL
    -- Ref: openspec/changes/add-realtime-handoff-omni/design.md (Decision 6)
    -- ==========================================
    
    local presence_check_enabled = secretary.presence_check_enabled
    if presence_check_enabled == nil then
        presence_check_enabled = true  -- Default: verificar presença
    end
    
    if presence_check_enabled and domain_name then
        log("INFO", "Checking presence for extension " .. extension .. "@" .. domain_name)
        
        local is_available, dest_type, status = presence.check_destination(extension, domain_uuid, domain_name)
        
        log("INFO", string.format("Presence check result: available=%s, type=%s, status=%s", 
            tostring(is_available), dest_type, status))
        
        if not is_available then
            -- 10.5: Ramal offline - fallback imediato para ticket
            log("WARNING", "Extension " .. extension .. " is not available (" .. status .. "), creating fallback ticket")
            
            -- Mensagem informando indisponibilidade
            local offline_msg = "O setor de " .. department .. " está indisponível no momento. " ..
                "Vou registrar sua solicitação e entraremos em contato em breve."
            play_tts(offline_msg, domain_uuid, secretary_id)
            
            -- Criar ticket no OmniPlay
            local ticket_id = create_fallback_ticket(
                domain_uuid, 
                caller_id, 
                conversation_history, 
                "extension_offline:" .. status,
                secretary,
                nil  -- recording_url - será buscado internamente
            )
            
            -- Salvar conversa com fallback
            save_conversation(
                domain_uuid, session:getVariable("uuid"), caller_id, secretary_id,
                conversation_history, "fallback_ticket:offline", extension
            )
            
            return false  -- Indicar que não transferiu
        end
    else
        if not presence_check_enabled then
            log("DEBUG", "Presence check disabled for this secretary")
        end
        if not domain_name then
            log("WARNING", "domain_name not available, skipping presence check")
        end
    end
    
    -- ==========================================
    -- 11.6: VERIFICAÇÃO DE HORÁRIO DE ATENDIMENTO (TIME CONDITIONS)
    -- Ref: openspec/changes/add-realtime-handoff-omni/design.md (Decision 7)
    -- ==========================================
    
    if secretary.time_condition_uuid then
        log("INFO", "Checking time conditions for secretary")
        
        local is_available, time_reason = time_conditions.check_availability(secretary)
        
        log("INFO", string.format("Time conditions check result: available=%s, reason=%s", 
            tostring(is_available), time_reason))
        
        if not is_available then
            -- Fora do horário - fallback imediato para ticket
            log("WARNING", "Outside business hours (" .. time_reason .. "), creating fallback ticket")
            
            -- Mensagem informando horário
            local closed_msg
            if time_reason:find("holiday") then
                closed_msg = "Hoje estamos fechados por ser feriado. " ..
                    "Vou registrar sua solicitação e entraremos em contato no próximo dia útil."
            else
                closed_msg = "Nosso horário de atendimento já encerrou. " ..
                    "Vou registrar sua solicitação e entraremos em contato em breve."
            end
            play_tts(closed_msg, domain_uuid, secretary_id)
            
            -- Criar ticket no OmniPlay
            local ticket_id = create_fallback_ticket(
                domain_uuid, 
                caller_id, 
                conversation_history, 
                time_reason,
                secretary,
                nil  -- recording_url - será buscado internamente
            )
            
            -- Salvar conversa com fallback
            save_conversation(
                domain_uuid, session:getVariable("uuid"), caller_id, secretary_id,
                conversation_history, "fallback_ticket:" .. time_reason, extension
            )
            
            return false  -- Indicar que não transferiu
        end
    else
        log("DEBUG", "No time_condition configured, skipping time check")
    end
    
    -- ==========================================
    -- PROSSEGUIR COM TRANSFER (presença OK, horário OK ou checks desabilitados)
    -- ==========================================
    
    -- 7.3: Usar transfer_message personalizada se fornecida
    local announce_msg = transfer_message
    if not announce_msg or announce_msg == "" then
        announce_msg = "Vou transferir você para " .. department .. ". Um momento."
    end
    play_tts(announce_msg, domain_uuid, secretary_id)
    
    -- Salvar conversa antes de tentar transfer
    local conv_uuid = save_conversation(
        domain_uuid, session:getVariable("uuid"), caller_id, secretary_id,
        conversation_history, "transfer_attempt", extension
    )
    
    -- Tentar bridge ao invés de transfer para ter controle do resultado
    -- Timeout de 30 segundos configurável
    local transfer_timeout = secretary.handoff_timeout or 30
    
    -- Configurar variáveis para bridge
    session:setVariable("call_timeout", tostring(transfer_timeout))
    session:setVariable("hangup_after_bridge", "false")
    
    log("INFO", "Attempting transfer to " .. extension .. " with " .. transfer_timeout .. "s timeout")
    
    -- Usar bridge com timeout
    local bridge_string = "sofia/internal/" .. extension .. "@${domain_name}"
    session:execute("set", "continue_on_fail=true")
    session:execute("bridge", bridge_string)
    
    -- Verificar resultado do bridge
    local disposition = session:getVariable("originate_disposition") or "UNKNOWN"
    local hangup_cause = session:getVariable("hangup_cause") or ""
    
    log("INFO", "Transfer result: disposition=" .. disposition .. ", hangup_cause=" .. hangup_cause)
    
    -- Se bridge foi bem-sucedido, a chamada já está conectada
    if disposition == "SUCCESS" or disposition == "ANSWER" then
        log("INFO", "Transfer successful to " .. extension)
        -- Atualizar conversa com sucesso
        save_conversation(
            domain_uuid, session:getVariable("uuid"), caller_id, secretary_id,
            conversation_history, "transfer_success", extension
        )
        return true
    end
    
    -- Transfer falhou - verificar se sessão ainda está ativa
    if not session:ready() then
        log("INFO", "Session ended during transfer attempt")
        return false
    end
    
    -- 7.4 & 9.3: Fallback para ticket
    log("WARNING", "Transfer failed with disposition: " .. disposition .. " - creating fallback ticket")
    
    -- 9.4: Mensagem de despedida no fallback
    local fallback_msg = "Não foi possível transferir sua chamada no momento. " ..
        "Vou registrar sua solicitação e entraremos em contato em breve. " ..
        "Tenha um bom dia!"
    play_tts(fallback_msg, domain_uuid, secretary_id)
    
    -- Criar ticket no OmniPlay
    local ticket_id = create_fallback_ticket(
        domain_uuid, 
        caller_id, 
        conversation_history, 
        "transfer_failed:" .. disposition,
        secretary,
        nil  -- recording_url - será buscado internamente
    )
    
    -- Atualizar conversa com fallback
    save_conversation(
        domain_uuid, session:getVariable("uuid"), caller_id, secretary_id,
        conversation_history, "fallback_ticket", extension
    )
    
    -- Enviar webhook para OmniPlay
    if conv_uuid and secretary.webhook_url then
        send_omniplay_webhook(domain_uuid, {
            conversation_uuid = conv_uuid,
            caller_id = caller_id,
            final_action = "fallback_ticket",
            transfer_target = extension,
            ticket_id = ticket_id,
            duration_seconds = os.time() - (session:getVariable("start_epoch") or os.time()),
            messages = conversation_history,
        }, secretary)
    end
    
    return false
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
            local transfer_message = response.transfer_message  -- 7.3: mensagem personalizada
            
            log("INFO", "Transferring to " .. extension .. " (" .. department .. ")")
            
            -- Usar função com fallback para ticket
            -- Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (7.3, 7.4)
            local success = attempt_transfer_with_fallback(
                extension,
                department,
                domain_uuid,
                caller_id_number,
                conversation_history,
                secretary,
                transfer_message
            )
            
            if success then
                -- Transfer bem-sucedido, chamada já está conectada
                log("INFO", "Transfer completed successfully")
            else
                -- Fallback já tratado pela função, encerrar
                log("INFO", "Transfer failed, fallback ticket created")
            end
            
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
    -- Ref: openspec/changes/add-realtime-handoff-omni/tasks.md (7.4)
    if session:ready() then
        log("INFO", "Max turns reached, attempting transfer with fallback")
        
        local fallback_extension = secretary.transfer_extension or "200"
        
        -- Usar função com fallback para ticket
        local success = attempt_transfer_with_fallback(
            fallback_extension,
            "atendimento",
            domain_uuid,
            caller_id_number,
            conversation_history,
            secretary,
            "Vou transferir você para um atendente. Um momento."
        )
        
        if not success and session:ready() then
            -- Se fallback ticket foi criado, encerrar chamada
            session:hangup("NORMAL_CLEARING")
        end
    end
    
    log("INFO", "Call ended for " .. caller_id_number)
    
else
    log("ERROR", "Session not ready")
end
