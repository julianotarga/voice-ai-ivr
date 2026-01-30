#!/bin/bash
# =============================================================================
# Voice AI IVR - Installer Voice Service Functions
# =============================================================================
# Funções para instalação do serviço Python Voice AI
# =============================================================================

# Variáveis
VOICE_AI_INSTALL_PATH="${VOICE_AI_INSTALL_PATH:-/opt/voice-ai}"
VOICE_AI_USER="${VOICE_AI_USER:-voiceai}"
VOICE_AI_LOG_PATH="/var/log/voice-ai"
PYTHON_CMD=""

# =============================================================================
# Criar Usuário do Serviço
# =============================================================================

create_voice_ai_user() {
    log_step "Configurando usuário do serviço..."
    
    if id "$VOICE_AI_USER" &>/dev/null; then
        log_debug "Usuário $VOICE_AI_USER já existe"
        return 0
    fi
    
    if log_dry "useradd -r -s /bin/false -d $VOICE_AI_INSTALL_PATH $VOICE_AI_USER"; then
        return 0
    fi
    
    useradd -r -s /bin/false -d "$VOICE_AI_INSTALL_PATH" "$VOICE_AI_USER"
    
    if [[ $? -eq 0 ]]; then
        log_success "Usuário criado: $VOICE_AI_USER"
        
        # Adicionar ao grupo audio (para acesso a dispositivos de áudio)
        usermod -aG audio "$VOICE_AI_USER" 2>/dev/null || true
        
        return 0
    else
        log_error "Falha ao criar usuário $VOICE_AI_USER"
        return 1
    fi
}

# =============================================================================
# Criar Estrutura de Diretórios
# =============================================================================

create_directory_structure() {
    log_step "Criando estrutura de diretórios..."
    
    local dirs=(
        "$VOICE_AI_INSTALL_PATH"
        "$VOICE_AI_INSTALL_PATH/data"
        "$VOICE_AI_LOG_PATH"
        "/tmp/voice-ai-announcements"
    )
    
    for dir in "${dirs[@]}"; do
        if log_dry "mkdir -p $dir"; then
            continue
        fi
        
        mkdir -p "$dir"
        log_debug "Diretório criado: $dir"
    done
    
    # Ajustar ownership
    if log_dry "chown -R $VOICE_AI_USER:$VOICE_AI_USER $VOICE_AI_INSTALL_PATH"; then
        return 0
    fi
    
    chown -R "$VOICE_AI_USER:$VOICE_AI_USER" "$VOICE_AI_INSTALL_PATH"
    chown -R "$VOICE_AI_USER:$VOICE_AI_USER" "$VOICE_AI_LOG_PATH"
    chown -R "$VOICE_AI_USER:$VOICE_AI_USER" "/tmp/voice-ai-announcements"
    
    chmod 755 "$VOICE_AI_INSTALL_PATH"
    chmod 755 "$VOICE_AI_LOG_PATH"
    
    # Link simbólico para logs
    ln -sf "$VOICE_AI_LOG_PATH" "$VOICE_AI_INSTALL_PATH/logs" 2>/dev/null || true
    
    log_success "Estrutura de diretórios criada"
    return 0
}

# =============================================================================
# Copiar Código Python
# =============================================================================

copy_voice_service_code() {
    local source_path="$1"
    local dest_path="$VOICE_AI_INSTALL_PATH"
    
    log_step "Copiando código do Voice AI..."
    
    if [[ ! -d "$source_path" ]]; then
        log_error "Diretório source não encontrado: $source_path"
        return 1
    fi
    
    # Verificar se tem requirements.txt
    if [[ ! -f "$source_path/requirements.txt" ]]; then
        log_error "requirements.txt não encontrado em: $source_path"
        return 1
    fi
    
    if log_dry "rsync -av $source_path/ $dest_path/"; then
        return 0
    fi
    
    # Copiar código (excluindo venv, __pycache__, .git)
    rsync -av --exclude 'venv' --exclude '__pycache__' --exclude '.git' \
        --exclude '*.pyc' --exclude '.env' --exclude '.env.*' \
        "$source_path/" "$dest_path/" >> "$LOG_FILE" 2>&1
    
    if [[ $? -eq 0 ]]; then
        log_success "Código copiado para: $dest_path"
        return 0
    else
        log_error "Falha ao copiar código"
        return 1
    fi
}

