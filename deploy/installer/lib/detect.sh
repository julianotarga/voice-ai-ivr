#!/bin/bash
# =============================================================================
# Voice AI IVR - Installer Detection Functions
# =============================================================================
# Funções para detecção automática do ambiente FusionPBX/FreeSWITCH
# =============================================================================

# Variáveis detectadas (serão preenchidas pelas funções)
FUSIONPBX_PATH=""
FUSIONPBX_VERSION=""
FREESWITCH_PATH=""
FREESWITCH_CONF=""
DB_HOST=""
DB_PORT=""
DB_NAME=""
DB_USER=""
DB_PASS=""
ESL_HOST=""
ESL_PORT=""
ESL_PASSWORD=""
VOICE_AI_INSTALLED=""
VOICE_AI_VERSION=""

# =============================================================================
# Detecção de FusionPBX
# =============================================================================

detect_fusionpbx() {
    log_step "Detectando FusionPBX..."
    
    # Caminhos comuns do FusionPBX
    local paths=(
        "/var/www/fusionpbx"
        "/usr/share/fusionpbx"
        "/opt/fusionpbx"
    )
    
    for path in "${paths[@]}"; do
        if [[ -f "$path/resources/config.php" ]]; then
            FUSIONPBX_PATH="$path"
            log_success "FusionPBX encontrado: $FUSIONPBX_PATH"
            break
        fi
    done
    
    # Tentar encontrar via find se não achou nos caminhos padrão
    if [[ -z "$FUSIONPBX_PATH" ]]; then
        local found=$(find /var/www /opt /usr -name "config.php" -path "*/fusionpbx/*" -type f 2>/dev/null | head -1)
        if [[ -n "$found" ]]; then
            FUSIONPBX_PATH=$(dirname "$(dirname "$found")")
            log_success "FusionPBX encontrado: $FUSIONPBX_PATH"
        fi
    fi
    
    if [[ -z "$FUSIONPBX_PATH" ]]; then
        log_error "FusionPBX não encontrado"
        log_info "Verifique se o FusionPBX está instalado ou use --fusionpbx-path=/caminho"
        return 1
    fi
    
    # Detectar versão
    if [[ -f "$FUSIONPBX_PATH/app_config.php" ]]; then
        FUSIONPBX_VERSION=$(grep -oP "(?<=\['version'\].*=.*')[^']*" "$FUSIONPBX_PATH/app_config.php" 2>/dev/null | head -1)
    fi
    
    if [[ -z "$FUSIONPBX_VERSION" ]]; then
        # Tentar via upgrade_data.php
        if [[ -f "$FUSIONPBX_PATH/core/upgrade/upgrade_data_types.php" ]]; then
            FUSIONPBX_VERSION=$(grep -oP "(?<=version.*')[^']*" "$FUSIONPBX_PATH/core/upgrade/upgrade_data_types.php" 2>/dev/null | head -1)
        fi
    fi
    
    if [[ -n "$FUSIONPBX_VERSION" ]]; then
        log_debug "Versão do FusionPBX: $FUSIONPBX_VERSION"
        
        # Validar versão mínima (5.x)
        local major_version="${FUSIONPBX_VERSION%%.*}"
        if [[ "$major_version" -lt 5 ]]; then
            log_warn "Versão do FusionPBX ($FUSIONPBX_VERSION) pode não ser compatível"
            log_warn "Versão recomendada: 5.x ou superior"
        fi
    else
        log_debug "Não foi possível detectar a versão do FusionPBX"
    fi
    
    return 0
}

# =============================================================================
# Detecção de Credenciais do Banco de Dados
# =============================================================================

detect_database_credentials() {
    log_step "Detectando credenciais do banco de dados..."
    
    local config_file="$FUSIONPBX_PATH/resources/config.php"
    
    if [[ ! -f "$config_file" ]]; then
        log_error "Arquivo de configuração não encontrado: $config_file"
        return 1
    fi
    
    # Extrair credenciais do config.php
    # Formato: $db_host = 'localhost';
    DB_HOST=$(grep -oP "(?<=\\\$db_host.*=.*['\"])[^'\"]*" "$config_file" 2>/dev/null | head -1)
    DB_PORT=$(grep -oP "(?<=\\\$db_port.*=.*['\"])[^'\"]*" "$config_file" 2>/dev/null | head -1)
    DB_NAME=$(grep -oP "(?<=\\\$db_name.*=.*['\"])[^'\"]*" "$config_file" 2>/dev/null | head -1)
    DB_USER=$(grep -oP "(?<=\\\$db_username.*=.*['\"])[^'\"]*" "$config_file" 2>/dev/null | head -1)
    DB_PASS=$(grep -oP "(?<=\\\$db_password.*=.*['\"])[^'\"]*" "$config_file" 2>/dev/null | head -1)
    
    # Valores padrão
    DB_HOST="${DB_HOST:-localhost}"
    DB_PORT="${DB_PORT:-5432}"
    DB_NAME="${DB_NAME:-fusionpbx}"
    
    if [[ -z "$DB_USER" ]] || [[ -z "$DB_PASS" ]]; then
        log_error "Não foi possível extrair credenciais do banco de dados"
        log_info "Verifique o arquivo: $config_file"
        return 1
    fi
    
    log_success "Banco de dados: $DB_NAME@$DB_HOST:$DB_PORT"
    log_debug "Usuário do banco: $DB_USER"
    
    return 0
}

