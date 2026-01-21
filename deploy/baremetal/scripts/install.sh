#!/bin/bash
# =============================================================================
# Voice AI Baremetal - Installation Script
# =============================================================================
# Instala Voice AI no servidor FreeSWITCH para rodar co-residente
# Testado em: Ubuntu 22.04, Ubuntu 24.04, Debian 12
# =============================================================================

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# Verificações Iniciais
# =============================================================================

if [[ $EUID -ne 0 ]]; then
   log_error "Este script precisa ser executado como root"
   exit 1
fi

log_info "=== Voice AI Baremetal Installation ==="
echo ""

# =============================================================================
# 1. Verificar FreeSWITCH
# =============================================================================

log_info "Verificando FreeSWITCH..."

if systemctl is-active --quiet freeswitch; then
    log_success "FreeSWITCH está rodando"
else
    log_error "FreeSWITCH não está rodando!"
    echo "  Este script deve ser executado no servidor onde FreeSWITCH está instalado."
    exit 1
fi

if nc -z 127.0.0.1 8021 2>/dev/null; then
    log_success "FreeSWITCH ESL disponível na porta 8021"
else
    log_error "FreeSWITCH ESL não acessível na porta 8021"
    exit 1
fi

# =============================================================================
# 2. Verificar/Instalar Python 3.11
# =============================================================================

log_info "Verificando Python 3.11..."

if command -v python3.11 &> /dev/null; then
    PYTHON_VERSION=$(python3.11 --version)
    log_success "Python: $PYTHON_VERSION"
else
    log_info "Instalando Python 3.11..."
    
    # Tentar instalar via apt
    apt-get update
    apt-get install -y software-properties-common
    
    # Adicionar PPA deadsnakes se necessário
    if ! apt-cache show python3.11 &>/dev/null; then
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update
    fi
    
    apt-get install -y python3.11 python3.11-venv python3.11-dev
    
    if command -v python3.11 &> /dev/null; then
        log_success "Python 3.11 instalado"
    else
        log_error "Falha ao instalar Python 3.11"
        exit 1
    fi
fi

# =============================================================================
# 3. Instalar dependências do sistema
# =============================================================================

log_info "Instalando dependências do sistema..."

apt-get install -y \
    curl \
    ffmpeg \
    libspeexdsp1 \
    libspeexdsp-dev \
    libsndfile1 \
    netcat-openbsd

log_success "Dependências instaladas"

# =============================================================================
# 4. Criar usuário de serviço
# =============================================================================

log_info "Configurando usuário de serviço..."

if id "voiceai" &>/dev/null; then
    log_warn "Usuário 'voiceai' já existe"
else
    useradd -r -s /bin/false -d /opt/voice-ai voiceai
    log_success "Usuário 'voiceai' criado"
fi

# Adicionar ao grupo audio (para acesso a dispositivos de áudio se necessário)
usermod -aG audio voiceai 2>/dev/null || true

# =============================================================================
# 5. Criar estrutura de diretórios
# =============================================================================

log_info "Criando estrutura de diretórios..."

mkdir -p /opt/voice-ai/{data,logs}
mkdir -p /var/log/voice-ai
mkdir -p /tmp/voice-ai-announcements

chown -R voiceai:voiceai /opt/voice-ai
chown -R voiceai:voiceai /var/log/voice-ai
chown -R voiceai:voiceai /tmp/voice-ai-announcements
chmod 755 /opt/voice-ai
chmod 755 /var/log/voice-ai

# Link simbólico para logs
ln -sf /var/log/voice-ai /opt/voice-ai/logs 2>/dev/null || true

log_success "Diretórios criados"

# =============================================================================
# 6. Instalar units systemd
# =============================================================================

log_info "Instalando units systemd..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="$SCRIPT_DIR/../systemd"

if [[ -d "$SYSTEMD_DIR" ]]; then
    cp "$SYSTEMD_DIR"/voice-ai-*.service /etc/systemd/system/
    systemctl daemon-reload
    log_success "Units systemd instaladas"
else
    log_warn "Diretório systemd não encontrado em $SYSTEMD_DIR"
    log_warn "Copie manualmente os arquivos .service para /etc/systemd/system/"
fi

# =============================================================================
# 7. Configurar logrotate
# =============================================================================

log_info "Configurando logrotate..."

LOGROTATE_CONF="$SCRIPT_DIR/../config/logrotate-voice-ai.conf"

if [[ -f "$LOGROTATE_CONF" ]]; then
    cp "$LOGROTATE_CONF" /etc/logrotate.d/voice-ai
    chmod 644 /etc/logrotate.d/voice-ai
    log_success "Logrotate configurado"
else
    log_warn "Arquivo logrotate não encontrado"
fi

# =============================================================================
# 8. Copiar template de ambiente
# =============================================================================

log_info "Configurando arquivo de ambiente..."

ENV_TEMPLATE="$SCRIPT_DIR/../config/voice-ai.env.template"

if [[ -f "$ENV_TEMPLATE" ]] && [[ ! -f /opt/voice-ai/.env ]]; then
    cp "$ENV_TEMPLATE" /opt/voice-ai/.env
    chown voiceai:voiceai /opt/voice-ai/.env
    chmod 600 /opt/voice-ai/.env
    log_success "Template de ambiente copiado para /opt/voice-ai/.env"
    log_warn "EDITE /opt/voice-ai/.env com suas configurações!"
else
    log_warn "Arquivo .env já existe ou template não encontrado"
fi

# =============================================================================
# Resumo Final
# =============================================================================

echo ""
log_info "=== Instalação Concluída ==="
echo ""
echo "Próximos passos:"
echo ""
echo "  1. Copiar código do voice-ai-ivr para /opt/voice-ai/"
echo "     rsync -av /path/to/voice-ai-ivr/voice-ai-service/ /opt/voice-ai/"
echo ""
echo "  2. Criar virtual environment e instalar dependências:"
echo "     cd /opt/voice-ai"
echo "     python3.11 -m venv venv"
echo "     source venv/bin/activate"
echo "     pip install -r requirements.txt"
echo ""
echo "  3. Editar configurações:"
echo "     nano /opt/voice-ai/.env"
echo ""
echo "  4. Ajustar permissões:"
echo "     chown -R voiceai:voiceai /opt/voice-ai"
echo ""
echo "  5. Habilitar e iniciar serviços:"
echo "     systemctl enable voice-ai-service voice-ai-realtime"
echo "     systemctl start voice-ai-realtime"
echo ""
echo "  6. Verificar status:"
echo "     systemctl status voice-ai-*"
echo "     journalctl -u voice-ai-realtime -f"
echo ""
log_success "Instalação finalizada!"