# =============================================================================
# Criar Virtual Environment
# =============================================================================

create_virtualenv() {
    log_step "Criando ambiente virtual Python..."
    
    # Detectar Python
    PYTHON_CMD=$(detect_python 2>/dev/null)
    
    if [[ -z "$PYTHON_CMD" ]]; then
        log_error "Python 3.11+ não encontrado"
        return 1
    fi
    
    local venv_path="$VOICE_AI_INSTALL_PATH/venv"
    
    if [[ -d "$venv_path" ]]; then
        log_debug "Virtual environment já existe"
        return 0
    fi
    
    if log_dry "$PYTHON_CMD -m venv $venv_path"; then
        return 0
    fi
    
    "$PYTHON_CMD" -m venv "$venv_path" >> "$LOG_FILE" 2>&1
    
    if [[ $? -eq 0 ]]; then
        log_success "Virtual environment criado: $venv_path"
        return 0
    else
        log_error "Falha ao criar virtual environment"
        return 1
    fi
}

# =============================================================================
# Instalar Dependências Python
# =============================================================================

install_python_dependencies() {
    log_step "Instalando dependências Python..."
    
    local venv_path="$VOICE_AI_INSTALL_PATH/venv"
    local requirements_file="$VOICE_AI_INSTALL_PATH/requirements.txt"
    
    if [[ ! -f "$requirements_file" ]]; then
        log_error "requirements.txt não encontrado: $requirements_file"
        return 1
    fi
    
    if log_dry "pip install -r $requirements_file"; then
        return 0
    fi
    
    # Ativar venv e instalar
    source "$venv_path/bin/activate"
    
    # Atualizar pip
    pip install --upgrade pip >> "$LOG_FILE" 2>&1
    
    # Instalar dependências
    pip install -r "$requirements_file" >> "$LOG_FILE" 2>&1
    
    local exit_code=$?
    
    deactivate 2>/dev/null
    
    if [[ $exit_code -eq 0 ]]; then
        log_success "Dependências instaladas"
        return 0
    else
        log_error "Falha ao instalar dependências"
        return 1
    fi
}

# =============================================================================
# Gerar Arquivo .env
# =============================================================================

generate_env_file() {
    local env_file="$VOICE_AI_INSTALL_PATH/.env"
    
    log_step "Gerando arquivo de configuração .env..."
    
    if [[ -f "$env_file" ]]; then
        log_warn "Arquivo .env já existe, preservando configurações"
        return 0
    fi
    
    if log_dry "Criar $env_file"; then
        return 0
    fi
    
    cat > "$env_file" << EOF
# =============================================================================
# Voice AI IVR - Configuration
# Gerado automaticamente em $(date '+%Y-%m-%d %H:%M:%S')
# =============================================================================

# Ambiente
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO

# Banco de Dados (FusionPBX)
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}
DB_HOST=${DB_HOST}
DB_PORT=${DB_PORT}
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASS=${DB_PASS}

# FreeSWITCH ESL
ESL_HOST=${ESL_HOST:-127.0.0.1}
ESL_PORT=${ESL_PORT:-8021}
ESL_PASSWORD=${ESL_PASSWORD:-ClueCon}
ESL_SERVER_PORT=8022

# API Keys (preencher manualmente)
OPENAI_API_KEY=
ELEVENLABS_API_KEY=
GOOGLE_APPLICATION_CREDENTIALS=

# Configurações de Áudio
AUDIO_MODE=websocket
FS_PCMU_PASSTHROUGH=false

