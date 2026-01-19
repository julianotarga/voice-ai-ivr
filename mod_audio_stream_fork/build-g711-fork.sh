#!/bin/bash
#
# Build script para mod_audio_stream com suporte a G.711
# Fork com patches de segurança e performance
#
# Uso:
#   sudo ./build-g711-fork.sh
#
# O script irá:
#   1. Instalar dependências
#   2. Baixar libwsc (WebSocket client library)
#   3. Compilar o módulo
#   4. Instalar no FreeSWITCH
#   5. Opcionalmente recarregar o módulo
#

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Verificar se está rodando como root
if [ "$EUID" -ne 0 ]; then
    log_error "Este script precisa ser executado como root (sudo)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log_info "Diretório de trabalho: $SCRIPT_DIR"

# ============================================================
# 1. Instalar dependências
# ============================================================
log_info "Instalando dependências..."

apt-get update
apt-get -y install \
    build-essential \
    cmake \
    git \
    libfreeswitch-dev \
    libssl-dev \
    zlib1g-dev \
    libspeexdsp-dev \
    pkg-config

# ============================================================
# 2. Baixar libwsc (WebSocket client library) se não existir
# ============================================================
if [ ! -d "libs/libwsc" ] || [ ! -f "libs/libwsc/CMakeLists.txt" ]; then
    log_info "Baixando libwsc (WebSocket client library)..."
    mkdir -p libs
    rm -rf libs/libwsc
    git clone --depth 1 https://github.com/amigniter/libwsc.git libs/libwsc
else
    log_info "libwsc já existe, pulando download"
fi

# ============================================================
# 3. Configurar PKG_CONFIG_PATH para FreeSWITCH
# ============================================================
FS_PKGCONFIG_PATHS=(
    "/usr/local/freeswitch/lib/pkgconfig"
    "/usr/lib/freeswitch/lib/pkgconfig"
    "/usr/share/freeswitch/lib/pkgconfig"
)

for path in "${FS_PKGCONFIG_PATHS[@]}"; do
    if [ -d "$path" ]; then
        export PKG_CONFIG_PATH="$path:$PKG_CONFIG_PATH"
        log_info "Encontrado FreeSWITCH pkgconfig em: $path"
        break
    fi
done

# Verificar se FreeSWITCH foi encontrado
if ! pkg-config --exists freeswitch 2>/dev/null; then
    log_warn "FreeSWITCH não encontrado via pkg-config"
    log_warn "Tentando localizar manualmente..."
    
    # FusionPBX típico
    if [ -f "/usr/include/freeswitch/switch.h" ]; then
        log_info "Encontrado headers em /usr/include/freeswitch"
    else
        log_error "Headers do FreeSWITCH não encontrados!"
        log_error "Instale libfreeswitch-dev ou verifique a instalação"
        exit 1
    fi
fi

# ============================================================
# 4. Compilar o módulo
# ============================================================
log_info "Compilando mod_audio_stream com suporte a G.711..."

# Limpar build anterior
rm -rf build
mkdir build
cd build

# Configurar cmake
cmake -DCMAKE_BUILD_TYPE=Release ..

# Compilar
make -j$(nproc)

log_info "Compilação concluída!"

# ============================================================
# 5. Instalar o módulo
# ============================================================
log_info "Instalando módulo..."

make install

# Verificar onde foi instalado
FS_MOD_DIR=$(pkg-config --variable=modulesdir freeswitch 2>/dev/null || echo "/usr/lib/freeswitch/mod")

if [ -f "$FS_MOD_DIR/mod_audio_stream.so" ]; then
    log_info "Módulo instalado em: $FS_MOD_DIR/mod_audio_stream.so"
    ls -la "$FS_MOD_DIR/mod_audio_stream.so"
else
    log_warn "Módulo não encontrado em $FS_MOD_DIR"
    log_warn "Procurando..."
    find /usr -name "mod_audio_stream.so" 2>/dev/null || true
fi

# ============================================================
# 6. Recarregar módulo no FreeSWITCH
# ============================================================
echo ""
read -p "Deseja recarregar o módulo no FreeSWITCH agora? (s/N): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Ss]$ ]]; then
    log_info "Recarregando módulo..."
    
    # Verificar se fs_cli está disponível
    if command -v fs_cli &> /dev/null; then
        # Descarregar módulo antigo (ignorar erro se não estiver carregado)
        fs_cli -x "unload mod_audio_stream" 2>/dev/null || true
        sleep 1
        
        # Carregar novo módulo
        if fs_cli -x "load mod_audio_stream"; then
            log_info "Módulo carregado com sucesso!"
            
            # Verificar se está funcionando
            fs_cli -x "module_exists mod_audio_stream"
        else
            log_error "Falha ao carregar módulo"
            exit 1
        fi
    else
        log_warn "fs_cli não encontrado. Recarregue manualmente:"
        echo "  fs_cli -x 'unload mod_audio_stream'"
        echo "  fs_cli -x 'load mod_audio_stream'"
    fi
fi

# ============================================================
# 7. Mostrar instruções de uso
# ============================================================
echo ""
log_info "============================================"
log_info "Build concluído com sucesso!"
log_info "============================================"
echo ""
echo "Uso do novo parâmetro G.711:"
echo ""
echo "  uuid_audio_stream <uuid> start <url> mono 8k pcmu [metadata]"
echo "  uuid_audio_stream <uuid> start <url> mono 8k pcma [metadata]"
echo "  uuid_audio_stream <uuid> start <url> mono 8k l16 [metadata]"
echo ""
echo "Exemplo com G.711 μ-law:"
echo "  uuid_audio_stream abc123 start wss://api.openai.com/v1/realtime mono 8k pcmu"
echo ""
echo "Formatos suportados:"
echo "  - l16/linear/pcm : Linear PCM 16-bit (padrão)"
echo "  - pcmu/ulaw/mulaw: G.711 μ-law"
echo "  - pcma/alaw      : G.711 A-law"
echo ""
log_info "NOTA: G.711 (pcmu/pcma) só funciona com sample rate 8kHz"
