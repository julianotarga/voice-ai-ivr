#!/bin/bash
# =============================================================================
# Voice AI IVR - Status Check Tool
# =============================================================================
# Verifica o status de todos os componentes do Voice AI
# =============================================================================

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Funções
check_ok() { echo -e "  ${GREEN}✓${NC} $1"; }
check_fail() { echo -e "  ${RED}✗${NC} $1"; }
check_warn() { echo -e "  ${YELLOW}!${NC} $1"; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}              Voice AI IVR - Status Check                     ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# =============================================================================
# Serviços Systemd
# =============================================================================

echo -e "${BLUE}Serviços Systemd:${NC}"

if systemctl is-active --quiet voice-ai-realtime 2>/dev/null; then
    uptime=$(systemctl show voice-ai-realtime --property=ActiveEnterTimestamp | cut -d= -f2)
    check_ok "voice-ai-realtime: ATIVO (desde $uptime)"
else
    check_fail "voice-ai-realtime: INATIVO"
fi

if systemctl is-active --quiet freeswitch 2>/dev/null; then
    check_ok "freeswitch: ATIVO"
else
    check_fail "freeswitch: INATIVO"
fi

if systemctl is-active --quiet postgresql 2>/dev/null; then
    check_ok "postgresql: ATIVO"
else
    if systemctl is-active --quiet postgres 2>/dev/null; then
        check_ok "postgres: ATIVO"
    else
        check_warn "postgresql: não detectado (pode ter outro nome)"
    fi
fi

echo ""

# =============================================================================
# Portas
# =============================================================================

echo -e "${BLUE}Portas de Rede:${NC}"

if ss -tlnp 2>/dev/null | grep -q ":8021 "; then
    process=$(ss -tlnp | grep ":8021 " | sed -E 's/.*users:\(\("([^"]+)".*/\1/')
    check_ok "8021 (ESL Inbound): $process"
else
    check_fail "8021 (ESL Inbound): não escutando"
fi

if ss -tlnp 2>/dev/null | grep -q ":8022 "; then
    process=$(ss -tlnp | grep ":8022 " | sed -E 's/.*users:\(\("([^"]+)".*/\1/')
    check_ok "8022 (ESL Outbound): $process"
else
    check_fail "8022 (ESL Outbound): não escutando"
fi

if ss -tlnp 2>/dev/null | grep -q ":8085 "; then
    check_ok "8085 (API): escutando"
else
    check_warn "8085 (API): não escutando (pode ser normal)"
fi

echo ""

# =============================================================================
# Conexão ESL
# =============================================================================

echo -e "${BLUE}Conexão ESL:${NC}"

if nc -z 127.0.0.1 8021 2>/dev/null; then
    check_ok "Conexão ESL 127.0.0.1:8021 OK"
    
    # Tentar autenticar
    result=$(echo -e "auth ClueCon\nexit" | nc -w 2 127.0.0.1 8021 2>/dev/null)
    if echo "$result" | grep -q "Reply-Text: +OK"; then
        check_ok "Autenticação ESL OK"
    else
        check_warn "Autenticação ESL falhou (senha diferente?)"
    fi
else
    check_fail "Conexão ESL falhou"
fi

echo ""

# =============================================================================
# Banco de Dados
# =============================================================================

echo -e "${BLUE}Banco de Dados:${NC}"

# Tentar ler credenciais do .env
if [[ -f /opt/voice-ai/.env ]]; then
    source /opt/voice-ai/.env 2>/dev/null
fi

if [[ -n "$DB_HOST" ]] && [[ -n "$DB_USER" ]] && [[ -n "$DB_PASS" ]]; then
    if PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "${DB_NAME:-fusionpbx}" -c "SELECT 1" &>/dev/null; then
        check_ok "Conexão PostgreSQL OK"
        
        # Contar tabelas Voice AI
        count=$(PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "${DB_NAME:-fusionpbx}" -tAc \
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name LIKE 'v_voice_%';" 2>/dev/null)
        
        if [[ "$count" -gt 0 ]]; then
            check_ok "Tabelas Voice AI: $count"
        else
            check_warn "Nenhuma tabela Voice AI encontrada"
        fi
    else
        check_fail "Conexão PostgreSQL falhou"
    fi
else
    check_warn "Credenciais do banco não encontradas em /opt/voice-ai/.env"
fi

echo ""

# =============================================================================
# Arquivos e Permissões
# =============================================================================

echo -e "${BLUE}Arquivos e Diretórios:${NC}"

if [[ -d /opt/voice-ai ]]; then
    check_ok "/opt/voice-ai existe"
    
    if [[ -f /opt/voice-ai/.env ]]; then
        check_ok ".env configurado"
    else
        check_fail ".env não encontrado"
    fi
    
    if [[ -d /opt/voice-ai/venv ]]; then
        check_ok "Virtual environment existe"
    else
        check_fail "Virtual environment não encontrado"
    fi
else
    check_fail "/opt/voice-ai não existe"
fi

if [[ -d /var/www/fusionpbx/app/voice_secretary ]]; then
    check_ok "App FusionPBX instalado"
else
    check_warn "App FusionPBX não encontrado"
fi

echo ""

# =============================================================================
# Logs Recentes
# =============================================================================

echo -e "${BLUE}Últimos Logs (voice-ai-realtime):${NC}"
echo "────────────────────────────────────────────────────────────────"

journalctl -u voice-ai-realtime --no-pager -n 10 2>/dev/null || echo "  (logs não disponíveis)"

echo "────────────────────────────────────────────────────────────────"
echo ""

# =============================================================================
# Resumo
# =============================================================================

echo -e "${CYAN}Para mais detalhes:${NC}"
echo "  Logs completos:    journalctl -u voice-ai-realtime -f"
echo "  Status detalhado:  systemctl status voice-ai-realtime"
echo "  Configuração:      cat /opt/voice-ai/.env"
echo ""