# Redis (opcional)
# REDIS_URL=redis://localhost:6379/0

# OmniPlay Backend (opcional)
# OMNIPLAY_BACKEND_URL=http://localhost:8080
# OMNIPLAY_API_KEY=

# =============================================================================
# IMPORTANTE: Configure as API keys acima antes de iniciar o serviço
# =============================================================================
EOF

    # Ajustar permissões (somente owner pode ler)
    chmod 600 "$env_file"
    chown "$VOICE_AI_USER:$VOICE_AI_USER" "$env_file"
    
    log_success "Arquivo .env criado: $env_file"
    log_warn "IMPORTANTE: Edite $env_file e configure as API keys"
    
    return 0
}

# =============================================================================
# Instalar Units Systemd
# =============================================================================

install_systemd_units() {
    local templates_dir="$1"
    
    log_step "Instalando units do systemd..."
    
    local units=(
        "voice-ai-realtime.service"
        "voice-ai-service.service"
    )
    
    for unit in "${units[@]}"; do
        local source_file="$templates_dir/systemd/$unit"
        
        if [[ ! -f "$source_file" ]]; then
            log_debug "Unit não encontrada: $source_file"
            continue
        fi
        
        if log_dry "cp $source_file /etc/systemd/system/$unit"; then
            continue
        fi
        
        cp "$source_file" "/etc/systemd/system/$unit"
        chmod 644 "/etc/systemd/system/$unit"
        
        log_debug "Unit instalada: $unit"
    done
    
    # Reload systemd
    reload_systemd
    
    log_success "Units do systemd instaladas"
    return 0
}

# =============================================================================
# Configurar Logrotate
# =============================================================================

configure_logrotate() {
    log_step "Configurando logrotate..."
    
    local logrotate_file="/etc/logrotate.d/voice-ai"
    
    if log_dry "Criar $logrotate_file"; then
        return 0
    fi
    
    cat > "$logrotate_file" << EOF
${VOICE_AI_LOG_PATH}/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 ${VOICE_AI_USER} ${VOICE_AI_USER}
    sharedscripts
    postrotate
        systemctl reload voice-ai-realtime 2>/dev/null || true
    endscript
}
EOF

    chmod 644 "$logrotate_file"
    
    log_success "Logrotate configurado"
    return 0
}

# =============================================================================
# Habilitar e Iniciar Serviços
# =============================================================================

enable_and_start_services() {
    log_step "Habilitando e iniciando serviços..."
    
    local services=(
        "voice-ai-realtime"
    )
    
    for service in "${services[@]}"; do
        if log_dry "systemctl enable --now $service"; then
            continue
        fi
        
        # Habilitar
        systemctl enable "$service" >> "$LOG_FILE" 2>&1
        
        # Iniciar
        systemctl start "$service" >> "$LOG_FILE" 2>&1
        
        sleep 2
        
        # Verificar
        if systemctl is-active --quiet "$service"; then
            log_success "Serviço ativo: $service"
        else
            log_error "Falha ao iniciar: $service"
            log_info "Verifique: journalctl -u $service -n 50"
            return 1
        fi
    done
    
    return 0
}

# =============================================================================
# Parar Serviços
# =============================================================================

stop_voice_ai_services() {
    log_step "Parando serviços do Voice AI..."
    
    local services=(
        "voice-ai-realtime"
        "voice-ai-service"
    )
    
    for service in "${services[@]}"; do
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            if log_dry "systemctl stop $service"; then
                continue
            fi
            
            systemctl stop "$service" >> "$LOG_FILE" 2>&1
            log_debug "Serviço parado: $service"
        fi
    done
    
    log_success "Serviços parados"
    return 0
}

# =============================================================================
# Remover Serviços
# =============================================================================

