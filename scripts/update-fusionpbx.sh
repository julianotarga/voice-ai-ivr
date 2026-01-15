#!/bin/bash
# ============================================================================
# Script para atualizar arquivos do Voice AI no FusionPBX
# ============================================================================
# Uso: ./update-fusionpbx.sh [usuario@servidor]
# Exemplo: ./update-fusionpbx.sh root@192.168.1.100
# ============================================================================

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configurações padrão
FUSIONPBX_APP_PATH="/var/www/fusionpbx/app/voice_secretary"
FREESWITCH_SCRIPTS_PATH="/usr/share/freeswitch/scripts"
FREESWITCH_DIALPLAN_PATH="/etc/freeswitch/dialplan"

# Diretório fonte (onde o script está)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}   Voice AI - Atualização FusionPBX${NC}"
echo -e "${BLUE}============================================${NC}"

# Verificar se foi passado o servidor como argumento
if [ -z "$1" ]; then
    echo -e "${YELLOW}Uso: $0 [usuario@servidor]${NC}"
    echo -e "${YELLOW}Exemplo: $0 root@192.168.1.100${NC}"
    echo ""
    echo -e "${BLUE}Modo LOCAL (mesma máquina):${NC}"
    TARGET="local"
else
    TARGET="$1"
fi

echo ""
echo -e "${GREEN}📂 Arquivos a serem atualizados:${NC}"
echo ""

# Lista de arquivos
echo "1. FusionPBX App (voice_secretary/):"
echo "   - secretary_edit.php"
echo "   - secretary.php"
echo "   - providers_edit.php"
echo "   - resources/classes/voice_secretary.php"
echo "   - resources/classes/voice_ai_provider.php"
echo "   - app_languages.php"
echo ""
echo "2. Scripts Lua:"
echo "   - voice_secretary.lua"
echo ""
echo "3. Migrations SQL:"
echo "   - 009_add_handoff_fields.sql"
echo ""

