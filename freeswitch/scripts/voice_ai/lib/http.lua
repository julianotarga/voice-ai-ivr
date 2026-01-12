--[[
  http.lua - Simple HTTP client for Lua
  
  Referência: .context/docs/architecture.md
  
  Usa curl para fazer requisições HTTP.
--]]

local http = {}

-- Executa comando e retorna saída
local function exec(cmd)
    local handle = io.popen(cmd)
    local result = handle:read("*a")
    local success, _, code = handle:close()
    return result, code
end

-- HTTP GET
function http.get(url, headers)
    local header_args = ""
    if headers then
        for k, v in pairs(headers) do
            header_args = header_args .. string.format(' -H "%s: %s"', k, v)
        end
    end
    
    local cmd = string.format(
        'curl -s -w "\\n%%{http_code}" %s "%s"',
        header_args, url
    )
    
    local output, code = exec(cmd)
    
    if output then
        local lines = {}
        for line in output:gmatch("[^\n]+") do
            table.insert(lines, line)
        end
        
        local status = tonumber(lines[#lines]) or 0
        table.remove(lines)
        local body = table.concat(lines, "\n")
        
        return {
            status = status,
            body = body
        }
    end
    
    return nil
end

-- HTTP POST
function http.post(url, body, headers)
    local header_args = ""
    if headers then
        for k, v in pairs(headers) do
            header_args = header_args .. string.format(' -H "%s: %s"', k, v)
        end
    end
    
    -- Escapar body para shell
    local escaped_body = body:gsub("'", "'\\''")
    
    local cmd = string.format(
        "curl -s -w '\\n%%{http_code}' -X POST %s -d '%s' '%s'",
        header_args, escaped_body, url
    )
    
    local output, code = exec(cmd)
    
    if output then
        local lines = {}
        for line in output:gmatch("[^\n]+") do
            table.insert(lines, line)
        end
        
        local status = tonumber(lines[#lines]) or 0
        table.remove(lines)
        local body_result = table.concat(lines, "\n")
        
        return {
            status = status,
            body = body_result
        }
    end
    
    return nil
end

-- HTTP PUT
function http.put(url, body, headers)
    local header_args = ""
    if headers then
        for k, v in pairs(headers) do
            header_args = header_args .. string.format(' -H "%s: %s"', k, v)
        end
    end
    
    local escaped_body = body:gsub("'", "'\\''")
    
    local cmd = string.format(
        "curl -s -w '\\n%%{http_code}' -X PUT %s -d '%s' '%s'",
        header_args, escaped_body, url
    )
    
    local output = exec(cmd)
    
    if output then
        local lines = {}
        for line in output:gmatch("[^\n]+") do
            table.insert(lines, line)
        end
        
        local status = tonumber(lines[#lines]) or 0
        table.remove(lines)
        local body_result = table.concat(lines, "\n")
        
        return {
            status = status,
            body = body_result
        }
    end
    
    return nil
end

return http
