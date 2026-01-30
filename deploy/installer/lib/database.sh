#!/bin/bash
# =============================================================================
# Voice AI IVR - Installer Database Functions
# =============================================================================
# Funções para gerenciamento do banco de dados PostgreSQL
# =============================================================================

# =============================================================================
# Backup do Banco de Dados
# =============================================================================

backup_database() {
    local backup_file="${1:-}"
    local backup_dir="${BACKUP_DIR:-/tmp/voice-ai-backup-$$}"
    
    if [[ -z "$backup_file" ]]; then
        backup_file="$backup_dir/database_backup_$(date +%Y%m%d_%H%M%S).sql"
    fi
    
    log_step "Criando backup do banco de dados..."
    
    if log_dry "pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER $DB_NAME > $backup_file"; then
        return 0
    fi
    
    mkdir -p "$(dirname "$backup_file")"
    
    PGPASSWORD="$DB_PASS" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" > "$backup_file" 2>> "$LOG_FILE"
    
    if [[ $? -eq 0 ]] && [[ -s "$backup_file" ]]; then
        local size=$(du -h "$backup_file" | cut -f1)
        log_success "Backup criado: $backup_file ($size)"
        echo "$backup_file"
        return 0
    else
        log_error "Falha ao criar backup do banco de dados"
        return 1
    fi
}

# =============================================================================
# Restaurar Banco de Dados
# =============================================================================

restore_database() {
    local backup_file="$1"
    
    if [[ ! -f "$backup_file" ]]; then
        log_error "Arquivo de backup não encontrado: $backup_file"
        return 1
    fi
    
    log_step "Restaurando banco de dados de: $backup_file"
    
    if log_dry "psql -h $DB_HOST -p $DB_PORT -U $DB_USER $DB_NAME < $backup_file"; then
        return 0
    fi
    
    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" < "$backup_file" 2>> "$LOG_FILE"
    
    if [[ $? -eq 0 ]]; then
        log_success "Banco de dados restaurado"
        return 0
    else
        log_error "Falha ao restaurar banco de dados"
        return 1
    fi
}

# =============================================================================
# Executar SQL
# =============================================================================

execute_sql() {
    local sql="$1"
    local description="${2:-}"
    
    [[ -n "$description" ]] && log_debug "$description"
    
    if log_dry "psql: $sql"; then
        return 0
    fi
    
    local result=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "$sql" 2>&1)
    local exit_code=$?
    
    if [[ $exit_code -ne 0 ]]; then
        log_error "Erro ao executar SQL: $result"
        log_debug "SQL: $sql"
        return 1
    fi
    
    echo "$result"
    return 0
}

execute_sql_file() {
    local sql_file="$1"
    local description="${2:-Executando SQL}"
    
    if [[ ! -f "$sql_file" ]]; then
        log_error "Arquivo SQL não encontrado: $sql_file"
        return 1
    fi
    
    log_info "$description: $(basename "$sql_file")"
    
    if log_dry "psql -f $sql_file"; then
        return 0
    fi
    
    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$sql_file" >> "$LOG_FILE" 2>&1
    
    if [[ $? -eq 0 ]]; then
        log_success "SQL executado: $(basename "$sql_file")"
        return 0
    else
        log_error "Falha ao executar SQL: $(basename "$sql_file")"
        return 1
    fi
}

# =============================================================================
# Verificar Tabelas do Voice AI
# =============================================================================

check_voice_ai_tables() {
    log_step "Verificando tabelas do Voice AI..."
    
    local tables=(
        "v_voice_ai_providers"
        "v_voice_secretaries"
        "v_voice_documents"
        "v_voice_document_chunks"
        "v_voice_conversations"
        "v_voice_messages"
        "v_voice_transfer_destinations"
        "v_voice_transfer_rules"
    )
    
    local existing=0
    local missing=0
    
    for table in "${tables[@]}"; do
        local exists=$(execute_sql "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '$table');" 2>/dev/null)
        
        if [[ "$exists" == "t" ]]; then
            log_debug "Tabela existe: $table"
            ((existing++))
        else
            log_debug "Tabela não existe: $table"
            ((missing++))
        fi
    done
    
    log_info "Tabelas existentes: $existing, Faltando: $missing"
    
    if [[ $missing -eq 0 ]]; then
        log_success "Todas as tabelas do Voice AI existem"
        return 0
    elif [[ $existing -gt 0 ]]; then
        log_warn "Instalação parcial detectada ($existing/${#tables[@]} tabelas)"
        return 1
    else
        log_info "Nenhuma tabela do Voice AI encontrada"
        return 2
    fi
}

