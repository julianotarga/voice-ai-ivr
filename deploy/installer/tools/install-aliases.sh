#!/bin/bash
# =============================================================================
# Voice AI IVR - Install Aliases
# =============================================================================
# Adiciona comandos do Voice AI ao PATH do sistema
# =============================================================================

set -e

# Cores
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

TOOLS_DIR="/opt/voice-ai/tools"
BIN_DIR="/usr/local/bin"

echo -e "${CYAN}Instalando aliases do Voice AI...${NC}"

# Criar diretório de ferramentas
mkdir -p "$TOOLS_DIR"

# Copiar ferramentas
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for tool in voice-ai-status.sh voice-ai-logs.sh voice-ai-uninstall.sh; do
    if [[ -f "$SCRIPT_DIR/$tool" ]]; then
        cp "$SCRIPT_DIR/$tool" "$TOOLS_DIR/"
        chmod +x "$TOOLS_DIR/$tool"
    fi
done

# Criar links simbólicos em /usr/local/bin
ln -sf "$TOOLS_DIR/voice-ai-status.sh" "$BIN_DIR/voice-ai-status"
ln -sf "$TOOLS_DIR/voice-ai-logs.sh" "$BIN_DIR/voice-ai-logs"
ln -sf "$TOOLS_DIR/voice-ai-uninstall.sh" "$BIN_DIR/voice-ai-uninstall"

# Criar alias adicional
ln -sf "$TOOLS_DIR/voice-ai-status.sh" "$BIN_DIR/vai-status"
ln -sf "$TOOLS_DIR/voice-ai-logs.sh" "$BIN_DIR/vai-logs"

echo -e "${GREEN}✓${NC} Aliases instalados!"
echo ""
echo "Comandos disponíveis:"
echo "  voice-ai-status   - Verificar status do sistema"
echo "  voice-ai-logs     - Ver logs em tempo real"
echo "  voice-ai-uninstall - Desinstalar Voice AI"
echo ""
echo "Atalhos:"
echo "  vai-status        - Alias para voice-ai-status"
echo "  vai-logs          - Alias para voice-ai-logs"
echo ""
