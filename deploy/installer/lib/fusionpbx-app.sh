#!/bin/bash
# =============================================================================
# Voice AI IVR - Installer FusionPBX App Functions
# =============================================================================
# Funções para instalação do app voice_secretary no FusionPBX
# =============================================================================

# Variáveis
VOICE_SECRETARY_APP_PATH=""
FUSIONPBX_WWW_USER="${FUSIONPBX_WWW_USER:-www-data}"
FUSIONPBX_WWW_GROUP="${FUSIONPBX_WWW_GROUP:-www-data}"

# =============================================================================
# Verificar App Existente
# =============================================================================

check_existing_app() {
    local app_path="$FUSIONPBX_PATH/app/voice_secretary"
    
    if [[ -d "$app_path" ]]; then
        log_debug "App voice_secretary já existe em: $app_path"
        VOICE_SECRETARY_APP_PATH="$app_path"
        return 0
    fi
    
    return 1
}

# =============================================================================
# Backup do App Existente
# =============================================================================

backup_existing_app() {
    if [[ -d "$VOICE_SECRETARY_APP_PATH" ]]; then
        log_step "Fazendo backup do app existente..."
        
        local backup_file=$(backup_directory "$VOICE_SECRETARY_APP_PATH")
        
        if [[ -n "$backup_file" ]]; then
            log_success "Backup do app criado: $backup_file"
            echo "$backup_file"
            return 0
        else
            log_error "Falha ao criar backup do app"
            return 1
        fi
    fi
    
    return 0
}

# =============================================================================
# Copiar App para FusionPBX
# =============================================================================

install_fusionpbx_app() {
    local source_path="$1"
    local dest_path="$FUSIONPBX_PATH/app/voice_secretary"
    
    log_step "Instalando app voice_secretary no FusionPBX..."
    
    if [[ ! -d "$source_path" ]]; then
        log_error "Diretório source não encontrado: $source_path"
        return 1
    fi
    
    # Verificar se app_config.php existe no source
    if [[ ! -f "$source_path/app_config.php" ]]; then
        log_error "app_config.php não encontrado em: $source_path"
        return 1
    fi
    
    # Backup se existir
    if check_existing_app; then
        backup_existing_app || return 1
    fi
    
    # Copiar arquivos
    if log_dry "cp -r $source_path $dest_path"; then
        return 0
    fi
    
    # Criar diretório destino
    mkdir -p "$(dirname "$dest_path")"
    
    # Copiar
    cp -r "$source_path" "$dest_path"
    
    if [[ $? -ne 0 ]]; then
        log_error "Falha ao copiar app para $dest_path"
        return 1
    fi
    
    log_success "App copiado para: $dest_path"
    VOICE_SECRETARY_APP_PATH="$dest_path"
    
    return 0
}

# =============================================================================
# Ajustar Permissões do App
# =============================================================================

fix_app_permissions() {
    log_step "Ajustando permissões do app..."
    
    if [[ -z "$VOICE_SECRETARY_APP_PATH" ]]; then
        VOICE_SECRETARY_APP_PATH="$FUSIONPBX_PATH/app/voice_secretary"
    fi
    
    if [[ ! -d "$VOICE_SECRETARY_APP_PATH" ]]; then
        log_error "App não encontrado: $VOICE_SECRETARY_APP_PATH"
        return 1
    fi
    
    if log_dry "chown -R $FUSIONPBX_WWW_USER:$FUSIONPBX_WWW_GROUP $VOICE_SECRETARY_APP_PATH"; then
        return 0
    fi
    
    # Ownership
    chown -R "$FUSIONPBX_WWW_USER:$FUSIONPBX_WWW_GROUP" "$VOICE_SECRETARY_APP_PATH"
    
    # Permissões de diretórios
    find "$VOICE_SECRETARY_APP_PATH" -type d -exec chmod 755 {} \;
    
    # Permissões de arquivos
    find "$VOICE_SECRETARY_APP_PATH" -type f -exec chmod 644 {} \;
    
    log_success "Permissões ajustadas"
    return 0
}

# =============================================================================
# Executar Upgrade do FusionPBX
# =============================================================================

run_fusionpbx_upgrade() {
    log_step "Registrando app no FusionPBX (upgrade.php)..."
    
    local upgrade_script="$FUSIONPBX_PATH/core/upgrade/upgrade.php"
    
    if [[ ! -f "$upgrade_script" ]]; then
        log_error "Script de upgrade não encontrado: $upgrade_script"
        return 1
    fi
    
    if log_dry "sudo -u $FUSIONPBX_WWW_USER php $upgrade_script"; then
        return 0
    fi
    
    # Executar upgrade como www-data
    sudo -u "$FUSIONPBX_WWW_USER" php "$upgrade_script" >> "$LOG_FILE" 2>&1
    
    if [[ $? -eq 0 ]]; then
        log_success "App registrado no FusionPBX"
        return 0
    else
        log_warn "Upgrade completou com avisos (verificar logs)"
        return 0
    fi
}

