--[[
Configuration Loader for Voice AI Secretary
Loads secretary configuration from PostgreSQL database

⚠️ MULTI-TENANT: SEMPRE filtrar por domain_uuid!
]]--

local config = {}

-- Database connection (usando API do FusionPBX ou conexão direta)
local DB_HOST = "localhost"
local DB_PORT = 5432
local DB_NAME = "fusionpbx"
local DB_USER = "fusionpbx"
local DB_PASS = ""  -- Carregar de variável de ambiente

-- Função para executar query SQL
local function query(sql)
    -- Em produção, usar ODBC ou freeswitch.Dbh
    -- Esta é uma implementação simplificada usando psql via popen
    
    local cmd = string.format(
        'PGPASSWORD="%s" psql -h %s -p %d -U %s -d %s -t -A -F"|" -c "%s"',
        DB_PASS, DB_HOST, DB_PORT, DB_USER, DB_NAME, sql
    )
    
    local handle = io.popen(cmd)
    if not handle then
        return nil
    end
    
    local output = handle:read("*a")
    handle:close()
    
    return output
end

-- Exportar função de query para uso por outras libs
config.query = query

-- Carregar configuração da secretária
-- ⚠️ MULTI-TENANT: SEMPRE filtrar por domain_uuid!
function config.load_secretary(domain_uuid)
    if not domain_uuid then
        error("domain_uuid is required for multi-tenant isolation!")
    end
    
    local sql = string.format([[
        SELECT 
            voice_secretary_uuid,
            secretary_name,
            company_name,
            personality_prompt,
            greeting_message,
            farewell_message,
            fallback_message,
            transfer_extension,
            max_turns,
            enabled,
            COALESCE(presence_check_enabled, true) as presence_check_enabled,
            COALESCE(handoff_timeout, 30) as handoff_timeout,
            time_condition_uuid,
            webhook_url
        FROM v_voice_secretaries
        WHERE domain_uuid = '%s'
          AND enabled = true
        ORDER BY insert_date ASC
        LIMIT 1
    ]], domain_uuid)
    
    local result = query(sql)
    if not result or result == "" then
        return nil
    end
    
    -- Parse result (pipe-separated)
    local parts = {}
    for part in result:gmatch("[^|]+") do
        table.insert(parts, part)
    end
    
    if #parts < 10 then
        return nil
    end
    
    return {
        voice_secretary_uuid = parts[1],
        secretary_name = parts[2],
        company_name = parts[3],
        personality_prompt = parts[4],
        greeting_message = parts[5],
        farewell_message = parts[6],
        fallback_message = parts[7],
        transfer_extension = parts[8],
        max_turns = tonumber(parts[9]) or 20,
        enabled = parts[10] == "t" or parts[10] == "true",
        presence_check_enabled = parts[11] == "t" or parts[11] == "true",
        handoff_timeout = tonumber(parts[12]) or 30,
        time_condition_uuid = parts[13] ~= "" and parts[13] or nil,
        webhook_url = parts[14] ~= "" and parts[14] or nil,
    }
end

-- Carregar regras de transferência
-- ⚠️ MULTI-TENANT: Implicitamente filtrado via secretary_uuid
function config.load_transfer_rules(secretary_uuid)
    if not secretary_uuid then
        return {}
    end
    
    local sql = string.format([[
        SELECT 
            transfer_rule_uuid,
            intent_keywords,
            department_name,
            transfer_extension,
            priority
        FROM v_voice_transfer_rules
        WHERE voice_secretary_uuid = '%s'
          AND enabled = true
        ORDER BY priority ASC
    ]], secretary_uuid)
    
    local result = query(sql)
    if not result or result == "" then
        return {}
    end
    
    local rules = {}
    for line in result:gmatch("[^\r\n]+") do
        local parts = {}
        for part in line:gmatch("[^|]+") do
            table.insert(parts, part)
        end
        
        if #parts >= 4 then
            table.insert(rules, {
                transfer_rule_uuid = parts[1],
                intent_keywords = parts[2],  -- Array serializado
                department_name = parts[3],
                transfer_extension = parts[4],
                priority = tonumber(parts[5]) or 0,
            })
        end
    end
    
    return rules
end

return config
