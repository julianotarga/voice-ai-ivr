#!/bin/bash
# =============================================================================
# Voice AI Baremetal - Status Script
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Voice AI Baremetal Status ===${NC}"
echo ""

# =============================================================================
# Status dos Serviços
# =============================================================================

echo -e "${BLUE}Services:${NC}"

for service in voice-ai-realtime voice-ai-service; do
    if systemctl is-active --quiet "$service"; then
        pid=$(systemctl show "$service" --property=MainPID --value)
        mem=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.0fMB", $1/1024}')
        uptime=$(systemctl show "$service" --property=ActiveEnterTimestamp --value | cut -d' ' -f2-3)
        echo -e "  $service: ${GREEN}● running${NC}  PID: $pid  MEM: $mem"
    elif systemctl is-enabled --quiet "$service" 2>/dev/null; then
        echo -e "  $service: ${YELLOW}○ stopped${NC}"
    else
        echo -e "  $service: ${RED}○ not enabled${NC}"
    fi
done

echo ""

# =============================================================================
# Status das Portas
# =============================================================================

echo -e "${BLUE}Ports:${NC}"

PORTS=(
    "8085:realtime (A-leg WebSocket)"
    "8086:realtime (B-leg WebSocket)"
    "8022:realtime (ESL outbound)"
    "8100:service (HTTP API)"
)

for port_info in "${PORTS[@]}"; do
    port="${port_info%%:*}"
    name="${port_info##*:}"
    
    if nc -z 127.0.0.1 "$port" 2>/dev/null; then
        echo -e "  Port $port ($name): ${GREEN}LISTENING${NC}"
    else
        echo -e "  Port $port ($name): ${RED}CLOSED${NC}"
    fi
done

echo ""

# =============================================================================
# FreeSWITCH ESL
# =============================================================================

echo -e "${BLUE}FreeSWITCH:${NC}"

if systemctl is-active --quiet freeswitch; then
    echo -e "  Service: ${GREEN}● running${NC}"
else
    echo -e "  Service: ${RED}○ stopped${NC}"
fi

if nc -z 127.0.0.1 8021 2>/dev/null; then
    echo -e "  ESL (8021): ${GREEN}AVAILABLE${NC}"
else
    echo -e "  ESL (8021): ${RED}NOT AVAILABLE${NC}"
fi

echo ""

# =============================================================================
# Dependências Remotas
# =============================================================================

echo -e "${BLUE}Remote Dependencies (from .env):${NC}"

if [[ -f /opt/voice-ai/.env ]]; then
    REDIS_HOST=$(grep -E "^REDIS_HOST=" /opt/voice-ai/.env | cut -d= -f2)
    REDIS_PORT=$(grep -E "^REDIS_PORT=" /opt/voice-ai/.env | cut -d= -f2)
    
    if [[ -n "$REDIS_HOST" ]]; then
        if nc -z "$REDIS_HOST" "${REDIS_PORT:-6379}" 2>/dev/null; then
            echo -e "  Redis ($REDIS_HOST:${REDIS_PORT:-6379}): ${GREEN}AVAILABLE${NC}"
        else
            echo -e "  Redis ($REDIS_HOST:${REDIS_PORT:-6379}): ${RED}NOT AVAILABLE${NC}"
        fi
    fi
else
    echo -e "  ${YELLOW}/opt/voice-ai/.env not found${NC}"
fi

echo ""

# =============================================================================
# Últimos Logs
# =============================================================================

echo -e "${BLUE}Recent Logs (last 5 lines):${NC}"
journalctl -u voice-ai-realtime -n 5 --no-pager 2>/dev/null || echo "  No logs available"

echo ""
echo -e "${BLUE}Commands:${NC}"
echo "  Start:   sudo systemctl start voice-ai-realtime"
echo "  Stop:    sudo systemctl stop voice-ai-realtime"
echo "  Logs:    journalctl -u voice-ai-realtime -f"
echo ""
