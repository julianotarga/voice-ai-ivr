#!/bin/bash
# =============================================================================
# Voice AI IVR - Log Viewer
# =============================================================================
# Visualiza logs do Voice AI em tempo real com filtros
# =============================================================================

# Cores
CYAN='\033[0;36m'
NC='\033[0m'

show_help() {
    echo ""
    echo -e "${CYAN}Voice AI IVR - Log Viewer${NC}"
    echo ""
    echo "Uso: $0 [opções]"
    echo ""
    echo "Opções:"
    echo "  -f, --follow      Seguir logs em tempo real (padrão)"
    echo "  -n, --lines NUM   Número de linhas a mostrar (padrão: 50)"
    echo "  -e, --errors      Mostrar apenas erros"
    echo "  -s, --search STR  Filtrar por string"
    echo "  --since TIME      Logs desde (ex: '1 hour ago', '2024-01-01')"
    echo "  --all             Mostrar todos os serviços Voice AI"
    echo "  -h, --help        Mostrar esta ajuda"
    echo ""
    echo "Exemplos:"
    echo "  $0                    # Seguir logs em tempo real"
    echo "  $0 -n 100             # Últimas 100 linhas"
    echo "  $0 -e                 # Apenas erros"
    echo "  $0 -s 'OpenAI'        # Filtrar por OpenAI"
    echo "  $0 --since '1 hour ago'  # Última hora"
    echo ""
    exit 0
}

# Defaults
FOLLOW=true
LINES=50
ERRORS_ONLY=false
SEARCH=""
SINCE=""
ALL_SERVICES=false

# Parse argumentos
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--follow)
            FOLLOW=true
            ;;
        -n|--lines)
            LINES="$2"
            FOLLOW=false
            shift
            ;;
        -e|--errors)
            ERRORS_ONLY=true
            ;;
        -s|--search)
            SEARCH="$2"
            shift
            ;;
        --since)
            SINCE="$2"
            shift
            ;;
        --all)
            ALL_SERVICES=true
            ;;
        -h|--help)
            show_help
            ;;
        *)
            echo "Opção desconhecida: $1"
            show_help
            ;;
    esac
    shift
done

# Construir comando
CMD="journalctl -u voice-ai-realtime"

if [[ "$ALL_SERVICES" == "true" ]]; then
    CMD="journalctl -u voice-ai-realtime -u voice-ai-service"
fi

if [[ "$FOLLOW" == "true" ]]; then
    CMD="$CMD -f"
else
    CMD="$CMD -n $LINES"
fi

if [[ -n "$SINCE" ]]; then
    CMD="$CMD --since '$SINCE'"
fi

CMD="$CMD --no-pager"

# Executar com filtros
echo -e "${CYAN}Voice AI Logs${NC}"
echo "────────────────────────────────────────────────────────────────"
echo "Comando: $CMD"
echo "────────────────────────────────────────────────────────────────"
echo ""

if [[ "$ERRORS_ONLY" == "true" ]]; then
    eval "$CMD" 2>/dev/null | grep -iE "error|exception|fail|critical"
elif [[ -n "$SEARCH" ]]; then
    eval "$CMD" 2>/dev/null | grep -i "$SEARCH"
else
    eval "$CMD" 2>/dev/null
fi
