#!/bin/bash
# ============================================
# Voice AI IVR - Installation Script
# Secretária Virtual com IA para FreeSWITCH/FusionPBX
# ============================================

set -e

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Voice AI IVR - Instalação${NC}"
echo -e "${GREEN}========================================${NC}"

# Verificar se está rodando como root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Por favor, execute como root (sudo)${NC}"
    exit 1
fi

# Diretório base
INSTALL_DIR="/opt/voice-ai-service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ============================================
# 1. Instalar dependências do sistema
# ============================================
echo -e "\n${YELLOW}[1/7] Instalando dependências do sistema...${NC}"

apt-get update
apt-get install -y \
    python3.10 \
    python3.10-venv \
    python3-pip \
    ffmpeg \
    curl \
    jq

# ============================================
# 2. Criar diretório e copiar arquivos
# ============================================
echo -e "\n${YELLOW}[2/7] Criando estrutura de diretórios...${NC}"

mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data/whisper"
mkdir -p "$INSTALL_DIR/data/piper"
mkdir -p "$INSTALL_DIR/data/embeddings"
mkdir -p /tmp/voice-ai

# Copiar código do serviço Python
cp -r "$PROJECT_DIR/voice-ai-service/"* "$INSTALL_DIR/"

# ============================================
# 3. Criar ambiente virtual e instalar dependências
# ============================================
echo -e "\n${YELLOW}[3/7] Criando ambiente virtual Python...${NC}"

cd "$INSTALL_DIR"
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ============================================
# 4. Baixar modelos
# ============================================
echo -e "\n${YELLOW}[4/7] Baixando modelos de IA...${NC}"

# Whisper base model (será baixado automaticamente na primeira execução)
echo "Modelo Whisper será baixado na primeira execução..."

# Piper TTS (baixar voz pt-BR)
if [ ! -f "$INSTALL_DIR/data/piper/pt_BR-faber-medium.onnx" ]; then
    echo "Baixando voz Piper pt-BR..."
    cd "$INSTALL_DIR/data/piper"
    curl -LO "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx"
    curl -LO "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json"
fi

# ============================================
# 5. Instalar scripts Lua no FreeSWITCH
# ============================================
echo -e "\n${YELLOW}[5/7] Instalando scripts Lua no FreeSWITCH...${NC}"

FREESWITCH_SCRIPTS="/usr/share/freeswitch/scripts"
mkdir -p "$FREESWITCH_SCRIPTS/lib"

cp "$PROJECT_DIR/freeswitch/scripts/"*.lua "$FREESWITCH_SCRIPTS/"
cp "$PROJECT_DIR/freeswitch/scripts/lib/"*.lua "$FREESWITCH_SCRIPTS/lib/"

chown -R freeswitch:freeswitch "$FREESWITCH_SCRIPTS"

# ============================================
# 6. Instalar dialplan
# ============================================
echo -e "\n${YELLOW}[6/7] Instalando dialplan...${NC}"

DIALPLAN_DIR="/etc/freeswitch/dialplan/default"
cp "$PROJECT_DIR/freeswitch/dialplan/secretary.xml" "$DIALPLAN_DIR/99_voice_secretary.xml"
chown freeswitch:freeswitch "$DIALPLAN_DIR/99_voice_secretary.xml"

# Recarregar dialplan
fs_cli -x "reloadxml"

# ============================================
# 7. Configurar e iniciar serviço
# ============================================
echo -e "\n${YELLOW}[7/7] Configurando serviço systemd...${NC}"

cp "$PROJECT_DIR/deploy/systemd/voice-ai-service.service" /etc/systemd/system/

# Ajustar permissões
chown -R freeswitch:freeswitch "$INSTALL_DIR"
chown -R freeswitch:freeswitch /tmp/voice-ai

# Habilitar e iniciar serviço
systemctl daemon-reload
systemctl enable voice-ai-service
systemctl start voice-ai-service

# ============================================
# Verificação final
# ============================================
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}Instalação concluída!${NC}"
echo -e "${GREEN}========================================${NC}"

# Verificar status
sleep 2
if systemctl is-active --quiet voice-ai-service; then
    echo -e "${GREEN}✓ Serviço voice-ai-service está rodando${NC}"
else
    echo -e "${RED}✗ Serviço voice-ai-service não iniciou. Verifique: journalctl -u voice-ai-service${NC}"
fi

# Testar health check
if curl -s http://127.0.0.1:8100/health | jq -e '.status == "healthy"' > /dev/null 2>&1; then
    echo -e "${GREEN}✓ API respondendo corretamente${NC}"
else
    echo -e "${YELLOW}⚠ API não respondeu. Aguarde alguns segundos e teste: curl http://127.0.0.1:8100/health${NC}"
fi

echo -e "\n${YELLOW}Próximos passos:${NC}"
echo "1. Instale o app FusionPBX: cp -r fusionpbx-app/voice_secretary /var/www/fusionpbx/app/"
echo "2. Execute as migrations no banco de dados"
echo "3. Configure a secretária virtual no FusionPBX"
echo ""
echo "Documentação: $PROJECT_DIR/docs/"
