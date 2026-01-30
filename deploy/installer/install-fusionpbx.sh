#!/bin/bash
# =============================================================================
# Voice AI IVR - FusionPBX Installer
# =============================================================================
# Instalador automatizado para Voice AI IVR em servidores FusionPBX
# 
# Uso:
#   sudo ./install-fusionpbx.sh [opções]
#
# Opções:
#   --install       Instalação nova (padrão)
#   --upgrade       Atualização preservando configurações
#   --uninstall     Remoção completa
#   --check         Apenas verificar ambiente
#   --dry-run       Preview sem executar alterações
#   --force         Continuar mesmo com avisos
#   --verbose       Log detalhado
#   --help          Mostrar ajuda
#
# =============================================================================

set -o pipefail

# Diretório do script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Carregar bibliotecas
source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/detect.sh"
source "$SCRIPT_DIR/lib/database.sh"
source "$SCRIPT_DIR/lib/fusionpbx-app.sh"
source "$SCRIPT_DIR/lib/voice-service.sh"
source "$SCRIPT_DIR/lib/dialplan.sh"
source "$SCRIPT_DIR/lib/rollback.sh"
source "$SCRIPT_DIR/lib/firewall.sh"

# =============================================================================
# Variáveis Globais
# =============================================================================

MODE="install"
START_TIME=""

# Paths (podem ser sobrescritos via argumentos)
VOICE_AI_SOURCE="${VOICE_AI_SOURCE:-$(dirname "$SCRIPT_DIR")/..}"
FUSIONPBX_APP_SOURCE="${FUSIONPBX_APP_SOURCE:-$VOICE_AI_SOURCE/fusionpbx-app/voice_secretary}"
VOICE_SERVICE_SOURCE="${VOICE_SERVICE_SOURCE:-$VOICE_AI_SOURCE/voice-ai-service}"
MIGRATIONS_SOURCE="${MIGRATIONS_SOURCE:-$SCRIPT_DIR/migrations}"
TEMPLATES_SOURCE="${TEMPLATES_SOURCE:-$SCRIPT_DIR/templates}"

# =============================================================================
# Funções de Ajuda
# =============================================================================

show_help() {
    cat << EOF
Voice AI IVR - Instalador para FusionPBX
Versão: $INSTALLER_VERSION

Uso:
  sudo ./install-fusionpbx.sh [opções]

Modos de Operação:
  --install       Instalação nova (padrão)
  --upgrade       Atualização preservando configurações
  --uninstall     Remoção completa
  --check         Apenas verificar ambiente

Opções:
  --dry-run                 Preview sem executar alterações
  --force                   Continuar mesmo com avisos
  --verbose                 Log detalhado
  --skip-dialplan           Não criar dialplans
  --skip-providers          Não inserir providers padrão
  --fusionpbx-path=PATH     Caminho do FusionPBX
  --help, -h                Mostrar esta ajuda

Exemplos:
  # Instalação padrão
  sudo ./install-fusionpbx.sh

  # Preview das alterações
  sudo ./install-fusionpbx.sh --dry-run

  # Atualização
  sudo ./install-fusionpbx.sh --upgrade

  # Verificar ambiente apenas
  sudo ./install-fusionpbx.sh --check

EOF
    exit 0
}

# =============================================================================
# Parse de Argumentos
# =============================================================================

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --install)
                MODE="install"
                ;;
            --upgrade)
                MODE="upgrade"
                ;;
            --uninstall)
                MODE="uninstall"
                ;;
            --check)
                MODE="check"
                ;;
            --dry-run)
                DRY_RUN=true
                ;;
            --force)
                FORCE=true
                ;;
            --verbose|-v)
                VERBOSE=true
                ;;
            --skip-dialplan)
                SKIP_DIALPLAN=true
                ;;
            --skip-providers)
                SKIP_PROVIDERS=true
                ;;
            --fusionpbx-path=*)
                FUSIONPBX_PATH="${1#*=}"
                ;;
            --help|-h)
                show_help
                ;;
            *)
                log_error "Opção desconhecida: $1"
                echo "Use --help para ver as opções disponíveis"
                exit 1
                ;;
        esac
        shift
    done
}

