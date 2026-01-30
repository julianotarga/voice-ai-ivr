#!/bin/bash
# =============================================================================
# Voice AI IVR - Installer Dialplan Functions
# =============================================================================
# Funções para configuração de dialplan no FusionPBX
# =============================================================================

# =============================================================================
# Verificar Dialplan Existente
# =============================================================================

check_existing_dialplan() {
    local dialplan_name="${1:-voice_ai_secretary}"
    
    local count=$(execute_sql "
        SELECT COUNT(*) FROM v_dialplans 
        WHERE dialplan_name LIKE '%${dialplan_name}%' 
           OR dialplan_name LIKE '%voice_secretary%';
    " 2>/dev/null)
    
    if [[ "$count" -gt 0 ]]; then
        log_debug "Dialplan existente encontrado: $count registros"
        return 0
    fi
    
    return 1
}

# =============================================================================
# Criar Dialplan para Domínio
# =============================================================================

create_dialplan_for_domain() {
    local domain_uuid="$1"
    local dialplan_name="${2:-voice_ai_secretary}"
    local dialplan_context="${3:-public}"
    local dialplan_order="${4:-5}"
    
    log_debug "Criando dialplan para domínio: $domain_uuid"
    
    # XML do dialplan
    local dialplan_xml='<extension name="voice_ai_secretary" continue="false">
  <condition field="${voice_secretary_uuid}" expression="^(.+)$">
    <action application="set" data="hangup_after_bridge=true"/>
    <action application="set" data="continue_on_fail=false"/>
    <action application="answer"/>
    <action application="socket" data="127.0.0.1:8022 async full"/>
  </condition>
</extension>'
    
    # Verificar se já existe para este domínio
    local exists=$(execute_sql "
        SELECT COUNT(*) FROM v_dialplans 
        WHERE dialplan_name = '$dialplan_name' 
          AND domain_uuid = '$domain_uuid'::uuid;
    " 2>/dev/null)
    
    if [[ "$exists" -gt 0 ]]; then
        log_debug "Dialplan já existe para este domínio"
        return 0
    fi
    
    # Inserir dialplan
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
        'Voice AI Secretary - ESL Outbound para IA. Ativado quando voice_secretary_uuid está definido.',
        \$dialplan\$${dialplan_xml}\$dialplan\$,
        NOW()
    );"
    
    if log_dry "INSERT INTO v_dialplans (domain: $domain_uuid)"; then
        return 0
    fi
    
    execute_sql "$sql" 2>/dev/null
    
    if [[ $? -eq 0 ]]; then
        log_debug "Dialplan criado para domínio: $domain_uuid"
        return 0
    else
        log_error "Falha ao criar dialplan para domínio: $domain_uuid"
        return 1
    fi
}

# =============================================================================
# Criar Dialplan para Extensão Específica
# =============================================================================

create_extension_dialplan() {
    local domain_uuid="$1"
    local extension="$2"
    local secretary_uuid="$3"
    local dialplan_context="${4:-public}"
    
    local dialplan_name="voice_ai_ext_${extension}"
    
    log_debug "Criando dialplan para extensão $extension"
    
    # XML do dialplan para extensão específica
    local dialplan_xml="<extension name=\"voice_ai_${extension}\" continue=\"false\">
  <condition field=\"destination_number\" expression=\"^${extension}\$\">
    <action application=\"set\" data=\"voice_secretary_uuid=${secretary_uuid}\"/>
    <action application=\"set\" data=\"hangup_after_bridge=true\"/>
    <action application=\"answer\"/>
    <action application=\"socket\" data=\"127.0.0.1:8022 async full\"/>
  </condition>
</extension>"
    
    # Verificar se já existe
    local exists=$(execute_sql "
        SELECT COUNT(*) FROM v_dialplans 
        WHERE dialplan_name = '$dialplan_name' 
          AND domain_uuid = '$domain_uuid'::uuid;
    " 2>/dev/null)
    
    if [[ "$exists" -gt 0 ]]; then
        log_debug "Dialplan para extensão $extension já existe"
        return 0
    fi
    
    # Inserir
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
        '$extension',
        '$dialplan_context',
        'false',
        10,
        'true',
        'Voice AI Secretary - Extensão $extension',
        \$dialplan\$${dialplan_xml}\$dialplan\$,
        NOW()
    );"
    
    if log_dry "INSERT INTO v_dialplans (extension: $extension)"; then
        return 0
    fi
    
    execute_sql "$sql" 2>/dev/null
    
    return $?
}

# =============================================================================
# Criar Dialplans para Todos os Domínios
# =============================================================================

create_dialplans_all_domains() {
    log_step "Criando dialplans para todos os domínios..."
    
    local domains=$(execute_sql "
        SELECT domain_uuid FROM v_domains 
        WHERE domain_enabled = 'true';
    " 2>/dev/null)
    
    if [[ -z "$domains" ]]; then
        log_error "Nenhum domínio habilitado encontrado"
        return 1
    fi
    
    local count=0
    local errors=0
    
    while read -r domain_uuid; do
        if [[ -n "$domain_uuid" ]]; then
            if create_dialplan_for_domain "$domain_uuid"; then
                ((count++))
            else
                ((errors++))
            fi
        fi
    done <<< "$domains"
    
    if [[ $errors -gt 0 ]]; then
        log_warn "Dialplans criados: $count, Erros: $errors"
    else
        log_success "Dialplans criados para $count domínios"
    fi
    
    return 0
}

# =============================================================================
# Remover Dialplans do Voice AI
# =============================================================================

remove_voice_ai_dialplans() {
    log_step "Removendo dialplans do Voice AI..."
    
    if log_dry "DELETE FROM v_dialplans WHERE dialplan_name LIKE 'voice_ai%'"; then
        return 0
    fi
    
    execute_sql "
        DELETE FROM v_dialplans 
        WHERE dialplan_name LIKE 'voice_ai%' 
           OR dialplan_name LIKE 'voice_secretary%';
    " 2>/dev/null
    
    log_success "Dialplans do Voice AI removidos"
    return 0
}

# =============================================================================
# Atualizar Dialplan Existente
# =============================================================================

update_dialplan() {
    local dialplan_uuid="$1"
    local new_order="${2:-5}"
    
    log_debug "Atualizando dialplan: $dialplan_uuid"
    
    local sql="UPDATE v_dialplans SET
        dialplan_order = $new_order,
        dialplan_continue = 'false',
        dialplan_enabled = 'true',
        update_date = NOW()
    WHERE dialplan_uuid = '$dialplan_uuid'::uuid;"
    
    if log_dry "UPDATE v_dialplans SET ..."; then
        return 0
    fi
    
    execute_sql "$sql" 2>/dev/null
    return $?
}

# =============================================================================
# Recarregar Dialplan no FreeSWITCH
# =============================================================================

reload_dialplan() {
    log_step "Recarregando dialplan no FreeSWITCH..."
    
    if log_dry "fs_cli -x 'reloadxml'"; then
        return 0
    fi
    
    # Verificar se fs_cli está disponível
    if ! command -v fs_cli &>/dev/null; then
        log_warn "fs_cli não encontrado, tentando via ESL..."
        return reload_dialplan_via_esl
    fi
    
    # Recarregar XML
    fs_cli -x "reloadxml" >> "$LOG_FILE" 2>&1
    
    # Limpar cache de dialplan
    fs_cli -x "xml_flush_cache dialplan" >> "$LOG_FILE" 2>&1
    
    # Verificar se recarregou
    sleep 1
    local result=$(fs_cli -x "show channels count" 2>/dev/null)
    
    if [[ -n "$result" ]]; then
        log_success "Dialplan recarregado no FreeSWITCH"
        return 0
    else
        log_warn "Não foi possível verificar reload do FreeSWITCH"
        return 1
    fi
}

reload_dialplan_via_esl() {
    log_debug "Tentando reload via ESL..."
    
    # Usar netcat para enviar comando ESL
    {
        echo "auth ${ESL_PASSWORD:-ClueCon}"
        sleep 0.1
        echo "api reloadxml"
        sleep 0.1
        echo "exit"
    } | nc -w 2 "${ESL_HOST:-127.0.0.1}" "${ESL_PORT:-8021}" >> "$LOG_FILE" 2>&1
    
    if [[ $? -eq 0 ]]; then
        log_success "Reload via ESL executado"
        return 0
    else
        log_error "Falha no reload via ESL"
        return 1
    fi
}

# =============================================================================
# Listar Dialplans do Voice AI
# =============================================================================

list_voice_ai_dialplans() {
    log_step "Listando dialplans do Voice AI..."
    
    local result=$(execute_sql "
        SELECT 
            d.dialplan_name,
            d.dialplan_context,
            d.dialplan_order,
            d.dialplan_enabled,
            dom.domain_name
        FROM v_dialplans d
        JOIN v_domains dom ON d.domain_uuid = dom.domain_uuid
        WHERE d.dialplan_name LIKE 'voice_ai%' 
           OR d.dialplan_name LIKE 'voice_secretary%'
        ORDER BY dom.domain_name, d.dialplan_order;
    " 2>/dev/null)
    
    if [[ -z "$result" ]]; then
        log_info "Nenhum dialplan do Voice AI encontrado"
        return 0
    fi
    
    echo ""
    echo "Dialplans do Voice AI:"
    echo "────────────────────────────────────────────────────"
    echo "$result" | while read -r line; do
        echo "  $line"
    done
    echo "────────────────────────────────────────────────────"
    echo ""
    
    return 0
}

# =============================================================================
# Verificar Dialplan
# =============================================================================

verify_dialplan_installation() {
    log_step "Verificando instalação de dialplans..."
    
    local errors=0
    
    # Contar dialplans
    local count=$(execute_sql "
        SELECT COUNT(*) FROM v_dialplans 
        WHERE dialplan_name LIKE 'voice_ai%' 
           OR dialplan_name LIKE 'voice_secretary%';
    " 2>/dev/null)
    
    if [[ "$count" -gt 0 ]]; then
        print_checklist_item "ok" "Dialplans encontrados: $count"
    else
        print_checklist_item "fail" "Nenhum dialplan do Voice AI encontrado"
        ((errors++))
    fi
    
    # Verificar se estão habilitados
    local enabled=$(execute_sql "
        SELECT COUNT(*) FROM v_dialplans 
        WHERE (dialplan_name LIKE 'voice_ai%' OR dialplan_name LIKE 'voice_secretary%')
          AND dialplan_enabled = 'true';
    " 2>/dev/null)
    
    if [[ "$enabled" -gt 0 ]]; then
        print_checklist_item "ok" "Dialplans habilitados: $enabled"
    else
        print_checklist_item "warn" "Nenhum dialplan habilitado"
    fi
    
    # Verificar ordem (deve ser baixa para executar antes do catch-all)
    local low_order=$(execute_sql "
        SELECT COUNT(*) FROM v_dialplans 
        WHERE (dialplan_name LIKE 'voice_ai%' OR dialplan_name LIKE 'voice_secretary%')
          AND dialplan_order <= 50;
    " 2>/dev/null)
    
    if [[ "$low_order" -gt 0 ]]; then
        print_checklist_item "ok" "Dialplans com ordem baixa (prioridade alta): $low_order"
    else
        print_checklist_item "warn" "Dialplans podem ter ordem muito alta"
    fi
    
    return $errors
}

# =============================================================================
# Instalação Completa de Dialplans
# =============================================================================

install_dialplans_complete() {
    log_step "Instalando dialplans do Voice AI..."
    
    # 1. Criar dialplans para todos os domínios
    create_dialplans_all_domains || return 1
    
    # 2. Recarregar no FreeSWITCH
    reload_dialplan
    
    # 3. Verificar
    verify_dialplan_installation
    
    log_success "Instalação de dialplans concluída"
    return 0
}
