#!/bin/bash
# =============================================================================
# Voice AI IVR - Uninstall Tool
# =============================================================================
# Remove completamente o Voice AI IVR do sistema
# =============================================================================

set -e

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${CYAN}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Verificar root
if [[ $EUID -ne 0 ]]; then
    log_error "Este script precisa ser executado como root"
    exit 1
fi

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}              Voice AI IVR - Desinstalação                    ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${RED}ATENÇÃO: Esta operação irá remover o Voice AI IVR.${NC}"
echo ""
echo "Serão removidos:"
echo "  - Serviços systemd (voice-ai-realtime, voice-ai-service)"
echo "  - Diretório /opt/voice-ai"
echo "  - App FusionPBX (/var/www/fusionpbx/app/voice_secretary)"
echo "  - Logs (/var/log/voice-ai)"
echo "  - Configuração logrotate"
echo ""

read -p "Deseja continuar? [y/N] " confirm
if [[ ! "$confirm" =~ ^[yY]$ ]]; then
    log_info "Desinstalação cancelada"
    exit 0
fi

echo ""

# =============================================================================
# Parar serviços
# =============================================================================

log_info "Parando serviços..."

for service in voice-ai-realtime voice-ai-service; do
    if systemctl is-active --quiet "$service" 2>/dev/null; then
        systemctl stop "$service" 2>/dev/null || true
        log_success "Serviço parado: $service"
    fi
done

# =============================================================================
# Remover units systemd
# =============================================================================

log_info "Removendo units systemd..."

for service in voice-ai-realtime voice-ai-service; do
    if [[ -f "/etc/systemd/system/${service}.service" ]]; then
        systemctl disable "$service" 2>/dev/null || true
        rm -f "/etc/systemd/system/${service}.service"
        log_success "Unit removida: ${service}.service"
    fi
done

systemctl daemon-reload

# =============================================================================
# Remover diretórios
# =============================================================================

log_info "Removendo diretórios..."

if [[ -d /opt/voice-ai ]]; then
    rm -rf /opt/voice-ai
    log_success "Removido: /opt/voice-ai"
fi

if [[ -d /var/log/voice-ai ]]; then
    rm -rf /var/log/voice-ai
    log_success "Removido: /var/log/voice-ai"
fi

if [[ -d /tmp/voice-ai-announcements ]]; then
    rm -rf /tmp/voice-ai-announcements
    log_success "Removido: /tmp/voice-ai-announcements"
fi

# =============================================================================
# Remover app FusionPBX
# =============================================================================

log_info "Removendo app FusionPBX..."

FUSIONPBX_PATH="/var/www/fusionpbx"
if [[ -d "$FUSIONPBX_PATH/app/voice_secretary" ]]; then
    rm -rf "$FUSIONPBX_PATH/app/voice_secretary"
    log_success "Removido: $FUSIONPBX_PATH/app/voice_secretary"
fi

# =============================================================================
# Remover logrotate
# =============================================================================

if [[ -f /etc/logrotate.d/voice-ai ]]; then
    rm -f /etc/logrotate.d/voice-ai
    log_success "Removido: /etc/logrotate.d/voice-ai"
fi

# =============================================================================
# Perguntar sobre banco de dados
# =============================================================================

echo ""
read -p "Remover tabelas do banco de dados? (PERDA DE DADOS) [y/N] " remove_db

if [[ "$remove_db" =~ ^[yY]$ ]]; then
    log_warn "Removendo tabelas do banco de dados..."
    
    # Tentar ler credenciais
    DB_HOST="localhost"
    DB_PORT="5432"
    DB_NAME="fusionpbx"
    
    if [[ -f "$FUSIONPBX_PATH/resources/config.php" ]]; then
        DB_USER=$(grep -oP "(?<=\\\$db_username.*=.*['\"])[^'\"]*" "$FUSIONPBX_PATH/resources/config.php" | head -1)
        DB_PASS=$(grep -oP "(?<=\\\$db_password.*=.*['\"])[^'\"]*" "$FUSIONPBX_PATH/resources/config.php" | head -1)
    fi
    
    if [[ -n "$DB_USER" ]] && [[ -n "$DB_PASS" ]]; then
        # Fazer backup primeiro
        backup_file="/tmp/voice-ai-backup-$(date +%Y%m%d_%H%M%S).sql"
        log_info "Criando backup em: $backup_file"
        PGPASSWORD="$DB_PASS" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" -t 'v_voice_*' > "$backup_file" 2>/dev/null || true
        
        # Remover tabelas
        tables=(
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
            PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
                -c "DROP TABLE IF EXISTS $table CASCADE;" 2>/dev/null && \
                log_success "Tabela removida: $table"
        done
        
        # Remover dialplans
        PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -c "DELETE FROM v_dialplans WHERE dialplan_name LIKE 'voice_ai%' OR dialplan_name LIKE 'voice_secretary%';" 2>/dev/null && \
            log_success "Dialplans removidos"
        
        # Remover permissões e menus
        PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -c "DELETE FROM v_group_permissions WHERE permission_name LIKE 'voice_secretary%';" 2>/dev/null
        PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -c "DELETE FROM v_menu_items WHERE menu_item_link LIKE '%voice_secretary%';" 2>/dev/null
        
        log_success "Dados do banco removidos (backup em $backup_file)"
    else
        log_error "Não foi possível detectar credenciais do banco"
    fi
fi

# =============================================================================
# Perguntar sobre usuário
# =============================================================================

echo ""
read -p "Remover usuário 'voiceai'? [y/N] " remove_user

if [[ "$remove_user" =~ ^[yY]$ ]]; then
    if id voiceai &>/dev/null; then
        userdel voiceai 2>/dev/null || true
        log_success "Usuário removido: voiceai"
    fi
fi

# =============================================================================
# Recarregar FreeSWITCH
# =============================================================================

log_info "Recarregando FreeSWITCH..."
fs_cli -x "reloadxml" 2>/dev/null || true
fs_cli -x "xml_flush_cache dialplan" 2>/dev/null || true

# =============================================================================
# Finalização
# =============================================================================

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║${NC}              Desinstalação concluída                         ${GREEN}║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