# =============================================================================
# Verificação de Pré-requisitos
# =============================================================================

check_prerequisites() {
    log_step "Verificando pré-requisitos..."
    echo ""
    
    local errors=0
    
    # Root
    if [[ $EUID -eq 0 ]]; then
        print_checklist_item "ok" "Executando como root"
    else
        print_checklist_item "fail" "Não está executando como root"
        ((errors++))
    fi
    
    # Sistema operacional
    local os=$(check_os 2>/dev/null)
    if [[ -n "$os" ]]; then
        print_checklist_item "ok" "Sistema operacional: $os"
    fi
    
    # Espaço em disco
    if check_disk_space 500 /opt 2>/dev/null; then
        print_checklist_item "ok" "Espaço em disco suficiente"
    else
        print_checklist_item "fail" "Espaço em disco insuficiente"
        ((errors++))
    fi
    
    # FusionPBX
    if [[ -n "$FUSIONPBX_PATH" ]] && [[ -d "$FUSIONPBX_PATH" ]]; then
        print_checklist_item "ok" "FusionPBX: $FUSIONPBX_PATH"
    else
        print_checklist_item "fail" "FusionPBX não encontrado"
        ((errors++))
    fi
    
    # FreeSWITCH
    if check_service freeswitch; then
        print_checklist_item "ok" "FreeSWITCH está rodando"
    else
        print_checklist_item "fail" "FreeSWITCH não está rodando"
        ((errors++))
    fi
    
    # ESL
    if check_port "${ESL_PORT:-8021}"; then
        print_checklist_item "ok" "ESL acessível na porta ${ESL_PORT:-8021}"
    else
        print_checklist_item "fail" "ESL não acessível"
        ((errors++))
    fi
    
    # Python
    local python_cmd=$(detect_python 2>/dev/null)
    if [[ -n "$python_cmd" ]]; then
        print_checklist_item "ok" "Python 3.11+ disponível: $python_cmd"
    else
        print_checklist_item "warn" "Python 3.11+ não encontrado (será instalado)"
    fi
    
    # Banco de dados
    if [[ -n "$DB_HOST" ]] && [[ -n "$DB_USER" ]]; then
        if test_database_connection 2>/dev/null; then
            print_checklist_item "ok" "Conexão com banco de dados OK"
        else
            print_checklist_item "fail" "Falha na conexão com banco de dados"
            ((errors++))
        fi
    else
        print_checklist_item "fail" "Credenciais do banco não detectadas"
        ((errors++))
    fi
    
    # Comandos necessários
    for cmd in rsync tar nc psql; do
        if check_command "$cmd"; then
            print_checklist_item "ok" "Comando disponível: $cmd"
        else
            print_checklist_item "warn" "Comando não encontrado: $cmd"
        fi
    done
    
    echo ""
    
    if [[ $errors -gt 0 ]]; then
        log_error "Pré-requisitos não atendidos: $errors erro(s)"
        return 1
    fi
    
    log_success "Todos os pré-requisitos atendidos"
    return 0
}

# =============================================================================
# Instalação
# =============================================================================

