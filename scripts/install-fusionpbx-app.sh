#!/bin/bash
# =============================================================================
# Voice Secretary - Instalador do Módulo FusionPBX
# =============================================================================
#
# Este script instala o aplicativo Voice Secretary no FusionPBX.
#
# Uso: ./install-fusionpbx-app.sh [FUSIONPBX_PATH]
#
# Exemplo:
#   ./install-fusionpbx-app.sh
#   ./install-fusionpbx-app.sh /var/www/fusionpbx
#
# =============================================================================

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configurações
FUSIONPBX_PATH="${1:-/var/www/fusionpbx}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
APP_SOURCE="${PROJECT_ROOT}/fusionpbx-app/voice_secretary"
APP_DEST="${FUSIONPBX_PATH}/app/voice_secretary"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Voice Secretary - Instalador FusionPBX${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Verificar se está rodando como root
if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}⚠️  Este script precisa de privilégios de root${NC}"
    echo "   Execute: sudo $0 $@"
    exit 1
fi

# Verificar se o FusionPBX existe
if [ ! -d "$FUSIONPBX_PATH" ]; then
    echo -e "${RED}❌ FusionPBX não encontrado em: $FUSIONPBX_PATH${NC}"
    echo "   Use: $0 /caminho/para/fusionpbx"
    exit 1
fi

if [ ! -f "${FUSIONPBX_PATH}/app/system/app_config.php" ]; then
    echo -e "${RED}❌ Instalação do FusionPBX inválida em: $FUSIONPBX_PATH${NC}"
    exit 1
fi

# Verificar se o source existe
if [ ! -d "$APP_SOURCE" ]; then
    echo -e "${RED}❌ Pasta do aplicativo não encontrada: $APP_SOURCE${NC}"
    exit 1
fi

echo -e "${GREEN}✓ FusionPBX encontrado em: $FUSIONPBX_PATH${NC}"
echo -e "${GREEN}✓ App source: $APP_SOURCE${NC}"
echo ""

# ============================================================================
# PASSO 1: Backup (se existir instalação anterior)
# ============================================================================
if [ -d "$APP_DEST" ]; then
    BACKUP_DIR="${APP_DEST}.backup.$(date +%Y%m%d_%H%M%S)"
    echo -e "${YELLOW}📦 Backup da instalação anterior...${NC}"
    mv "$APP_DEST" "$BACKUP_DIR"
    echo -e "   Backup salvo em: $BACKUP_DIR"
fi

# ============================================================================
# PASSO 2: Copiar arquivos do aplicativo
# ============================================================================
echo -e "${BLUE}📂 Copiando arquivos do aplicativo...${NC}"
cp -r "$APP_SOURCE" "$APP_DEST"
echo -e "${GREEN}✓ Arquivos copiados para: $APP_DEST${NC}"

# ============================================================================
# PASSO 3: Ajustar permissões
# ============================================================================
echo -e "${BLUE}🔒 Ajustando permissões...${NC}"

# Detectar usuário do web server
if id "www-data" &>/dev/null; then
    WEB_USER="www-data"
    WEB_GROUP="www-data"
elif id "nginx" &>/dev/null; then
    WEB_USER="nginx"
    WEB_GROUP="nginx"
elif id "apache" &>/dev/null; then
    WEB_USER="apache"
    WEB_GROUP="apache"
else
    WEB_USER=$(stat -c '%U' "${FUSIONPBX_PATH}/index.php" 2>/dev/null || echo "www-data")
    WEB_GROUP=$(stat -c '%G' "${FUSIONPBX_PATH}/index.php" 2>/dev/null || echo "www-data")
fi

chown -R ${WEB_USER}:${WEB_GROUP} "$APP_DEST"
find "$APP_DEST" -type f -exec chmod 644 {} \;
find "$APP_DEST" -type d -exec chmod 755 {} \;

echo -e "${GREEN}✓ Permissões ajustadas (${WEB_USER}:${WEB_GROUP})${NC}"

