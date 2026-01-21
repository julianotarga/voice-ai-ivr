#!/bin/bash
# =============================================================================
# Voice AI Baremetal - Deploy Script
# =============================================================================
# Faz deploy do código voice-ai para /opt/voice-ai/
# Uso: ./deploy.sh [/path/to/voice-ai-ivr]
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

# Diretório fonte
SOURCE_DIR="${1:-$(pwd)}"
VOICE_AI_SOURCE="$SOURCE_DIR/voice-ai-service"

# Destino
DEST_DIR="/opt/voice-ai"

# =============================================================================
# Verificações
# =============================================================================

if [[ $EUID -ne 0 ]]; then
   log_error "Este script precisa ser executado como root"
   exit 1
fi

if [[ ! -d "$VOICE_AI_SOURCE" ]]; then
    log_error "Diretório fonte não encontrado: $VOICE_AI_SOURCE"
    echo "Uso: $0 /path/to/voice-ai-ivr"
    exit 1
fi

if [[ ! -f "$VOICE_AI_SOURCE/requirements.txt" ]]; then
    log_error "requirements.txt não encontrado em $VOICE_AI_SOURCE"
    exit 1
fi

log_info "=== Voice AI Deploy ==="
log_info "Fonte: $VOICE_AI_SOURCE"
log_info "Destino: $DEST_DIR"
echo ""

# =============================================================================
# 1. Parar serviços
# =============================================================================

log_info "Parando serviços..."

systemctl stop voice-ai-realtime.service 2>/dev/null || true
systemctl stop voice-ai-service.service 2>/dev/null || true

sleep 2
log_success "Serviços parados"

# =============================================================================
# 2. Copiar código
# =============================================================================

log_info "Copiando código..."

rsync -av --delete \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='logs/*' \
    --exclude='data/*' \
    --exclude='.git' \
    "$VOICE_AI_SOURCE/" "$DEST_DIR/"

log_success "Código copiado"

# =============================================================================
# 3. Criar/atualizar virtual environment
# =============================================================================

log_info "Configurando virtual environment..."

cd "$DEST_DIR"

if [[ ! -d "venv" ]]; then
    log_info "Criando novo virtual environment..."
    python3.11 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

log_success "Dependências instaladas"

# =============================================================================
# 4. Criar diretórios necessários
# =============================================================================

mkdir -p "$DEST_DIR/data"
mkdir -p /var/log/voice-ai
mkdir -p /tmp/voice-ai-announcements

# =============================================================================
# 5. Ajustar permissões
# =============================================================================

log_info "Ajustando permissões..."

chown -R voiceai:voiceai "$DEST_DIR"
chown -R voiceai:voiceai /var/log/voice-ai
chown -R voiceai:voiceai /tmp/voice-ai-announcements

log_success "Permissões ajustadas"

# =============================================================================
# 6. Reiniciar serviços
# =============================================================================

log_info "Iniciando serviços..."

systemctl start voice-ai-realtime.service

# Aguardar e verificar
sleep 3

if systemctl is-active --quiet voice-ai-realtime; then
    log_success "voice-ai-realtime está rodando"
else
    log_error "Falha ao iniciar voice-ai-realtime"
    echo "Verifique os logs: journalctl -u voice-ai-realtime -e"
    exit 1
fi

# Voice-ai-service é opcional (pode não estar usando)
# systemctl start voice-ai-service.service

# =============================================================================
# Resumo
# =============================================================================

echo ""
log_info "=== Deploy Concluído ==="
echo ""
echo "Status:"
systemctl status voice-ai-realtime --no-pager -l | head -15
echo ""
echo "Logs:"
echo "  journalctl -u voice-ai-realtime -f"
echo ""
log_success "Deploy finalizado!"
