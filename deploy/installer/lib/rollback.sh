#!/bin/bash
# =============================================================================
# Voice AI IVR - Installer Rollback Functions
# =============================================================================
# Funções para backup, checkpoint e rollback em caso de falha
# =============================================================================

# Variáveis de rollback
ROLLBACK_DIR=""
ROLLBACK_LOG=""
CHECKPOINT_FILE=""
ROLLBACK_ENABLED=true

# Lista de operações para rollback (pilha LIFO)
declare -a ROLLBACK_STACK

# =============================================================================
# Inicializar Sistema de Rollback
# =============================================================================

init_rollback() {
    local install_id="${1:-$(date +%Y%m%d_%H%M%S)}"
    
    ROLLBACK_DIR="/tmp/voice-ai-rollback-${install_id}"
    ROLLBACK_LOG="$ROLLBACK_DIR/rollback.log"
    CHECKPOINT_FILE="$ROLLBACK_DIR/checkpoint.txt"
    BACKUP_DIR="$ROLLBACK_DIR/backups"
    
    mkdir -p "$ROLLBACK_DIR"
    mkdir -p "$BACKUP_DIR"
    
    echo "# Voice AI Rollback Log - $(date)" > "$ROLLBACK_LOG"
    echo "INSTALL_ID=$install_id" >> "$ROLLBACK_LOG"
    
    log_debug "Sistema de rollback inicializado: $ROLLBACK_DIR"
    
    return 0
}

# =============================================================================
# Checkpoints
# =============================================================================

set_checkpoint() {
    local phase="$1"
    local description="${2:-}"
    
    echo "$phase" > "$CHECKPOINT_FILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') CHECKPOINT: $phase - $description" >> "$ROLLBACK_LOG"
    
    log_debug "Checkpoint: $phase"
}

get_checkpoint() {
    if [[ -f "$CHECKPOINT_FILE" ]]; then
        cat "$CHECKPOINT_FILE"
    else
        echo "NONE"
    fi
}

# =============================================================================
# Registrar Operação para Rollback
# =============================================================================

register_rollback() {
    local operation="$1"
    local rollback_cmd="$2"
    
    # Adicionar à pilha de rollback
    ROLLBACK_STACK+=("$rollback_cmd")
    
    echo "OPERATION: $operation" >> "$ROLLBACK_LOG"
    echo "ROLLBACK: $rollback_cmd" >> "$ROLLBACK_LOG"
    echo "---" >> "$ROLLBACK_LOG"
    
    log_debug "Rollback registrado: $operation"
}

# =============================================================================
# Backup de Arquivo
# =============================================================================

backup_for_rollback() {
    local file="$1"
    local type="${2:-file}"
    
    if [[ ! -e "$file" ]]; then
        log_debug "Arquivo não existe para backup: $file"
        return 0
    fi
    
    local backup_name=$(echo "$file" | tr '/' '_')
    local backup_path="$BACKUP_DIR/${backup_name}.backup"
    
    if [[ "$type" == "dir" ]]; then
        tar -czf "${backup_path}.tar.gz" -C "$(dirname "$file")" "$(basename "$file")" 2>/dev/null
        register_rollback "backup_dir:$file" "tar -xzf ${backup_path}.tar.gz -C $(dirname "$file")"
    else
        cp "$file" "$backup_path"
        register_rollback "backup_file:$file" "cp $backup_path $file"
    fi
    
    log_debug "Backup criado: $backup_path"
    return 0
}

# =============================================================================
# Backup do Banco de Dados
# =============================================================================

backup_database_for_rollback() {
    log_step "Criando backup do banco de dados para rollback..."
    
    local backup_file="$BACKUP_DIR/database_$(date +%Y%m%d_%H%M%S).sql"
    
    PGPASSWORD="$DB_PASS" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" > "$backup_file" 2>> "$LOG_FILE"
    
    if [[ $? -eq 0 ]] && [[ -s "$backup_file" ]]; then
        register_rollback "database_backup" "PGPASSWORD='$DB_PASS' psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME < $backup_file"
        log_success "Backup do banco criado: $(du -h "$backup_file" | cut -f1)"
        echo "$backup_file"
        return 0
    else
        log_error "Falha ao criar backup do banco"
        return 1
    fi
}

# =============================================================================
# Executar Rollback
# =============================================================================