remove_voice_ai_services() {
    log_step "Removendo serviços do Voice AI..."
    
    # Parar serviços
    stop_voice_ai_services
    
    local services=(
        "voice-ai-realtime"
        "voice-ai-service"
    )
    
    for service in "${services[@]}"; do
        local unit_file="/etc/systemd/system/${service}.service"
        
        if [[ -f "$unit_file" ]]; then
            if log_dry "rm $unit_file"; then
                continue
            fi
            
            systemctl disable "$service" >> "$LOG_FILE" 2>&1
            rm "$unit_file"
            log_debug "Unit removida: $unit_file"
        fi
    done
    
    reload_systemd
    
    log_success "Serviços removidos"
    return 0
}

# =============================================================================
# Remover Instalação Completa
# =============================================================================

remove_voice_ai_installation() {
    log_step "Removendo instalação do Voice AI..."
    
    # Remover serviços
    remove_voice_ai_services
    
    # Remover diretórios
    if log_dry "rm -rf $VOICE_AI_INSTALL_PATH"; then
        return 0
    fi
    
    rm -rf "$VOICE_AI_INSTALL_PATH"
    rm -rf "$VOICE_AI_LOG_PATH"
    rm -rf /tmp/voice-ai-*
    rm -f /etc/logrotate.d/voice-ai
    
    log_success "Instalação removida"
    return 0
}

# =============================================================================
# Verificar Status do Serviço
# =============================================================================

verify_service_status() {
    log_step "Verificando status do serviço..."
    
    local errors=0
    
    # Verificar serviço
    if systemctl is-active --quiet voice-ai-realtime; then
        print_checklist_item "ok" "Serviço voice-ai-realtime está rodando"
    else
        print_checklist_item "fail" "Serviço voice-ai-realtime não está rodando"
        ((errors++))
    fi
    
    # Verificar porta 8022 (ESL Outbound)
    if check_port 8022; then
        print_checklist_item "ok" "Porta 8022 (ESL Outbound) está escutando"
    else
        print_checklist_item "fail" "Porta 8022 não está escutando"
        ((errors++))
    fi
    
    # Verificar porta 8085 (API, se aplicável)
    if check_port 8085; then
        print_checklist_item "ok" "Porta 8085 (API) está escutando"
    else
        print_checklist_item "warn" "Porta 8085 não está escutando (pode ser normal)"
    fi
    
    # Verificar logs recentes
    local recent_errors=$(journalctl -u voice-ai-realtime --since "5 minutes ago" --no-pager 2>/dev/null | grep -ci "error")
    
    if [[ "$recent_errors" -eq 0 ]]; then
        print_checklist_item "ok" "Sem erros recentes nos logs"
    else
        print_checklist_item "warn" "$recent_errors erros nos últimos 5 minutos"
    fi
    
    return $errors
}

# =============================================================================
# Instalação Completa do Serviço
# =============================================================================

install_voice_service_complete() {
    local source_path="$1"
    local templates_path="$2"
    
    log_step "Iniciando instalação do serviço Voice AI..."
    
    # 1. Criar usuário
    create_voice_ai_user || return 1
    
    # 2. Criar diretórios
    create_directory_structure || return 1
    
    # 3. Copiar código
    copy_voice_service_code "$source_path" || return 1
    
    # 4. Criar virtual environment
    create_virtualenv || return 1
    
    # 5. Instalar dependências
    install_python_dependencies || return 1
    
    # 6. Gerar .env
    generate_env_file
    
    # 7. Instalar systemd units
    install_systemd_units "$templates_path" || return 1
    
    # 8. Configurar logrotate
    configure_logrotate
    
    # 9. Ajustar ownership final
    chown -R "$VOICE_AI_USER:$VOICE_AI_USER" "$VOICE_AI_INSTALL_PATH"
    
    log_success "Instalação do serviço Voice AI concluída"
    log_warn "Configure as API keys em: $VOICE_AI_INSTALL_PATH/.env"
    log_info "Para iniciar: systemctl start voice-ai-realtime"
    
    return 0
}
