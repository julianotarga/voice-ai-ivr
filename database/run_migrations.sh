#!/bin/bash
# ==============================================
# Voice AI IVR - Migration Runner
# Executa todas as migrations de forma idempotente
# ==============================================

set -e

# Configuracoes (podem ser sobrescritas por variaveis de ambiente)
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-fusionpbx}"
DB_USER="${DB_USER:-fusionpbx}"
DB_PASS="${DB_PASS:-}"

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Diretorio das migrations
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="${SCRIPT_DIR}/migrations"

echo "=========================================="
echo " Voice AI IVR - Migration Runner"
echo "=========================================="
echo ""

# Verificar se a senha foi fornecida
if [ -z "$DB_PASS" ]; then
    echo -e "${YELLOW}Senha do banco nao definida.${NC}"
    echo "Use: DB_PASS='sua_senha' ./run_migrations.sh"
    echo "Ou defina a variavel de ambiente DB_PASS"
    echo ""
    read -sp "Digite a senha do PostgreSQL para $DB_USER: " DB_PASS
    echo ""
fi

# Exportar senha para psql
export PGPASSWORD="$DB_PASS"

echo ""
echo "Configuracoes:"
echo "  Host: $DB_HOST:$DB_PORT"
echo "  Database: $DB_NAME"
echo "  User: $DB_USER"
echo ""

# Testar conexao
echo -n "Testando conexao... "
if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1" > /dev/null 2>&1; then
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${RED}ERRO${NC}"
    echo "Nao foi possivel conectar ao banco de dados."
    exit 1
fi

echo ""
echo "Executando migrations..."
echo ""

# Lista de migrations em ordem
MIGRATIONS=(
    "001_create_providers.sql"
    "002_create_secretaries.sql"
    "003_create_documents.sql"
    "004_create_conversations.sql"
    "005_create_transfer_rules.sql"
    "006_create_messages.sql"
    "007_insert_default_providers.sql"
    "008_add_realtime_fields.sql"
)

# Executar cada migration
for migration in "${MIGRATIONS[@]}"; do
    filepath="${MIGRATIONS_DIR}/${migration}"
    
    if [ -f "$filepath" ]; then
        echo -n "  $migration ... "
        
        if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$filepath" > /tmp/migration_output.txt 2>&1; then
            # Verificar se houve erros (mesmo com exit code 0)
            if grep -q "ERROR:" /tmp/migration_output.txt; then
                echo -e "${YELLOW}AVISO${NC}"
                grep "ERROR:" /tmp/migration_output.txt | head -3
            else
                echo -e "${GREEN}OK${NC}"
            fi
        else
            echo -e "${RED}ERRO${NC}"
            cat /tmp/migration_output.txt
        fi
    else
        echo -e "  $migration ... ${YELLOW}NAO ENCONTRADO${NC}"
    fi
done

echo ""
echo "=========================================="
echo " Verificando tabelas criadas..."
echo "=========================================="
echo ""

psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "\dt v_voice_*"

echo ""
echo -e "${GREEN}Migrations concluidas!${NC}"
echo ""

# Limpar
unset PGPASSWORD
rm -f /tmp/migration_output.txt
