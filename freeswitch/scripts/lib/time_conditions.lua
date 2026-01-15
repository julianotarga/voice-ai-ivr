--[[
Time Conditions Checker for Voice AI Secretary
Verifica se está dentro do horário de atendimento antes de transferir

⚠️ MULTI-TENANT: Time conditions são por domain_uuid!

Referência: openspec/changes/add-realtime-handoff-omni/design.md (Decision 7)
]]--

local time_conditions = {}

local config = require("config")

-- Configuração
local CACHE_TTL = 300  -- 5 minutos

-- Cache simples
local _cache = {}

-- Função auxiliar para log
local function log(level, message)
    if freeswitch and freeswitch.consoleLog then
        freeswitch.consoleLog(level, "[TIME_CONDITIONS] " .. message .. "\n")
    end
end

-- Limpar entradas expiradas do cache
local function cleanup_cache()
    local now = os.time()
    for key, entry in pairs(_cache) do
        if now - entry.timestamp > CACHE_TTL then
            _cache[key] = nil
        end
    end
end

-- Obter do cache
local function get_cached(key)
    cleanup_cache()
    local entry = _cache[key]
    if entry and (os.time() - entry.timestamp) <= CACHE_TTL then
        log("DEBUG", "Cache hit for: " .. key)
        return entry.value, true
    end
    return nil, false
end

-- Salvar no cache
local function set_cached(key, value)
    _cache[key] = {
        value = value,
        timestamp = os.time()
    }
end

-- Mapear dia da semana (Lua: 1=domingo, 2=segunda, ..., 7=sábado)
-- FusionPBX: 0=domingo, 1=segunda, ..., 6=sábado
local function lua_to_fusionpbx_wday(lua_wday)
    return lua_wday - 1
end

-- Verificar se horário atual está dentro de um range
local function is_time_in_range(current_hour, current_min, start_hour, start_min, end_hour, end_min)
    local current_minutes = current_hour * 60 + current_min
    local start_minutes = start_hour * 60 + start_min
    local end_minutes = end_hour * 60 + end_min
    
    return current_minutes >= start_minutes and current_minutes <= end_minutes
end

-- Parse horário no formato "HH:MM" ou "HH:MM:SS"
local function parse_time(time_str)
    if not time_str then return nil, nil end
    
    local hour, min = time_str:match("(%d+):(%d+)")
    if hour and min then
        return tonumber(hour), tonumber(min)
    end
    return nil, nil
end