# =============================================================================
# Limpar Cache do FusionPBX
# =============================================================================

clear_fusionpbx_cache() {
    log_step "Limpando cache do FusionPBX..."
    
    local cache_dirs=(
        "/var/cache/fusionpbx"
        "$FUSIONPBX_PATH/app/cache"
        "/tmp/fusionpbx_cache"
    )
    
    for cache_dir in "${cache_dirs[@]}"; do
        if [[ -d "$cache_dir" ]]; then
            if log_dry "rm -rf $cache_dir/*"; then
                continue
            fi
            
            rm -rf "$cache_dir"/* 2>/dev/null
            log_debug "Cache limpo: $cache_dir"
        fi
    done
    
    log_success "Cache do FusionPBX limpo"
    return 0
}

# =============================================================================
# Verificar Registro do App
# =============================================================================

verify_app_registration() {
    log_step "Verificando registro do app..."
    
    # Verificar se app_config.php existe
    if [[ ! -f "$VOICE_SECRETARY_APP_PATH/app_config.php" ]]; then
        log_error "app_config.php não encontrado"
        return 1
    fi
    
    # Verificar permissões no banco
    local permissions=$(execute_sql "
        SELECT COUNT(*) FROM v_group_permissions 
        WHERE permission_name LIKE 'voice_secretary%';
    " 2>/dev/null)
    
    if [[ "$permissions" -gt 0 ]]; then
        log_debug "Permissões encontradas: $permissions"
    else
        log_warn "Nenhuma permissão do voice_secretary encontrada no banco"
    fi
    
    # Verificar menu
    local menu_items=$(execute_sql "
        SELECT COUNT(*) FROM v_menu_items 
        WHERE menu_item_link LIKE '%voice_secretary%';
    " 2>/dev/null)
    
    if [[ "$menu_items" -gt 0 ]]; then
        log_debug "Itens de menu encontrados: $menu_items"
        log_success "App registrado no FusionPBX"
        return 0
    else
        log_warn "Menu do voice_secretary não encontrado"
        log_info "Pode ser necessário executar upgrade.php novamente"
        return 1
    fi
}

# =============================================================================
# Remover App do FusionPBX
# =============================================================================

remove_fusionpbx_app() {
    log_step "Removendo app voice_secretary do FusionPBX..."
    
    local app_path="$FUSIONPBX_PATH/app/voice_secretary"
    
    if [[ ! -d "$app_path" ]]; then
        log_debug "App não encontrado, nada a remover"
        return 0
    fi
    
    # Backup antes de remover
    backup_existing_app
    
    if log_dry "rm -rf $app_path"; then
        return 0
    fi
    
    rm -rf "$app_path"
    
    if [[ $? -eq 0 ]]; then
        log_success "App removido: $app_path"
    else
        log_error "Falha ao remover app"
        return 1
    fi
    
    # Remover permissões do banco
    execute_sql "DELETE FROM v_group_permissions WHERE permission_name LIKE 'voice_secretary%';" 2>/dev/null
    
    # Remover menu do banco
    execute_sql "DELETE FROM v_menu_items WHERE menu_item_link LIKE '%voice_secretary%';" 2>/dev/null
    
    # Limpar cache
    clear_fusionpbx_cache
    
    return 0
}

# =============================================================================
# Instalação Completa do App
# =============================================================================

install_app_complete() {
    local source_path="$1"
    
    log_step "Iniciando instalação do app FusionPBX..."
    
    # 1. Instalar arquivos
    install_fusionpbx_app "$source_path" || return 1
    
    # 2. Ajustar permissões
    fix_app_permissions || return 1
    
    # 3. Executar upgrade
    run_fusionpbx_upgrade || return 1
    
    # 4. Limpar cache
    clear_fusionpbx_cache
    
    # 5. Verificar
    verify_app_registration
    
    log_success "Instalação do app FusionPBX concluída"
    return 0
}

# =============================================================================
# Atualizar App Existente
# =============================================================================

upgrade_fusionpbx_app() {
    local source_path="$1"
    
    log_step "Atualizando app voice_secretary..."
    
    if ! check_existing_app; then
        log_info "App não existe, executando instalação completa"
        install_app_complete "$source_path"
        return $?
    fi
    
    # Backup
    local backup_file=$(backup_existing_app)
    
    # Remover app antigo
    rm -rf "$VOICE_SECRETARY_APP_PATH"
    
    # Instalar novo
    install_fusionpbx_app "$source_path" || {
        # Rollback
        if [[ -n "$backup_file" ]]; then
            log_warn "Restaurando backup..."
            tar -xzf "$backup_file" -C "$(dirname "$VOICE_SECRETARY_APP_PATH")"
        fi
        return 1
    }
    
    # Ajustar permissões
    fix_app_permissions
    
    # Executar upgrade
    run_fusionpbx_upgrade
    
    # Limpar cache
    clear_fusionpbx_cache
    
    log_success "App atualizado com sucesso"
    return 0
}