# =============================================================================
# Executar Migrations
# =============================================================================

run_migrations() {
    local migrations_dir="$1"
    
    if [[ ! -d "$migrations_dir" ]]; then
        log_error "Diretório de migrations não encontrado: $migrations_dir"
        return 1
    fi
    
    log_step "Executando migrations do banco de dados..."
    
    # Backup antes de migrations
    local backup_file
    backup_file=$(backup_database)
    
    if [[ -z "$backup_file" ]] && [[ "$DRY_RUN" != "true" ]]; then
        log_error "Falha ao criar backup, abortando migrations"
        return 1
    fi
    
    local success=0
    local failed=0
    
    # Executar migrations em ordem
    for migration in "$migrations_dir"/*.sql; do
        if [[ -f "$migration" ]]; then
            local name=$(basename "$migration")
            log_info "Migration: $name"
            
            if execute_sql_file "$migration" "Executando migration"; then
                ((success++))
            else
                ((failed++))
                log_error "Migration falhou: $name"
                
                # Rollback em caso de falha
                if [[ "$DRY_RUN" != "true" ]] && [[ -n "$backup_file" ]]; then
                    log_warn "Executando rollback do banco de dados..."
                    restore_database "$backup_file"
                fi
                
                return 1
            fi
        fi
    done
    
    log_success "Migrations concluídas: $success executadas"
    return 0
}

# =============================================================================
# Inserir Dialplan no Banco
# =============================================================================

insert_dialplan() {
    local domain_uuid="$1"
    local dialplan_name="${2:-voice_ai_secretary}"
    local dialplan_context="${3:-public}"
    local dialplan_order="${4:-5}"
    
    log_step "Inserindo dialplan no banco de dados..."
    
    # Verificar se já existe
    local exists=$(execute_sql "SELECT COUNT(*) FROM v_dialplans WHERE dialplan_name = '$dialplan_name' AND domain_uuid = '$domain_uuid'::uuid;" 2>/dev/null)
    
    if [[ "$exists" -gt 0 ]]; then
        log_warn "Dialplan '$dialplan_name' já existe para este domínio"
        return 0
    fi
    
    local dialplan_xml='<extension name="voice_ai_secretary" continue="false">
  <condition field="${voice_secretary_uuid}" expression="^(.+)$">
    <action application="answer"/>
    <action application="set" data="voice_secretary_uuid=${voice_secretary_uuid}"/>
    <action application="socket" data="127.0.0.1:8022 async full"/>
  </condition>
</extension>'
    
    local sql="INSERT INTO v_dialplans (
        dialplan_uuid,
        domain_uuid,
        dialplan_name,
        dialplan_number,
        dialplan_context,
        dialplan_continue,
        dialplan_order,
        dialplan_enabled,
        dialplan_description,
        dialplan_xml,
        insert_date
    ) VALUES (
        gen_random_uuid(),
        '$domain_uuid'::uuid,
        '$dialplan_name',
        '',
        '$dialplan_context',
        'false',
        $dialplan_order,
        'true',
        'Voice AI Secretary - ESL Outbound para IA',
        \$\$${dialplan_xml}\$\$,
        NOW()
    );"
    
    if log_dry "INSERT INTO v_dialplans ..."; then
        return 0
    fi
    
    execute_sql "$sql" "Inserindo dialplan"
    
    if [[ $? -eq 0 ]]; then
        log_success "Dialplan inserido: $dialplan_name"
        return 0
    else
        log_error "Falha ao inserir dialplan"
        return 1
    fi
}

# =============================================================================
# Inserir Dialplans para Todos os Domínios
# =============================================================================

insert_dialplans_all_domains() {
    log_step "Inserindo dialplans para todos os domínios..."
    
    local domains=$(execute_sql "SELECT domain_uuid FROM v_domains WHERE domain_enabled = 'true';" 2>/dev/null)
    
    if [[ -z "$domains" ]]; then
        log_error "Nenhum domínio encontrado"
        return 1
    fi
    
    local count=0
    while read -r domain_uuid; do
        if [[ -n "$domain_uuid" ]]; then
            insert_dialplan "$domain_uuid"
            ((count++))
        fi
    done <<< "$domains"
    
    log_success "Dialplans inseridos para $count domínios"
    return 0
}

# =============================================================================
# Remover Dialplans do Voice AI
# =============================================================================

remove_dialplans() {
    log_step "Removendo dialplans do Voice AI..."
    
    local sql="DELETE FROM v_dialplans WHERE dialplan_name LIKE '%voice_ai%' OR dialplan_name LIKE '%voice_secretary%';"
    
    if log_dry "$sql"; then
        return 0
    fi
    
    local deleted=$(execute_sql "$sql; SELECT changes();" 2>/dev/null)
    log_success "Dialplans removidos"
    return 0
}

# =============================================================================
# Recarregar Dialplan no FreeSWITCH
# =============================================================================

reload_freeswitch_dialplan() {
    log_step "Recarregando dialplan no FreeSWITCH..."
    
    if log_dry "fs_cli -x 'reloadxml'"; then
        return 0
    fi
    
    # Executar reload
    fs_cli -x "reloadxml" >> "$LOG_FILE" 2>&1
    fs_cli -x "xml_flush_cache dialplan" >> "$LOG_FILE" 2>&1
    
    log_success "Dialplan recarregado no FreeSWITCH"
    return 0
}

# =============================================================================
# Inserir Providers Padrão
# =============================================================================

insert_default_providers() {
    local domain_uuid="$1"
    
    log_step "Inserindo providers padrão..."
    
    local providers_sql="
    -- OpenAI Realtime (provider padrão)
    INSERT INTO v_voice_ai_providers (
        voice_ai_provider_uuid, domain_uuid, provider_type, provider_name, 
        display_name, config, is_default, is_enabled, priority
    )
    SELECT 
        gen_random_uuid(), '$domain_uuid'::uuid, 'realtime', 'openai_realtime',
        'OpenAI Realtime', '{\"model\": \"gpt-4o-realtime-preview\", \"voice\": \"alloy\"}'::jsonb,
        true, true, 100
    WHERE NOT EXISTS (
        SELECT 1 FROM v_voice_ai_providers 
        WHERE domain_uuid = '$domain_uuid'::uuid AND provider_type = 'realtime' AND provider_name = 'openai_realtime'
    );
    
    -- OpenAI GPT-4 para LLM
    INSERT INTO v_voice_ai_providers (
        voice_ai_provider_uuid, domain_uuid, provider_type, provider_name,
        display_name, config, is_default, is_enabled, priority
    )
    SELECT
        gen_random_uuid(), '$domain_uuid'::uuid, 'llm', 'openai',
        'OpenAI GPT-4', '{\"model\": \"gpt-4o\"}'::jsonb,
        true, true, 100
    WHERE NOT EXISTS (
        SELECT 1 FROM v_voice_ai_providers
        WHERE domain_uuid = '$domain_uuid'::uuid AND provider_type = 'llm' AND provider_name = 'openai'
    );
    
    -- OpenAI Whisper para STT
    INSERT INTO v_voice_ai_providers (
        voice_ai_provider_uuid, domain_uuid, provider_type, provider_name,
        display_name, config, is_default, is_enabled, priority
    )
    SELECT
        gen_random_uuid(), '$domain_uuid'::uuid, 'stt', 'openai_whisper',
        'OpenAI Whisper', '{\"model\": \"whisper-1\"}'::jsonb,
        true, true, 100
    WHERE NOT EXISTS (
        SELECT 1 FROM v_voice_ai_providers
        WHERE domain_uuid = '$domain_uuid'::uuid AND provider_type = 'stt' AND provider_name = 'openai_whisper'
    );
    
    -- ElevenLabs para TTS
    INSERT INTO v_voice_ai_providers (
        voice_ai_provider_uuid, domain_uuid, provider_type, provider_name,
        display_name, config, is_default, is_enabled, priority
    )
    SELECT
        gen_random_uuid(), '$domain_uuid'::uuid, 'tts', 'elevenlabs',
        'ElevenLabs', '{\"voice_id\": \"21m00Tcm4TlvDq8ikWAM\"}'::jsonb,
        true, true, 100
    WHERE NOT EXISTS (
        SELECT 1 FROM v_voice_ai_providers
        WHERE domain_uuid = '$domain_uuid'::uuid AND provider_type = 'tts' AND provider_name = 'elevenlabs'
    );
    "
    
    if log_dry "INSERT providers padrão..."; then
        return 0
    fi
    
    execute_sql "$providers_sql" "Inserindo providers padrão"
    
    if [[ $? -eq 0 ]]; then
        log_success "Providers padrão inseridos"
        return 0
    else
        log_warn "Alguns providers podem já existir"
        return 0
    fi
}

# =============================================================================
# Inserir Providers para Todos os Domínios
# =============================================================================

insert_providers_all_domains() {
    log_step "Inserindo providers para todos os domínios..."
    
    local domains=$(execute_sql "SELECT domain_uuid FROM v_domains WHERE domain_enabled = 'true';" 2>/dev/null)
    
    if [[ -z "$domains" ]]; then
        log_warn "Nenhum domínio encontrado"
        return 0
    fi
    
    local count=0
    while read -r domain_uuid; do
        if [[ -n "$domain_uuid" ]]; then
            insert_default_providers "$domain_uuid"
            ((count++))
        fi
    done <<< "$domains"
    
    log_success "Providers inseridos para $count domínios"
    return 0
}

# =============================================================================
# Remover Tabelas do Voice AI
# =============================================================================

drop_voice_ai_tables() {
    log_step "Removendo tabelas do Voice AI..."
    
    local tables=(
        "v_voice_messages"
        "v_voice_conversations"
        "v_voice_document_chunks"
        "v_voice_documents"
        "v_voice_transfer_rules"
        "v_voice_transfer_destinations"
        "v_voice_secretaries"
        "v_voice_ai_providers"
    )
    
    for table in "${tables[@]}"; do
        local sql="DROP TABLE IF EXISTS $table CASCADE;"
        
        if log_dry "$sql"; then
            continue
        fi
        
        execute_sql "$sql" "Removendo tabela $table"
    done
    
    log_success "Tabelas do Voice AI removidas"
    return 0
}

# =============================================================================
# Verificar Integridade do Banco
# =============================================================================

verify_database_integrity() {
    log_step "Verificando integridade do banco de dados..."
    
    local errors=0
    
    # Verificar foreign keys para v_domains
    local orphans=$(execute_sql "
        SELECT COUNT(*) FROM v_voice_ai_providers p 
        WHERE NOT EXISTS (SELECT 1 FROM v_domains d WHERE d.domain_uuid = p.domain_uuid);
    " 2>/dev/null)
    
    if [[ "$orphans" -gt 0 ]]; then
        log_warn "Encontrados $orphans registros órfãos em v_voice_ai_providers"
        ((errors++))
    fi
    
    orphans=$(execute_sql "
        SELECT COUNT(*) FROM v_voice_secretaries s 
        WHERE NOT EXISTS (SELECT 1 FROM v_domains d WHERE d.domain_uuid = s.domain_uuid);
    " 2>/dev/null)
    
    if [[ "$orphans" -gt 0 ]]; then
        log_warn "Encontrados $orphans registros órfãos em v_voice_secretaries"
        ((errors++))
    fi
    
    if [[ $errors -eq 0 ]]; then
        log_success "Integridade do banco OK"
        return 0
    else
        log_warn "Encontrados $errors problemas de integridade"
        return 1
    fi
}