do_install() {
    log_step "Iniciando instalação do Voice AI IVR..."
    echo ""
    
    # Inicializar rollback
    init_rollback
    set_checkpoint "INIT"
    
    # Fase 1: Banco de dados
    set_checkpoint "DATABASE"
    log_step "Fase 1/5: Banco de Dados"
    
    backup_database_for_rollback
    
    if [[ -d "$MIGRATIONS_SOURCE" ]]; then
        run_migrations "$MIGRATIONS_SOURCE" || {
            execute_rollback "Falha nas migrations"
            return 1
        }
    else
        log_warn "Diretório de migrations não encontrado, pulando..."
    fi
    
    # Inserir providers
    if [[ "$SKIP_PROVIDERS" != "true" ]]; then
        insert_providers_all_domains
    fi
    
    # Fase 2: App FusionPBX
    set_checkpoint "APP"
    log_step "Fase 2/5: App FusionPBX"
    
    if [[ -d "$FUSIONPBX_APP_SOURCE" ]]; then
        install_app_complete "$FUSIONPBX_APP_SOURCE" || {
            execute_rollback "Falha na instalação do app"
            return 1
        }
    else
        log_error "Source do app não encontrado: $FUSIONPBX_APP_SOURCE"
        return 1
    fi
    
    # Fase 3: Serviço Python
    set_checkpoint "SERVICE"
    log_step "Fase 3/5: Serviço Python"
    
    if [[ -d "$VOICE_SERVICE_SOURCE" ]]; then
        install_voice_service_complete "$VOICE_SERVICE_SOURCE" "$TEMPLATES_SOURCE" || {
            execute_rollback "Falha na instalação do serviço"
            return 1
        }
    else
        log_error "Source do serviço não encontrado: $VOICE_SERVICE_SOURCE"
        return 1
    fi
    
    # Fase 4: Dialplan
    set_checkpoint "DIALPLAN"
    log_step "Fase 4/5: Dialplan"
    
    if [[ "$SKIP_DIALPLAN" != "true" ]]; then
        install_dialplans_complete || {
            log_warn "Falha na configuração de dialplans (não crítico)"
        }
    fi
    
    # Fase 5: Firewall e Segurança
    set_checkpoint "FIREWALL"
    log_step "Fase 5/6: Firewall e Segurança"
    
    configure_firewall
    security_check
    
    # Fase 6: Verificação
    set_checkpoint "VERIFY"
    log_step "Fase 6/6: Verificação"
    
    verify_installation
    
    # Instalar aliases
    install_aliases
    
    # Limpar rollback após sucesso
    set_checkpoint "COMPLETE"
    cleanup_rollback
    
    return 0
}

# =============================================================================
# Atualização
# =============================================================================

do_upgrade() {
    log_step "Iniciando atualização do Voice AI IVR..."
    echo ""
    
    # Verificar instalação existente
    if [[ "$VOICE_AI_INSTALLED" == "false" ]]; then
        log_warn "Nenhuma instalação existente detectada"
        if confirm "Deseja fazer uma instalação nova?"; then
            do_install
            return $?
        fi
        return 1
    fi
    
    # Inicializar rollback
    init_rollback
    
    # Parar serviços
    stop_voice_ai_services
    
    # Backup
    backup_database_for_rollback
    backup_for_rollback "$VOICE_AI_INSTALL_PATH" "dir"
    
    # Atualizar código
    upgrade_fusionpbx_app "$FUSIONPBX_APP_SOURCE"
    
    # Atualizar serviço (preservando .env)
    local env_backup=$(backup_file "$VOICE_AI_INSTALL_PATH/.env")
    copy_voice_service_code "$VOICE_SERVICE_SOURCE"
    if [[ -n "$env_backup" ]]; then
        cp "$env_backup" "$VOICE_AI_INSTALL_PATH/.env"
    fi
    
    # Atualizar dependências
    install_python_dependencies
    
    # Executar migrations incrementais
    if [[ -d "$MIGRATIONS_SOURCE" ]]; then
        run_migrations "$MIGRATIONS_SOURCE"
    fi
    
    # Reiniciar serviços
    enable_and_start_services
    
    # Verificar
    verify_installation
    
    cleanup_rollback
    
    log_success "Atualização concluída"
    return 0
}

# =============================================================================
# Desinstalação
# =============================================================================

do_uninstall() {
    log_step "Iniciando desinstalação do Voice AI IVR..."
    echo ""
    
    echo -e "${RED}ATENÇÃO: Esta operação irá remover o Voice AI IVR.${NC}"
    echo ""
    
    if ! confirm "Deseja continuar com a desinstalação?"; then
        log_info "Desinstalação cancelada"
        return 0
    fi
    
    # Parar e remover serviços
    remove_voice_ai_services
    
    # Remover app FusionPBX
    remove_fusionpbx_app
    
    # Remover dialplans
    remove_voice_ai_dialplans
    reload_dialplan
    
    # Perguntar sobre banco de dados
    if confirm "Remover tabelas do banco de dados? (PERDA DE DADOS)"; then
        backup_database
        drop_voice_ai_tables
    else
        log_info "Tabelas do banco preservadas"
    fi
    
    # Remover instalação
    remove_voice_ai_installation
    
    # Perguntar sobre usuário
    if confirm "Remover usuário '$VOICE_AI_USER'?"; then
        userdel "$VOICE_AI_USER" 2>/dev/null || true
        log_info "Usuário removido"
    fi
    
    log_success "Desinstalação concluída"
    return 0
}

