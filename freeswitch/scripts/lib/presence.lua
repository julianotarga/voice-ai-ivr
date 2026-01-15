--[[
Presence Checker for Voice AI Secretary
Verifica se ramais estão online antes de transferir

⚠️ MULTI-TENANT: Sempre usar domain_name nas consultas!

Referência: openspec/changes/add-realtime-handoff-omni/design.md (Decision 6)
]]--

local presence = {}

-- Configuração
local PRESENCE_CHECK_TIMEOUT = 2000  -- ms
local CACHE_TTL = 30  -- segundos

-- Cache simples para evitar consultas repetidas
local _cache = {}

-- Função auxiliar para log
local function log(level, message)
    if freeswitch and freeswitch.consoleLog then
        freeswitch.consoleLog(level, "[PRESENCE] " .. message .. "\n")
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

--[[
Verifica se uma extensão está registrada no FreeSWITCH.

@param extension string - Número da extensão (ex: "1000")
@param domain_name string - Nome do domínio (ex: "empresa.com")
@return boolean - true se online, false se offline
@return string - status detalhado ("registered", "not_registered", "error")
]]--
function presence.check_extension(extension, domain_name)
    if not extension or not domain_name then
        log("ERROR", "check_extension: extension and domain_name are required")
        return false, "invalid_params"
    end
    
    local cache_key = "ext:" .. extension .. "@" .. domain_name
    local cached, found = get_cached(cache_key)
    if found then
        return cached.is_online, cached.status
    end
    
    -- Usar API do FreeSWITCH para verificar registro
    local api = freeswitch.API()
    if not api then
        log("ERROR", "Failed to get FreeSWITCH API handle")
        return true, "api_error"  -- Fallback: assumir online para não bloquear
    end
    
    -- sofia_contact formato correto: profile/user@domain
    -- Ref: https://freeswitch.org/confluence/display/FREESWITCH/Function+sofia_contact
    -- Tentar com profile "internal" (padrão FusionPBX) e fallback sem profile
    local contact_with_profile = string.format("internal/%s@%s", extension, domain_name)
    local contact_without_profile = string.format("%s@%s", extension, domain_name)
    
    local result = api:execute("sofia_contact", contact_with_profile)
    
    -- Se falhar com profile, tentar sem
    if not result or result == "" or result:find("^error") then
        result = api:execute("sofia_contact", contact_without_profile)
    end
    
    local is_online = false
    local status = "not_registered"
    
    if result and result ~= "" then
        result = result:gsub("^%s*(.-)%s*$", "%1")  -- trim
        
        if result:find("^error") or result:find("^user_not_registered") then
            is_online = false
            status = "not_registered"
            log("INFO", string.format("Extension %s@%s is OFFLINE", extension, domain_name))
        else
            is_online = true
            status = "registered"
            log("INFO", string.format("Extension %s@%s is ONLINE: %s", extension, domain_name, result))
        end
    else
        is_online = false
        status = "not_registered"
        log("INFO", string.format("Extension %s@%s is OFFLINE (empty result)", extension, domain_name))
    end
    
    -- Salvar no cache
    set_cached(cache_key, { is_online = is_online, status = status })
    
    return is_online, status
end

--[[
Verifica se pelo menos um membro de um ring-group está online.

@param rg_extension string - Extensão do ring-group (ex: "1000")
@param domain_uuid string - UUID do domínio
@param domain_name string - Nome do domínio
@return boolean - true se pelo menos um membro está online
@return number - quantidade de membros online
@return number - total de membros
]]--
function presence.check_ring_group(rg_extension, domain_uuid, domain_name)
    if not rg_extension or not domain_uuid then
        log("ERROR", "check_ring_group: rg_extension and domain_uuid are required")
        return false, 0, 0
    end
    
    local cache_key = "rg:" .. rg_extension .. "@" .. domain_uuid
    local cached, found = get_cached(cache_key)
    if found then
        return cached.has_online, cached.online_count, cached.total_count
    end
    
    -- Buscar membros do ring-group via banco
    local config = require("lib.config")
    
    -- Query para buscar destinos do ring-group
    -- Nota: Sanitizar inputs para prevenir SQL Injection
    -- domain_uuid deve ser UUID válido, rg_extension deve ser apenas dígitos
    local safe_domain_uuid = domain_uuid:gsub("[^a-fA-F0-9%-]", "")
    local safe_rg_extension = rg_extension:gsub("[^0-9*#]", "")
    
    if safe_domain_uuid ~= domain_uuid or safe_rg_extension ~= rg_extension then
        log("WARNING", "Sanitized potentially dangerous input in check_ring_group")
    end
    
    local sql = string.format([[
        SELECT ring_group_destination 
        FROM v_ring_group_destinations rgd
        JOIN v_ring_groups rg ON rg.ring_group_uuid = rgd.ring_group_uuid
        WHERE rg.domain_uuid = '%s'
          AND rg.ring_group_extension = '%s'
          AND rg.ring_group_enabled = 'true'
    ]], safe_domain_uuid, safe_rg_extension)
    
    -- Usar função de query do config
    local api = freeswitch.API()
    local db_result = api:execute("lua", string.format("return require('lib.config').query([[%s]])", sql))
    
    -- Fallback: se não conseguir verificar, assumir que está ok
    if not db_result or db_result == "" then
        log("WARNING", "Could not query ring-group members, assuming online")
        return true, 1, 1
    end
    
    -- Parse destinos e verificar cada um
    local total_count = 0
    local online_count = 0
    
    for dest in db_result:gmatch("[^\r\n]+") do
        dest = dest:gsub("^%s*(.-)%s*$", "%1")  -- trim
        if dest and dest ~= "" then
            total_count = total_count + 1
            
            -- Extrair extensão do destino (pode ser "1001", "user/1001@domain", etc)
            local ext = dest:match("^(%d+)") or dest:match("user/(%d+)@")
            if ext then
                local is_online, _ = presence.check_extension(ext, domain_name)
                if is_online then
                    online_count = online_count + 1
                end
            end
        end
    end
    
    local has_online = online_count > 0
    
    log("INFO", string.format("Ring-group %s: %d/%d members online", 
        rg_extension, online_count, total_count))
    
    -- Salvar no cache
    set_cached(cache_key, { 
        has_online = has_online, 
        online_count = online_count, 
        total_count = total_count 
    })
    
    return has_online, online_count, total_count