# =============================================================================
# Detecção de FreeSWITCH
# =============================================================================

detect_freeswitch() {
    log_step "Detectando FreeSWITCH..."
    
    # Verificar se FreeSWITCH está rodando
    if ! systemctl is-active --quiet freeswitch; then
        log_error "FreeSWITCH não está rodando"
        log_info "Inicie o FreeSWITCH: systemctl start freeswitch"
        return 1
    fi
    
    log_success "FreeSWITCH está rodando"
    
    # Detectar caminho de configuração
    local paths=(
        "/etc/freeswitch"
        "/usr/local/freeswitch/conf"
        "/opt/freeswitch/conf"
    )
    
    for path in "${paths[@]}"; do
        if [[ -d "$path" ]] && [[ -f "$path/vars.xml" || -f "$path/freeswitch.xml" ]]; then
            FREESWITCH_CONF="$path"
            log_success "Configuração do FreeSWITCH: $FREESWITCH_CONF"
            break
        fi
    done
    
    if [[ -z "$FREESWITCH_CONF" ]]; then
        log_warn "Diretório de configuração do FreeSWITCH não encontrado"
    fi
    
    # Detectar binário
    if command -v freeswitch &>/dev/null; then
        FREESWITCH_PATH=$(which freeswitch)
        log_debug "Binário do FreeSWITCH: $FREESWITCH_PATH"
    fi
    
    return 0
}

# =============================================================================
# Detecção de ESL (Event Socket)
# =============================================================================

detect_esl_config() {
    log_step "Detectando configuração ESL..."
    
    local esl_conf=""
    
    # Procurar event_socket.conf.xml
    if [[ -n "$FREESWITCH_CONF" ]]; then
        esl_conf=$(find "$FREESWITCH_CONF" -name "event_socket.conf.xml" 2>/dev/null | head -1)
    fi
    
    if [[ -z "$esl_conf" ]]; then
        esl_conf=$(find /etc/freeswitch /usr/local/freeswitch -name "event_socket.conf.xml" 2>/dev/null | head -1)
    fi
    
    if [[ -z "$esl_conf" ]]; then
        log_warn "Arquivo event_socket.conf.xml não encontrado"
        ESL_HOST="127.0.0.1"
        ESL_PORT="8021"
        ESL_PASSWORD="ClueCon"
        return 0
    fi
    
    log_debug "Configuração ESL: $esl_conf"
    
    # Extrair configurações
    ESL_HOST=$(grep -oP '(?<=listen-ip.*value=")[^"]*' "$esl_conf" 2>/dev/null | head -1)
    ESL_PORT=$(grep -oP '(?<=listen-port.*value=")[^"]*' "$esl_conf" 2>/dev/null | head -1)
    ESL_PASSWORD=$(grep -oP '(?<=password.*value=")[^"]*' "$esl_conf" 2>/dev/null | head -1)
    
    # Valores padrão
    ESL_HOST="${ESL_HOST:-127.0.0.1}"
    ESL_PORT="${ESL_PORT:-8021}"
    ESL_PASSWORD="${ESL_PASSWORD:-ClueCon}"
    
    # Verificar se ESL está acessível
    if nc -z "$ESL_HOST" "$ESL_PORT" 2>/dev/null; then
        log_success "ESL acessível: $ESL_HOST:$ESL_PORT"
    else
        log_error "ESL não acessível na porta $ESL_PORT"
        log_info "Verifique se mod_event_socket está carregado no FreeSWITCH"
        return 1
    fi
    
    return 0
}

# =============================================================================
# Detecção de Instalação Existente do Voice AI
# =============================================================================