# ============================================================================
# PASSO 4: Executar upgrade do schema (criar tabelas)
# ============================================================================
echo -e "${BLUE}🗄️  Criando tabelas no banco de dados...${NC}"

if [ -f "${FUSIONPBX_PATH}/core/upgrade/upgrade_schema.php" ]; then
    cd "$FUSIONPBX_PATH"
    php core/upgrade/upgrade_schema.php > /dev/null 2>&1 || true
    echo -e "${GREEN}✓ Schema atualizado${NC}"
else
    echo -e "${YELLOW}⚠️  upgrade_schema.php não encontrado, pulando...${NC}"
fi

# ============================================================================
# PASSO 5: Atualizar menus
# ============================================================================
echo -e "${BLUE}📋 Atualizando menus...${NC}"

if [ -f "${FUSIONPBX_PATH}/core/upgrade/upgrade_menu.php" ]; then
    cd "$FUSIONPBX_PATH"
    php core/upgrade/upgrade_menu.php > /dev/null 2>&1 || true
    echo -e "${GREEN}✓ Menus atualizados${NC}"
else
    echo -e "${YELLOW}⚠️  upgrade_menu.php não encontrado, pulando...${NC}"
fi

# ============================================================================
# PASSO 6: Atualizar permissões de grupo
# ============================================================================
echo -e "${BLUE}👥 Atualizando permissões de grupo...${NC}"

if [ -f "${FUSIONPBX_PATH}/core/upgrade/upgrade_permissions.php" ]; then
    cd "$FUSIONPBX_PATH"
    php core/upgrade/upgrade_permissions.php > /dev/null 2>&1 || true
    echo -e "${GREEN}✓ Permissões de grupo atualizadas${NC}"
else
    echo -e "${YELLOW}⚠️  upgrade_permissions.php não encontrado, pulando...${NC}"
fi

# ============================================================================
# PASSO 7: Limpar cache do PHP (opcional)
# ============================================================================
echo -e "${BLUE}🧹 Limpando cache...${NC}"

# OPcache
if php -m | grep -q "OPcache"; then
    php -r 'opcache_reset();' 2>/dev/null || true
fi

# PHP-FPM
if systemctl is-active --quiet php-fpm 2>/dev/null; then
    systemctl reload php-fpm 2>/dev/null || true
elif systemctl is-active --quiet php7.4-fpm 2>/dev/null; then
    systemctl reload php7.4-fpm 2>/dev/null || true
elif systemctl is-active --quiet php8.0-fpm 2>/dev/null; then
    systemctl reload php8.0-fpm 2>/dev/null || true
elif systemctl is-active --quiet php8.1-fpm 2>/dev/null; then
    systemctl reload php8.1-fpm 2>/dev/null || true
elif systemctl is-active --quiet php8.2-fpm 2>/dev/null; then
    systemctl reload php8.2-fpm 2>/dev/null || true
fi

echo -e "${GREEN}✓ Cache limpo${NC}"

# ============================================================================
# FINALIZADO
# ============================================================================
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  ✅ Instalação concluída com sucesso!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "O módulo Voice Secretary está instalado em:"
echo -e "  ${BLUE}${APP_DEST}${NC}"
echo ""
echo -e "Acesse no FusionPBX:"
echo -e "  ${BLUE}https://seu-fusionpbx/app/voice_secretary/${NC}"
echo ""
echo -e "${YELLOW}📝 Próximos passos:${NC}"
echo "  1. Faça login no FusionPBX como superadmin"
echo "  2. Vá em: Advanced > Upgrade"
echo "  3. Clique em 'Schema' e depois 'Menu' para garantir"
echo "  4. Acesse: Apps > Voice Secretary"
echo "  5. Configure os provedores de IA (API keys)"
echo "  6. Crie sua primeira secretária virtual"
echo ""
echo -e "${YELLOW}⚠️  Lembre-se de:${NC}"
echo "  - Verificar se as migrations do banco foram executadas"
echo "  - Configurar o serviço Docker (voice-ai-service)"
echo "  - Copiar os scripts Lua para o FreeSWITCH"
echo ""
