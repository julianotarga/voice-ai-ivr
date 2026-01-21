#!/bin/bash
# =============================================================================
# Voice AI - Rollback to Docker
# =============================================================================
# Reverte voice-ai para Docker em caso de problemas com baremetal
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Diretório do docker-compose do voice-ai-ivr
DOCKER_DIR="${1:-/root/voice-ai-ivr}"

log_warn "=== ROLLBACK: Voice AI Baremetal -> Docker ==="
echo ""

# =============================================================================
# 1. Parar serviços baremetal
# =============================================================================

log_info "Parando serviços baremetal..."

systemctl stop voice-ai-realtime.service 2>/dev/null || true
systemctl stop voice-ai-service.service 2>/dev/null || true

log_success "Serviços baremetal parados"

# =============================================================================
# 2. Desabilitar serviços baremetal
# =============================================================================

log_info "Desabilitando serviços baremetal..."

systemctl disable voice-ai-realtime.service 2>/dev/null || true
systemctl disable voice-ai-service.service 2>/dev/null || true

log_success "Serviços baremetal desabilitados"

# =============================================================================
# 3. Iniciar containers Docker
# =============================================================================

log_info "Iniciando containers Docker..."

if [[ -d "$DOCKER_DIR" ]]; then
    cd "$DOCKER_DIR"
    docker compose up -d voice-ai-realtime
    
    sleep 5
    
    if docker compose ps voice-ai-realtime | grep -q "Up"; then
        log_success "voice-ai-realtime Docker está rodando"
    else
        log_error "Falha ao iniciar Docker"
        docker compose logs voice-ai-realtime --tail 20
        exit 1
    fi
else
    log_error "Diretório Docker não encontrado: $DOCKER_DIR"
    echo "Uso: $0 /path/to/voice-ai-ivr"
    exit 1
fi

# =============================================================================
# Resumo
# =============================================================================

echo ""
log_success "=== Rollback Concluído ==="
echo ""
echo "Voice AI está rodando via Docker novamente."
echo ""
echo "Verifique com:"
echo "  docker compose ps"
echo "  docker compose logs -f voice-ai-realtime"
echo ""