execute_rollback() {
    local reason="${1:-Erro durante instalação}"
    
    if [[ "$ROLLBACK_ENABLED" != "true" ]]; then
        log_warn "Rollback desabilitado"
        return 0
    fi
    
    log_error "Executando rollback: $reason"
    echo ""
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}                    EXECUTANDO ROLLBACK                         ${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    local total=${#ROLLBACK_STACK[@]}
    local success=0
    local failed=0
    
    # Executar rollback em ordem reversa (LIFO)
    for ((i=${#ROLLBACK_STACK[@]}-1; i>=0; i--)); do
        local cmd="${ROLLBACK_STACK[$i]}"
        
        log_info "Rollback $((total - i))/$total: $cmd"
        
        eval "$cmd" >> "$LOG_FILE" 2>&1
        
        if [[ $? -eq 0 ]]; then
            ((success++))
            log_debug "Rollback OK: $cmd"
        else
            ((failed++))
            log_warn "Rollback falhou: $cmd"
        fi
    done
    
    echo ""
    echo -e "${CYAN}Rollback concluído: $success OK, $failed falhas${NC}"
    echo ""
    
    # Registrar no log
    echo "=== ROLLBACK EXECUTADO ===" >> "$ROLLBACK_LOG"
    echo "Razão: $reason" >> "$ROLLBACK_LOG"
    echo "Sucesso: $success" >> "$ROLLBACK_LOG"
    echo "Falhas: $failed" >> "$ROLLBACK_LOG"
    
    return $failed
}

# =============================================================================
# Rollback Parcial (até um checkpoint)
# =============================================================================

rollback_to_checkpoint() {
    local target_checkpoint="$1"
    
    log_info "Rollback até checkpoint: $target_checkpoint"
    
    # Ler log de rollback e executar operações após o checkpoint
    # (implementação simplificada)
    
    execute_rollback "Rollback até $target_checkpoint"
}

# =============================================================================
# Limpar Rollback (após sucesso)
# =============================================================================

cleanup_rollback() {
    local keep_days="${1:-7}"
    
    log_debug "Limpando dados de rollback..."
    
    # Limpar diretório atual
    if [[ -d "$ROLLBACK_DIR" ]]; then
        rm -rf "$ROLLBACK_DIR"
        log_debug "Diretório de rollback removido: $ROLLBACK_DIR"
    fi
    
    # Limpar rollbacks antigos
    find /tmp -maxdepth 1 -name "voice-ai-rollback-*" -type d -mtime +$keep_days -exec rm -rf {} \; 2>/dev/null
    
    # Limpar pilha
    ROLLBACK_STACK=()
    
    return 0
}

# =============================================================================
# Preservar Rollback (para investigação)
# =============================================================================

preserve_rollback() {
    local dest="/var/log/voice-ai/rollback-$(date +%Y%m%d_%H%M%S)"
    
    if [[ -d "$ROLLBACK_DIR" ]]; then
        mv "$ROLLBACK_DIR" "$dest"
        log_info "Dados de rollback preservados em: $dest"
        echo "$dest"
    fi
}

# =============================================================================
# Mostrar Status do Rollback
# =============================================================================

show_rollback_status() {
    echo ""
    echo -e "${CYAN}Status do Sistema de Rollback:${NC}"
    echo "────────────────────────────────────────────────────"
    echo "  Diretório:    ${ROLLBACK_DIR:-não inicializado}"
    echo "  Checkpoint:   $(get_checkpoint)"
    echo "  Operações:    ${#ROLLBACK_STACK[@]} registradas"
    
    if [[ -d "$BACKUP_DIR" ]]; then
        local backup_size=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
        echo "  Backups:      $backup_size"
    fi
    
    echo "────────────────────────────────────────────────────"
    echo ""
}

# =============================================================================
# Handler de Erro (trap)
# =============================================================================

error_handler() {
    local exit_code=$?
    local line_number=$1
    local command="$2"
    
    log_error "Erro na linha $line_number: $command (código: $exit_code)"
    
    if [[ "$ROLLBACK_ENABLED" == "true" ]] && [[ ${#ROLLBACK_STACK[@]} -gt 0 ]]; then
        if confirm "Deseja executar rollback?"; then
            execute_rollback "Erro na linha $line_number"
        else
            log_warn "Rollback cancelado pelo usuário"
            preserve_rollback
        fi
    fi
    
    exit $exit_code
}

setup_error_handler() {
    # Configurar trap para erros
    trap 'error_handler ${LINENO} "${BASH_COMMAND}"' ERR
    
    log_debug "Handler de erro configurado"
}

disable_error_handler() {
    trap - ERR
    log_debug "Handler de erro desabilitado"
}

# =============================================================================
# Registro de Operações Comuns
# =============================================================================

# Registrar criação de diretório
register_mkdir() {
    local dir="$1"
    register_rollback "mkdir:$dir" "rmdir '$dir' 2>/dev/null || true"
}

# Registrar cópia de arquivo
register_copy() {
    local dest="$1"
    register_rollback "copy:$dest" "rm -f '$dest'"
}

# Registrar instalação de pacote (não recomendado para rollback automático)
register_package() {
    local package="$1"
    # Não fazemos rollback de pacotes automaticamente
    echo "PACKAGE_INSTALLED: $package" >> "$ROLLBACK_LOG"
}

# Registrar criação de usuário
register_user() {
    local user="$1"
    register_rollback "user:$user" "userdel '$user' 2>/dev/null || true"
}

# Registrar instalação de serviço
register_service() {
    local service="$1"
    register_rollback "service:$service" "systemctl stop '$service' 2>/dev/null; systemctl disable '$service' 2>/dev/null; rm -f '/etc/systemd/system/${service}.service'"
}

# Registrar inserção no banco
register_db_insert() {
    local table="$1"
    local condition="$2"
    register_rollback "db_insert:$table" "PGPASSWORD='$DB_PASS' psql -h '$DB_HOST' -p '$DB_PORT' -U '$DB_USER' -d '$DB_NAME' -c \"DELETE FROM $table WHERE $condition;\""
}
