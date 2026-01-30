#!/bin/bash
# =============================================================================
# Voice AI IVR - Installer Firewall Functions
# =============================================================================
# Funções para configuração de firewall (ufw)
# =============================================================================

# =============================================================================
# Detectar Firewall
# =============================================================================

detect_firewall() {
    log_step "Detectando firewall..."
    
    # Verificar se ufw está instalado
    if ! command -v ufw &>/dev/null; then
        log_debug "ufw não está instalado"
        return 1
    fi
    
    # Verificar se está ativo
    local status=$(ufw status 2>/dev/null | head -1)
    
    if echo "$status" | grep -q "active"; then
        log_info "Firewall UFW está ATIVO"
        return 0
    else
        log_debug "Firewall UFW está inativo"
        return 1
    fi
}

# =============================================================================
# Verificar Regras Existentes
# =============================================================================

check_firewall_rules() {
    log_step "Verificando regras de firewall..."
    
    if ! detect_firewall; then
        log_info "Firewall não está ativo, nenhuma configuração necessária"
        return 0
    fi
    
    local issues=0
    
    # Verificar porta 8021 (ESL Inbound - deve estar aberta localmente)
    if ufw status | grep -q "8021"; then
        log_warn "Porta 8021 (ESL) está exposta no firewall - considere restringir"
    fi
    
    # Verificar porta 8022 (ESL Outbound - apenas local)
    if ufw status | grep -q "8022.*ALLOW.*Anywhere"; then
        log_warn "Porta 8022 está exposta externamente - isso é um risco de segurança!"
        ((issues++))
    fi
    
    # Verificar porta 8085 (API - apenas local)
    if ufw status | grep -q "8085.*ALLOW.*Anywhere"; then
        log_warn "Porta 8085 está exposta externamente - considere restringir"
    fi
    
    if [[ $issues -eq 0 ]]; then
        log_success "Configuração de firewall OK"
    fi
    
    return $issues
}

# =============================================================================
# Configurar Firewall para Voice AI
# =============================================================================

configure_firewall() {
    log_step "Configurando firewall para Voice AI..."
    
    if ! detect_firewall; then
        log_info "Firewall não está ativo, pulando configuração"
        return 0
    fi
    
    if log_dry "Configurar regras ufw"; then
        return 0
    fi
    
    # As portas do Voice AI são INTERNAS e não devem ser expostas
    # Vamos apenas documentar e verificar, não abrir portas
    
    echo ""
    echo -e "${YELLOW}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║              IMPORTANTE: Configuração de Firewall            ║${NC}"
    echo -e "${YELLOW}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${YELLOW}║${NC}                                                              ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}  O Voice AI usa as seguintes portas INTERNAS:               ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}                                                              ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}  • 8021 - ESL Inbound (FreeSWITCH)                           ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}  • 8022 - ESL Outbound (Voice AI)                            ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}  • 8085 - API HTTP (Voice AI)                                ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}                                                              ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}  ${RED}NUNCA exponha estas portas para a internet!${NC}               ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}  Elas devem ser acessíveis apenas via localhost (127.0.0.1)  ${YELLOW}║${NC}"
    echo -e "${YELLOW}║${NC}                                                              ${YELLOW}║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    # Verificar se há regras problemáticas
    check_firewall_rules
    
    return 0
}

# =============================================================================
# Bloquear Portas Externas (se necessário)
# =============================================================================

secure_voice_ai_ports() {
    log_step "Verificando segurança das portas..."
    
    if ! detect_firewall; then
        return 0
    fi
    
    local ports_to_block=(8022 8085)
    
    for port in "${ports_to_block[@]}"; do
        # Verificar se porta está aberta para qualquer origem
        if ufw status | grep -q "$port.*ALLOW.*Anywhere"; then
            log_warn "Porta $port está exposta externamente"
            
            if confirm "Deseja restringir porta $port para apenas localhost?"; then
                if log_dry "ufw delete allow $port && ufw allow from 127.0.0.1 to any port $port"; then
                    continue
                fi
                
                # Remover regra existente
                ufw delete allow "$port" 2>/dev/null || true
                ufw delete allow "$port/tcp" 2>/dev/null || true
                
                # Adicionar regra apenas para localhost
                ufw allow from 127.0.0.1 to any port "$port" >> "$LOG_FILE" 2>&1
                
                log_success "Porta $port restrita para localhost"
            fi
        fi
    done
    
    # Recarregar firewall
    if [[ "$DRY_RUN" != "true" ]]; then
        ufw reload >> "$LOG_FILE" 2>&1
    fi
    
    return 0
}

# =============================================================================
# Mostrar Status do Firewall
# =============================================================================

show_firewall_status() {
    echo ""
    echo -e "${CYAN}Status do Firewall:${NC}"
    echo "────────────────────────────────────────────────────────────────"
    
    if ! command -v ufw &>/dev/null; then
        echo "  UFW não está instalado"
    else
        ufw status verbose 2>/dev/null | head -20
    fi
    
    echo "────────────────────────────────────────────────────────────────"
    echo ""
}

# =============================================================================
# Verificação de Segurança
# =============================================================================

security_check() {
    log_step "Verificação de segurança..."
    
    local issues=0
    
    # Verificar portas expostas
    echo ""
    echo -e "${CYAN}Portas em escuta:${NC}"
    
    for port in 8021 8022 8085; do
        local binding=$(ss -tlnp 2>/dev/null | grep ":$port " | awk '{print $4}')
        
        if [[ -z "$binding" ]]; then
            print_checklist_item "ok" "Porta $port: não está escutando"
        elif echo "$binding" | grep -q "127.0.0.1\|0.0.0.0"; then
            if echo "$binding" | grep -q "0.0.0.0"; then
                print_checklist_item "warn" "Porta $port: escutando em todas interfaces ($binding)"
                ((issues++))
            else
                print_checklist_item "ok" "Porta $port: apenas localhost ($binding)"
            fi
        else
            print_checklist_item "ok" "Porta $port: $binding"
        fi
    done
    
    echo ""
    
    if [[ $issues -gt 0 ]]; then
        log_warn "Encontrados $issues avisos de segurança"
        echo ""
        echo -e "${YELLOW}Recomendação: Configure o Voice AI para escutar apenas em 127.0.0.1${NC}"
        echo -e "${YELLOW}Edite /opt/voice-ai/.env e defina:${NC}"
        echo -e "${YELLOW}  ESL_HOST=127.0.0.1${NC}"
        echo ""
    else
        log_success "Verificação de segurança OK"
    fi
    
    return $issues
}