if [ "$TARGET" == "local" ]; then
    echo -e "${YELLOW}Copiando arquivos localmente...${NC}"
    
    # Copiar FusionPBX App
    echo -e "${GREEN}[1/3] Copiando FusionPBX App...${NC}"
    sudo cp -r "$PROJECT_DIR/fusionpbx-app/voice_secretary"/* "$FUSIONPBX_APP_PATH/"
    sudo chown -R www-data:www-data "$FUSIONPBX_APP_PATH"
    sudo chmod -R 755 "$FUSIONPBX_APP_PATH"
    
    # Copiar Scripts Lua
    echo -e "${GREEN}[2/3] Copiando Scripts Lua...${NC}"
    sudo cp "$PROJECT_DIR/freeswitch/scripts/voice_secretary.lua" "$FREESWITCH_SCRIPTS_PATH/"
    sudo chmod 644 "$FREESWITCH_SCRIPTS_PATH/voice_secretary.lua"
    
    # Executar Migration
    echo -e "${GREEN}[3/3] Executando Migration...${NC}"
    if [ -f "$PROJECT_DIR/database/migrations/009_add_handoff_fields.sql" ]; then
        # Ler credenciais do FusionPBX
        DB_HOST=$(grep -oP "db_host = '\K[^']+" /etc/fusionpbx/config.conf 2>/dev/null || echo "localhost")
        DB_NAME=$(grep -oP "db_name = '\K[^']+" /etc/fusionpbx/config.conf 2>/dev/null || echo "fusionpbx")
        DB_USER=$(grep -oP "db_username = '\K[^']+" /etc/fusionpbx/config.conf 2>/dev/null || echo "fusionpbx")
        DB_PASS=$(grep -oP "db_password = '\K[^']+" /etc/fusionpbx/config.conf 2>/dev/null || echo "")
        
        if [ -n "$DB_PASS" ]; then
            PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -f "$PROJECT_DIR/database/migrations/009_add_handoff_fields.sql"
        else
            echo -e "${YELLOW}⚠️ Senha do banco não encontrada. Execute manualmente:${NC}"
            echo "psql -h $DB_HOST -U $DB_USER -d $DB_NAME -f $PROJECT_DIR/database/migrations/009_add_handoff_fields.sql"
        fi
    fi
    
    echo -e "${GREEN}✅ Atualização local concluída!${NC}"
    
else
    echo -e "${YELLOW}Copiando arquivos para $TARGET...${NC}"
    
    # Criar diretório temporário no servidor
    ssh "$TARGET" "mkdir -p /tmp/voice-ai-update"
    
    # Copiar FusionPBX App
    echo -e "${GREEN}[1/4] Enviando FusionPBX App...${NC}"
    scp -r "$PROJECT_DIR/fusionpbx-app/voice_secretary"/* "$TARGET:/tmp/voice-ai-update/"
    
    # Copiar Scripts Lua
    echo -e "${GREEN}[2/4] Enviando Scripts Lua...${NC}"
    scp "$PROJECT_DIR/freeswitch/scripts/voice_secretary.lua" "$TARGET:/tmp/voice-ai-update/"
    
    # Copiar Migrations
    echo -e "${GREEN}[3/4] Enviando Migrations...${NC}"
    scp "$PROJECT_DIR/database/migrations/009_add_handoff_fields.sql" "$TARGET:/tmp/voice-ai-update/"
    
    # Executar no servidor
    echo -e "${GREEN}[4/4] Instalando no servidor...${NC}"
    ssh "$TARGET" << 'ENDSSH'
        set -e
        
        # Configurações
        FUSIONPBX_APP_PATH="/var/www/fusionpbx/app/voice_secretary"
        FREESWITCH_SCRIPTS_PATH="/usr/share/freeswitch/scripts"
        
        # Backup
        echo "📦 Criando backup..."
        BACKUP_DIR="/tmp/voice-ai-backup-$(date +%Y%m%d_%H%M%S)"
        mkdir -p "$BACKUP_DIR"
        [ -d "$FUSIONPBX_APP_PATH" ] && cp -r "$FUSIONPBX_APP_PATH" "$BACKUP_DIR/"
        [ -f "$FREESWITCH_SCRIPTS_PATH/voice_secretary.lua" ] && cp "$FREESWITCH_SCRIPTS_PATH/voice_secretary.lua" "$BACKUP_DIR/"
        
        # Instalar FusionPBX App
        echo "📂 Instalando FusionPBX App..."
        mkdir -p "$FUSIONPBX_APP_PATH"
        cp -r /tmp/voice-ai-update/*.php "$FUSIONPBX_APP_PATH/" 2>/dev/null || true
        cp -r /tmp/voice-ai-update/resources "$FUSIONPBX_APP_PATH/" 2>/dev/null || true
        cp -r /tmp/voice-ai-update/languages "$FUSIONPBX_APP_PATH/" 2>/dev/null || true
        chown -R www-data:www-data "$FUSIONPBX_APP_PATH"
        chmod -R 755 "$FUSIONPBX_APP_PATH"
        
        # Instalar Script Lua
        echo "📜 Instalando Script Lua..."
        cp /tmp/voice-ai-update/voice_secretary.lua "$FREESWITCH_SCRIPTS_PATH/"
        chmod 644 "$FREESWITCH_SCRIPTS_PATH/voice_secretary.lua"
        
        # Executar Migration
        echo "🗄️ Executando Migration..."
        if [ -f "/etc/fusionpbx/config.conf" ]; then
            DB_HOST=$(grep -oP "db_host = '\K[^']+" /etc/fusionpbx/config.conf 2>/dev/null || echo "localhost")
            DB_NAME=$(grep -oP "db_name = '\K[^']+" /etc/fusionpbx/config.conf 2>/dev/null || echo "fusionpbx")
            DB_USER=$(grep -oP "db_username = '\K[^']+" /etc/fusionpbx/config.conf 2>/dev/null || echo "fusionpbx")
            DB_PASS=$(grep -oP "db_password = '\K[^']+" /etc/fusionpbx/config.conf 2>/dev/null || echo "")
            
            if [ -n "$DB_PASS" ]; then
                PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -f /tmp/voice-ai-update/009_add_handoff_fields.sql
            else
                echo "⚠️ Senha do banco não encontrada. Execute manualmente."
            fi
        else
            echo "⚠️ Config FusionPBX não encontrada. Execute a migration manualmente."
        fi
        
        # Limpar
        rm -rf /tmp/voice-ai-update
        
        echo "✅ Instalação concluída!"
        echo "📦 Backup salvo em: $BACKUP_DIR"
ENDSSH
    
    echo -e "${GREEN}✅ Atualização remota concluída!${NC}"
fi

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}📋 Próximos passos:${NC}"
echo ""
echo "1. Verifique o FusionPBX: acesse Voice Secretary no menu"
echo "2. Edite uma secretária e verifique os novos campos de Handoff"
echo "3. Configure o OmniPlay Company ID"
echo "4. Teste uma chamada para o ramal da secretária"
echo ""
echo -e "${BLUE}============================================${NC}"
