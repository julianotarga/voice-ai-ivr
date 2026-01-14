#!/bin/bash
# Script de Instalação do mod_audio_stream para FreeSWITCH
# Repositório: https://github.com/sptmru/freeswitch_mod_audio_stream
#
# Uso: ./install-mod-audio-stream.sh

set -e

echo "================================================"
echo "Instalação do mod_audio_stream para FreeSWITCH"
echo "================================================"
echo ""

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Função para verificar se comando existe
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Função para verificar se o módulo já está instalado
module_exists() {
    fs_cli -x "module_exists mod_audio_stream" 2>/dev/null | grep -q "true"
}

# Verificar se está rodando como root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Erro: Este script precisa ser executado como root${NC}"
    exit 1
fi

# Verificar se FreeSWITCH está instalado
if ! command_exists fs_cli; then
    echo -e "${RED}Erro: FreeSWITCH não encontrado. Instale o FreeSWITCH primeiro.${NC}"
    exit 1
fi

# Verificar se o módulo já está instalado
if module_exists; then
    echo -e "${YELLOW}Aviso: mod_audio_stream já está instalado e carregado.${NC}"
    read -p "Deseja reinstalar? (s/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Ss]$ ]]; then
        echo "Instalação cancelada."
        exit 0
    fi
fi

echo "Passo 1: Instalando dependências..."
apt-get update
apt-get install -y \
    libfreeswitch-dev \
    libssl-dev \
    zlib1g-dev \
    libspeexdsp-dev \
    cmake \
    build-essential \
    git

if [ $? -ne 0 ]; then
    echo -e "${RED}Erro ao instalar dependências${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Dependências instaladas${NC}"
echo ""

echo "Passo 2: Clonando repositório..."
cd /usr/src

# Remover versão antiga se existir
if [ -d "freeswitch_mod_audio_stream" ]; then
    echo "Removendo versão anterior..."
    rm -rf freeswitch_mod_audio_stream
fi

# Clonar repositório
git clone https://github.com/sptmru/freeswitch_mod_audio_stream.git
cd freeswitch_mod_audio_stream

# Inicializar submodules
echo "Inicializando submodules..."
git submodule init
git submodule update

echo -e "${GREEN}✓ Repositório clonado${NC}"
echo ""

echo "Passo 3: Compilando módulo..."
mkdir -p build
cd build

cmake ..
if [ $? -ne 0 ]; then
    echo -e "${RED}Erro na configuração do CMake${NC}"
    exit 1
fi

make
if [ $? -ne 0 ]; then
    echo -e "${RED}Erro na compilação${NC}"
    exit 1
fi

# Verificar se o arquivo foi gerado
if [ ! -f "mod_audio_stream.so" ]; then
    echo -e "${RED}Erro: Arquivo mod_audio_stream.so não foi gerado${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Compilação concluída${NC}"
echo ""

echo "Passo 4: Instalando módulo..."
cp mod_audio_stream.so /usr/lib/freeswitch/mod/
chmod 644 /usr/lib/freeswitch/mod/mod_audio_stream.so

# Obter usuário do FreeSWITCH
FS_USER=$(ps aux | grep '[f]reeswitch' | awk '{print $1}' | head -1)
if [ -z "$FS_USER" ]; then
    # Tentar determinar o usuário padrão
    FS_USER="freeswitch"
fi

chown "${FS_USER}:${FS_USER}" /usr/lib/freeswitch/mod/mod_audio_stream.so

echo -e "${GREEN}✓ Módulo instalado em /usr/lib/freeswitch/mod/mod_audio_stream.so${NC}"
echo ""

echo "Passo 5: Carregando módulo..."
fs_cli -x "load mod_audio_stream" 2>&1

if module_exists; then
    echo -e "${GREEN}✓ Módulo carregado com sucesso${NC}"
else
    echo -e "${YELLOW}Aviso: Módulo pode não ter carregado corretamente${NC}"
    echo "Verifique os logs: tail -f /var/log/freeswitch/freeswitch.log"
fi
echo ""

echo "Passo 6: Verificando API..."
API_CHECK=$(fs_cli -x "show api" 2>/dev/null | grep -c "uuid_audio_stream" || echo "0")
if [ "$API_CHECK" -gt 0 ]; then
    echo -e "${GREEN}✓ API uuid_audio_stream disponível${NC}"
else
    echo -e "${YELLOW}Aviso: API pode não estar disponível${NC}"
fi
echo ""

echo "================================================"
echo -e "${GREEN}Instalação concluída!${NC}"
echo "================================================"
echo ""
echo "Próximos passos:"
echo "1. Verificar se o módulo está carregado:"
echo "   fs_cli -x \"module_exists mod_audio_stream\""
echo ""
echo "2. (Opcional) Configurar autoload adicionando em"
echo "   /etc/freeswitch/autoload_configs/modules.conf.xml:"
echo "   <load module=\"mod_audio_stream\"/>"
echo ""
echo "3. Reiniciar o FreeSWITCH para garantir:"
echo "   systemctl restart freeswitch"
echo ""