end

--[[
Verifica presença de forma genérica.
Detecta automaticamente se é extensão simples, ring-group ou fila.

@param destination string - Destino (extensão, ring-group, fila)
@param domain_uuid string - UUID do domínio
@param domain_name string - Nome do domínio
@return boolean - true se disponível para receber chamadas
@return string - tipo detectado ("extension", "ring_group", "queue", "unknown")
@return string - status detalhado
]]--
function presence.check_destination(destination, domain_uuid, domain_name)
    if not destination or not domain_name then
        log("ERROR", "check_destination: destination and domain_name are required")
        return true, "unknown", "invalid_params"  -- Fallback: permitir
    end
    
    log("DEBUG", string.format("Checking destination: %s@%s", destination, domain_name))
    
    -- Primeiro, verificar se é uma extensão simples
    local is_online, status = presence.check_extension(destination, domain_name)
    
    if is_online then
        return true, "extension", status
    end
    
    -- Se não está registrado como extensão, pode ser ring-group ou queue
    -- Verificar se existe um ring-group com essa extensão
    if domain_uuid then
        local has_online, online_count, total_count = presence.check_ring_group(destination, domain_uuid, domain_name)
        
        if total_count > 0 then
            -- É um ring-group
            if has_online then
                return true, "ring_group", string.format("%d/%d online", online_count, total_count)
            else
                return false, "ring_group", "all_offline"
            end
        end
    end
    
    -- Verificar se é uma fila de call center
    -- Para filas, normalmente assumimos que está disponível se existir
    local cache_key = "queue:" .. destination .. "@" .. domain_uuid
    local cached, found = get_cached(cache_key)
    if found then
        return cached.is_available, "queue", cached.status
    end
    
    -- Query para verificar se é uma fila
    local api = freeswitch.API()
    local queue_result = api:execute("callcenter_config", "queue list")
    
    if queue_result and queue_result:find(destination) then
        -- É uma fila - verificar se tem agentes disponíveis
        local agents_result = api:execute("callcenter_config", 
            string.format("queue list agents %s@%s", destination, domain_name))
        
        local has_agents = agents_result and agents_result:find("Waiting")
        
        set_cached(cache_key, { is_available = has_agents, status = has_agents and "agents_available" or "no_agents" })
        
        return has_agents, "queue", has_agents and "agents_available" or "no_agents"
    end
    
    -- Não conseguimos determinar - fallback para permitir
    log("WARNING", string.format("Could not determine destination type for %s, allowing transfer", destination))
    return true, "unknown", "fallback_allow"
end

--[[
Invalida o cache para um destino específico ou todo o cache.

@param destination string|nil - Destino para invalidar, ou nil para limpar tudo
@param domain string|nil - Domínio do destino
]]--
function presence.invalidate_cache(destination, domain)
    if destination and domain then
        local key = "ext:" .. destination .. "@" .. domain
        _cache[key] = nil
        log("DEBUG", "Cache invalidated for: " .. key)
    else
        _cache = {}
        log("DEBUG", "Cache fully invalidated")
    end
end

--[[
Retorna estatísticas do cache.

@return table - Estatísticas { size, oldest_entry, newest_entry }
]]--
function presence.get_cache_stats()
    cleanup_cache()
    
    local count = 0
    local oldest = os.time()
    local newest = 0
    
    for _, entry in pairs(_cache) do
        count = count + 1
        if entry.timestamp < oldest then oldest = entry.timestamp end
        if entry.timestamp > newest then newest = entry.timestamp end
    end
    
    return {
        size = count,
        oldest_entry = count > 0 and os.date("%Y-%m-%d %H:%M:%S", oldest) or nil,
        newest_entry = count > 0 and os.date("%Y-%m-%d %H:%M:%S", newest) or nil,
        ttl_seconds = CACHE_TTL
    }
end

return presence