# =============================================================================
# Verificação
# =============================================================================

verify_installation() {
    log_step "Verificando instalação..."
    echo ""
    
    local errors=0
    
    # Verificar serviço
    verify_service_status || ((errors++))
    
    # Verificar app
    verify_app_registration || ((errors++))
    
    # Verificar banco
    check_voice_ai_tables || ((errors++))
    
    # Verificar dialplans
    verify_dialplan_installation || ((errors++))
    
    echo ""
    
    if [[ $errors -eq 0 ]]; then
        log_success "Verificação concluída sem erros"
    else
        log_warn "Verificação concluída com $errors aviso(s)"
    fi
    
    return $errors
}

# =============================================================================
# Resumo Final
# =============================================================================

show_final_summary() {
    local status="$1"
    local duration=$(get_elapsed "$START_TIME")
    
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    
    if [[ "$status" == "success" ]]; then
        echo -e "${CYAN}║${NC}  ${GREEN}✓ Instalação concluída com sucesso!${NC}                         ${CYAN}║${NC}"
    else
        echo -e "${CYAN}║${NC}  ${RED}✗ Instalação falhou${NC}                                         ${CYAN}║${NC}"
    fi
    
    echo -e "${CYAN}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  Tempo total: ${duration}s                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  Log: $LOG_FILE  ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    
    if [[ "$status" == "success" ]]; then
        echo -e "${CYAN}╠══════════════════════════════════════════════════════════════╣${NC}"
        echo -e "${CYAN}║${NC}  ${BOLD}Próximos passos:${NC}                                            ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  1. Edite o arquivo de configuração:                         ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     nano /opt/voice-ai/.env                                  ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  2. Configure as API keys (OPENAI_API_KEY, etc.)             ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  3. Inicie o serviço:                                        ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     systemctl start voice-ai-realtime                        ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  4. Acesse o FusionPBX e configure uma secretária:           ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     Apps > Secretária Virtual                                ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    fi
    
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# =============================================================================
# Main
# =============================================================================

main() {
    # Parse argumentos
    parse_arguments "$@"
    
    # Verificar root (exceto para --help)
    check_root
    
    # Inicializar logging
    init_logging
    
    # Mostrar banner
    show_banner
    
    # Iniciar timer
    START_TIME=$(start_timer)
    
    # Modo dry-run
    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${CYAN}[MODO DRY-RUN] Nenhuma alteração será feita${NC}"
        echo ""
    fi
    
    # Detectar ambiente
    run_detection
    
    if [[ $? -ne 0 ]] && [[ "$FORCE" != "true" ]]; then
        log_error "Falha na detecção de ambiente. Use --force para continuar."
        exit 1
    fi
    
    # Mostrar configurações detectadas
    show_detected_config
    
    # Verificar pré-requisitos
    if [[ "$MODE" != "check" ]]; then
        check_prerequisites || {
            if [[ "$FORCE" != "true" ]]; then
                log_error "Pré-requisitos não atendidos. Use --force para continuar."
                exit 1
            fi
        }
    fi
    
    # Confirmar execução
    if [[ "$MODE" != "check" ]] && [[ "$DRY_RUN" != "true" ]]; then
        if ! confirm "Deseja continuar com a $MODE?"; then
            log_info "Operação cancelada pelo usuário"
            exit 0
        fi
    fi
    
    echo ""
    
    # Executar modo selecionado
    local exit_code=0
    
    case "$MODE" in
        install)
            do_install
            exit_code=$?
            ;;
        upgrade)
            do_upgrade
            exit_code=$?
            ;;
        uninstall)
            do_uninstall
            exit_code=$?
            ;;
        check)
            verify_installation
            exit_code=$?
            ;;
    esac
    
    # Mostrar resumo
    if [[ "$MODE" != "check" ]]; then
        if [[ $exit_code -eq 0 ]]; then
            show_final_summary "success"
        else
            show_final_summary "failed"
        fi
    fi
    
    exit $exit_code
}

# Executar
main "$@"