detect_existing_installation() {
    log_step "Verificando instalação existente do Voice AI..."
    
    VOICE_AI_INSTALLED="false"
    
    # Verificar app FusionPBX
    if [[ -d "$FUSIONPBX_PATH/app/voice_secretary" ]]; then
        log_debug "App voice_secretary encontrado em FusionPBX"
        VOICE_AI_INSTALLED="partial"
    fi
    
    # Verificar serviço Python
    if [[ -d "/opt/voice-ai" ]]; then
        log_debug "Diretório /opt/voice-ai encontrado"
        VOICE_AI_INSTALLED="partial"
        
        if [[ -f "/opt/voice-ai/VERSION" ]]; then
            VOICE_AI_VERSION=$(cat /opt/voice-ai/VERSION 2>/dev/null)
            log_debug "Versão instalada: $VOICE_AI_VERSION"
        fi
    fi
    
    # Verificar serviço systemd
    if systemctl list-unit-files | grep -q "voice-ai"; then
        log_debug "Units systemd do Voice AI encontradas"
        VOICE_AI_INSTALLED="partial"
        
        if systemctl is-active --quiet voice-ai-realtime; then
            VOICE_AI_INSTALLED="active"
            log_info "Voice AI já está instalado e rodando"
        fi
    fi
    
    # Verificar tabelas no banco
    if [[ -n "$DB_HOST" ]] && [[ -n "$DB_USER" ]]; then
        local table_check=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc \
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'v_voice_%';" 2>/dev/null)
        
        if [[ "$table_check" -gt 0 ]]; then
            log_debug "Tabelas do Voice AI encontradas no banco: $table_check"
            VOICE_AI_INSTALLED="partial"
        fi
    fi
    
    case "$VOICE_AI_INSTALLED" in
        "false")
            log_success "Nenhuma instalação anterior detectada"
            ;;
        "partial")
            log_warn "Instalação parcial detectada"
            ;;
        "active")
            log_warn "Voice AI já está instalado e ativo"
            ;;
    esac
    
    return 0
}

# =============================================================================
# Detecção de Python
# =============================================================================

detect_python() {
    log_step "Detectando Python..."
    
    local python_cmd=""
    local python_version=""
    
    # Procurar Python 3.11+
    for cmd in python3.12 python3.11 python3; do
        if command -v "$cmd" &>/dev/null; then
            python_version=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
            local major="${python_version%%.*}"
            local minor="${python_version#*.}"
            
            if [[ "$major" -eq 3 ]] && [[ "$minor" -ge 11 ]]; then
                python_cmd="$cmd"
                break
            fi
        fi
    done
    
    if [[ -z "$python_cmd" ]]; then
        log_warn "Python 3.11+ não encontrado"
        log_info "Python 3.11 ou superior é necessário"
        return 1
    fi
    
    log_success "Python encontrado: $python_cmd (versão $python_version)"
    echo "$python_cmd"
    return 0
}

# =============================================================================
# Verificar Conectividade com Banco
# =============================================================================

test_database_connection() {
    log_step "Testando conexão com o banco de dados..."
    
    if [[ -z "$DB_HOST" ]] || [[ -z "$DB_USER" ]] || [[ -z "$DB_PASS" ]]; then
        log_error "Credenciais do banco não configuradas"
        return 1
    fi
    
    local result=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT 1;" 2>&1)
    
    if [[ "$result" == "1" ]]; then
        log_success "Conexão com o banco de dados OK"
        return 0
    else
        log_error "Falha na conexão com o banco de dados"
        log_debug "Erro: $result"
        return 1
    fi
}

# =============================================================================
# Função Principal de Detecção
# =============================================================================

run_detection() {
    local errors=0
    
    echo ""
    log_info "Iniciando detecção de ambiente..."
    echo ""
    
    detect_fusionpbx || ((errors++))
    detect_database_credentials || ((errors++))
    detect_freeswitch || ((errors++))
    detect_esl_config || ((errors++))
    detect_existing_installation
    
    echo ""
    
    if [[ $errors -gt 0 ]]; then
        log_error "Detecção completada com $errors erro(s)"
        return 1
    fi
    
    # Testar conexão com banco
    test_database_connection || ((errors++))
    
    echo ""
    
    return $errors
}

# =============================================================================
# Exibir Configurações Detectadas
# =============================================================================

show_detected_config() {
    echo ""
    echo -e "${CYAN}Configurações Detectadas:${NC}"
    echo -e "${CYAN}──────────────────────────────────────────${NC}"
    echo ""
    echo -e "  ${BOLD}FusionPBX${NC}"
    echo -e "    Caminho:  ${FUSIONPBX_PATH:-não detectado}"
    echo -e "    Versão:   ${FUSIONPBX_VERSION:-desconhecida}"
    echo ""
    echo -e "  ${BOLD}Banco de Dados${NC}"
    echo -e "    Host:     ${DB_HOST:-não detectado}:${DB_PORT:-5432}"
    echo -e "    Database: ${DB_NAME:-não detectado}"
    echo -e "    Usuário:  ${DB_USER:-não detectado}"
    echo ""
    echo -e "  ${BOLD}FreeSWITCH ESL${NC}"
    echo -e "    Host:     ${ESL_HOST:-127.0.0.1}:${ESL_PORT:-8021}"
    echo ""
    echo -e "  ${BOLD}Voice AI${NC}"
    echo -e "    Status:   ${VOICE_AI_INSTALLED:-não verificado}"
    [[ -n "$VOICE_AI_VERSION" ]] && echo -e "    Versão:   $VOICE_AI_VERSION"
    echo ""
    echo -e "${CYAN}──────────────────────────────────────────${NC}"
    echo ""
}