--[[
Carrega regras de time condition do banco de dados.

@param time_condition_uuid string - UUID da time condition
@return table - Lista de regras { wday, time_start, time_end }
]]--
function time_conditions.load_rules(time_condition_uuid)
    if not time_condition_uuid then
        return {}
    end
    
    local cache_key = "tc:" .. time_condition_uuid
    local cached, found = get_cached(cache_key)
    if found then
        return cached
    end
    
    -- Query para buscar regras de horário
    -- FusionPBX usa v_time_condition_times
    -- Sanitizar UUID para prevenir SQL Injection
    local safe_uuid = time_condition_uuid:gsub("[^a-fA-F0-9%-]", "")
    
    if safe_uuid ~= time_condition_uuid then
        log("WARNING", "Sanitized potentially dangerous time_condition_uuid")
        return {}
    end
    
    local sql = string.format([[
        SELECT 
            time_condition_time_uuid,
            time_condition_wday,
            time_condition_time_start,
            time_condition_time_end
        FROM v_time_condition_times
        WHERE time_condition_uuid = '%s'
        ORDER BY time_condition_order ASC
    ]], safe_uuid)
    
    local result = config.query(sql)
    
    if not result or result == "" then
        -- Sem regras específicas - assumir disponível sempre
        log("DEBUG", "No time rules found for " .. time_condition_uuid)
        set_cached(cache_key, {})
        return {}
    end
    
    local rules = {}
    
    for line in result:gmatch("[^\r\n]+") do
        local parts = {}
        for part in line:gmatch("[^|]+") do
            table.insert(parts, part)
        end
        
        if #parts >= 4 then
            local rule = {
                uuid = parts[1],
                wday = parts[2],  -- pode ser número, range "1-5", ou "*"
                time_start = parts[3],
                time_end = parts[4],
            }
            table.insert(rules, rule)
            log("DEBUG", string.format("Loaded rule: wday=%s, start=%s, end=%s", 
                rule.wday, rule.time_start, rule.time_end))
        end
    end
    
    set_cached(cache_key, rules)
    log("INFO", string.format("Loaded %d time rules for %s", #rules, time_condition_uuid))
    
    return rules
end

--[[
Verifica se um dia da semana está dentro de uma regra de dias.

@param current_wday number - Dia atual (0=domingo, 6=sábado)
@param rule_wday string - Regra de dias ("*", "1", "1-5", "0,6", etc)
@return boolean
]]--
local function matches_wday(current_wday, rule_wday)
    if not rule_wday or rule_wday == "" or rule_wday == "*" then
        return true  -- Qualquer dia
    end
    
    -- Verificar se é range (ex: "1-5")
    local start_day, end_day = rule_wday:match("(%d)-(%d)")
    if start_day and end_day then
        return current_wday >= tonumber(start_day) and current_wday <= tonumber(end_day)
    end
    
    -- Verificar se é lista (ex: "0,6")
    if rule_wday:find(",") then
        for day in rule_wday:gmatch("(%d)") do
            if tonumber(day) == current_wday then
                return true
            end
        end
        return false
    end
    
    -- Dia único
    return current_wday == tonumber(rule_wday)
end

--[[
Verifica se o momento atual está dentro de uma regra específica.

@param now table - os.date("*t")
@param rule table - { wday, time_start, time_end }
@return boolean
]]--
local function matches_rule(now, rule)
    local current_wday = lua_to_fusionpbx_wday(now.wday)
    
    -- Verificar dia da semana
    if not matches_wday(current_wday, rule.wday) then
        return false
    end
    
    -- Verificar horário
    local start_hour, start_min = parse_time(rule.time_start)
    local end_hour, end_min = parse_time(rule.time_end)
    
    if not start_hour or not end_hour then
        -- Se não tem horário definido, assumir o dia todo
        return true
    end
    
    return is_time_in_range(now.hour, now.min, start_hour, start_min, end_hour, end_min)
end

--[[
Verifica se está dentro do horário de atendimento.

@param secretary table - Configuração da secretária (com time_condition_uuid)
@return boolean - true se dentro do horário ou sem restrição
@return string - motivo ("within_hours", "outside_hours", "no_time_condition", etc)
]]--
function time_conditions.is_within_business_hours(secretary)
    if not secretary then
        return true, "no_secretary"
    end
    
    local time_condition_uuid = secretary.time_condition_uuid
    
    if not time_condition_uuid or time_condition_uuid == "" then
        -- Sem time condition configurada = sempre disponível
        log("DEBUG", "No time_condition configured for secretary, allowing")
        return true, "no_time_condition"
    end
    
    -- Carregar regras
    local rules = time_conditions.load_rules(time_condition_uuid)
    
    if #rules == 0 then
        -- Sem regras = sempre disponível
        log("DEBUG", "No time rules found, allowing")
        return true, "no_rules"
    end
    
    -- Verificar momento atual
    local now = os.date("*t")
    
    log("DEBUG", string.format("Checking time: %s %02d:%02d (wday=%d)", 
        os.date("%Y-%m-%d"), now.hour, now.min, lua_to_fusionpbx_wday(now.wday)))
    
    -- Verificar cada regra
    for _, rule in ipairs(rules) do
        if matches_rule(now, rule) then
            log("INFO", "Within business hours - matched rule: " .. rule.uuid)
            return true, "within_hours"
        end
    end
    
    -- Nenhuma regra casou = fora do horário
    log("INFO", string.format("Outside business hours: %s %02d:%02d", 
        os.date("%Y-%m-%d"), now.hour, now.min))
    return false, "outside_hours"
end

--[[
Carrega exceções (feriados) para uma time condition.

@param time_condition_uuid string
@return table - Lista de datas de exceção
]]--
function time_conditions.load_exceptions(time_condition_uuid)
    if not time_condition_uuid then
        return {}
    end
    
    local cache_key = "tc_ex:" .. time_condition_uuid
    local cached, found = get_cached(cache_key)
    if found then
        return cached
    end
    
    -- FusionPBX pode armazenar exceções de diferentes formas
    -- Esta é uma implementação básica
    -- Sanitizar UUID para prevenir SQL Injection
    local safe_uuid = time_condition_uuid:gsub("[^a-fA-F0-9%-]", "")
    
    if safe_uuid ~= time_condition_uuid then
        log("WARNING", "Sanitized potentially dangerous time_condition_uuid in load_exceptions")
        return {}
    end
    
    local sql = string.format([[
        SELECT 
            exception_date,
            exception_type
        FROM v_time_condition_exceptions
        WHERE time_condition_uuid = '%s'
          AND exception_enabled = 'true'
    ]], safe_uuid)
    
    local result = config.query(sql)
    
    local exceptions = {}
    
    if result and result ~= "" then
        for line in result:gmatch("[^\r\n]+") do
            local parts = {}
            for part in line:gmatch("[^|]+") do
                table.insert(parts, part)
            end
            
            if #parts >= 2 then
                table.insert(exceptions, {
                    date = parts[1],
                    type = parts[2],  -- "closed" ou "open"
                })
            end
        end
    end
    
    set_cached(cache_key, exceptions)
    return exceptions
end

--[[
Verifica se hoje é uma exceção (feriado).

@param secretary table
@return boolean - true se é exceção de fechamento
@return string - motivo
]]--
function time_conditions.is_exception_today(secretary)
    if not secretary or not secretary.time_condition_uuid then
        return false, "no_time_condition"
    end
    
    local exceptions = time_conditions.load_exceptions(secretary.time_condition_uuid)
    
    if #exceptions == 0 then
        return false, "no_exceptions"
    end
    
    local today = os.date("%Y-%m-%d")
    
    for _, exc in ipairs(exceptions) do
        if exc.date == today then
            if exc.type == "closed" then
                log("INFO", "Today is a holiday/exception: " .. today)
                return true, "holiday"
            else
                -- Exceção de abertura especial
                return false, "special_open"
            end
        end
    end
    
    return false, "no_exception_today"
end

--[[
Verificação completa de disponibilidade por horário.
Combina time conditions e exceções.

@param secretary table
@return boolean - true se disponível
@return string - motivo detalhado
]]--
function time_conditions.check_availability(secretary)
    -- Primeiro, verificar exceções (feriados)
    local is_exception, exception_reason = time_conditions.is_exception_today(secretary)
    
    if is_exception then
        return false, "outside_business_hours:holiday"
    end
    
    -- Depois, verificar horário normal
    local is_within, time_reason = time_conditions.is_within_business_hours(secretary)
    
    if is_within then
        return true, "available:" .. time_reason
    else
        return false, "outside_business_hours:" .. time_reason
    end
end

--[[
Invalida cache de time conditions.

@param time_condition_uuid string|nil - UUID específico ou nil para limpar tudo
]]--
function time_conditions.invalidate_cache(time_condition_uuid)
    if time_condition_uuid then
        _cache["tc:" .. time_condition_uuid] = nil
        _cache["tc_ex:" .. time_condition_uuid] = nil
        log("DEBUG", "Cache invalidated for: " .. time_condition_uuid)
    else
        _cache = {}
        log("DEBUG", "Cache fully invalidated")
    end
end

return time_conditions
