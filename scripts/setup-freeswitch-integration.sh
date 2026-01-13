#!/bin/bash
# =============================================================================
# Voice AI IVR - FreeSWITCH Integration Setup
# =============================================================================
# Run this on the HOST where FreeSWITCH is installed to configure integration
# with the Voice AI Docker containers.
#
# Referências:
# - .context/docs/development-workflow.md
# - .context/agents/devops-specialist.md
#
# Usage: sudo ./scripts/setup-freeswitch-integration.sh
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
FREESWITCH_SCRIPTS_DIR="${FREESWITCH_SCRIPTS_DIR:-/usr/share/freeswitch/scripts}"
FREESWITCH_DIALPLAN_DIR="${FREESWITCH_DIALPLAN_DIR:-/etc/freeswitch/dialplan/default}"
VOICE_AI_URL="${VOICE_AI_URL:-http://localhost:8100}"
VOICE_AI_REALTIME_URL="${VOICE_AI_REALTIME_URL:-ws://localhost:8085}"
PROJECT_DIR="$(dirname "$0")/.."

echo -e "${BLUE}=== Voice AI IVR - FreeSWITCH Integration Setup ===${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root: sudo $0${NC}"
    exit 1
fi

# Check FreeSWITCH installation
if [ ! -d "$FREESWITCH_SCRIPTS_DIR" ]; then
    echo -e "${RED}FreeSWITCH scripts directory not found: $FREESWITCH_SCRIPTS_DIR${NC}"
    echo "Set FREESWITCH_SCRIPTS_DIR environment variable if different"
    exit 1
fi

echo -e "${YELLOW}FreeSWITCH scripts: $FREESWITCH_SCRIPTS_DIR${NC}"
echo -e "${YELLOW}FreeSWITCH dialplan: $FREESWITCH_DIALPLAN_DIR${NC}"
echo -e "${YELLOW}Voice AI URL (v1): $VOICE_AI_URL${NC}"
echo -e "${YELLOW}Voice AI Realtime URL (v2): $VOICE_AI_REALTIME_URL${NC}"
echo ""

# 1. Copy Lua scripts
echo -e "${BLUE}1. Installing Lua scripts...${NC}"

# Create directories
mkdir -p "$FREESWITCH_SCRIPTS_DIR/voice_ai"
mkdir -p "$FREESWITCH_SCRIPTS_DIR/voice_ai/lib"

# Copy scripts - Voice AI module
if [ -d "$PROJECT_DIR/freeswitch/scripts/voice_ai" ]; then
    cp -v "$PROJECT_DIR/freeswitch/scripts/voice_ai/"*.lua "$FREESWITCH_SCRIPTS_DIR/voice_ai/"
    cp -v "$PROJECT_DIR/freeswitch/scripts/voice_ai/lib/"*.lua "$FREESWITCH_SCRIPTS_DIR/voice_ai/lib/"
fi

# Copy legacy scripts (if exist)
if [ -f "$PROJECT_DIR/freeswitch/scripts/secretary_ai.lua" ]; then
    cp -v "$PROJECT_DIR/freeswitch/scripts/secretary_ai.lua" "$FREESWITCH_SCRIPTS_DIR/"
fi
if [ -d "$PROJECT_DIR/freeswitch/scripts/lib" ]; then
    mkdir -p "$FREESWITCH_SCRIPTS_DIR/lib"
    cp -v "$PROJECT_DIR/freeswitch/scripts/lib/"*.lua "$FREESWITCH_SCRIPTS_DIR/lib/"
fi

# Update URLs in scripts
find "$FREESWITCH_SCRIPTS_DIR/voice_ai" -name "*.lua" -exec \
    sed -i "s|http://127.0.0.1:8100|$VOICE_AI_URL|g" {} \;
find "$FREESWITCH_SCRIPTS_DIR/voice_ai" -name "*.lua" -exec \
    sed -i "s|ws://127.0.0.1:8085|$VOICE_AI_REALTIME_URL|g" {} \;
find "$FREESWITCH_SCRIPTS_DIR/voice_ai" -name "*.lua" -exec \
    sed -i "s|ws://127.0.0.1:8080|$VOICE_AI_REALTIME_URL|g" {} \;

# Set permissions
chown -R freeswitch:freeswitch "$FREESWITCH_SCRIPTS_DIR/voice_ai"
chmod 755 "$FREESWITCH_SCRIPTS_DIR/voice_ai"
chmod 644 "$FREESWITCH_SCRIPTS_DIR/voice_ai/"*.lua 2>/dev/null || true
chmod 644 "$FREESWITCH_SCRIPTS_DIR/voice_ai/lib/"*.lua 2>/dev/null || true

echo -e "${GREEN}✓ Lua scripts installed${NC}"

# 2. Copy dialplan
echo -e "${BLUE}2. Installing dialplan...${NC}"

# Copy all dialplan files
for xml in "$PROJECT_DIR/freeswitch/dialplan/"*.xml; do
    if [ -f "$xml" ]; then
        cp -v "$xml" "$FREESWITCH_DIALPLAN_DIR/"
    fi
done

chown freeswitch:freeswitch "$FREESWITCH_DIALPLAN_DIR/"*voice_ai*.xml 2>/dev/null || true
chmod 644 "$FREESWITCH_DIALPLAN_DIR/"*voice_ai*.xml 2>/dev/null || true

echo -e "${GREEN}✓ Dialplan installed${NC}"

# 3. Check mod_audio_stream (for realtime)
echo -e "${BLUE}3. Checking mod_audio_stream...${NC}"

if [ -f "/usr/lib/freeswitch/mod/mod_audio_stream.so" ] || \
   [ -f "/usr/lib64/freeswitch/mod/mod_audio_stream.so" ]; then
    echo -e "${GREEN}✓ mod_audio_stream found${NC}"
    
    # Check if loaded
    if fs_cli -x "module_exists mod_audio_stream" 2>/dev/null | grep -q "true"; then
        echo -e "${GREEN}✓ mod_audio_stream is loaded${NC}"
    else
        echo -e "${YELLOW}⚠ mod_audio_stream not loaded. Run:${NC}"
        echo "  fs_cli -x 'load mod_audio_stream'"
    fi
else
    echo -e "${YELLOW}⚠ mod_audio_stream not found${NC}"
    echo "  Realtime mode will not work without this module."
    echo "  See: https://github.com/amigniter/mod_audio_stream"
fi

# 4. Test Voice AI connection
echo -e "${BLUE}4. Testing Voice AI connections...${NC}"

# Test v1 (HTTP API)
if curl -s "$VOICE_AI_URL/health" 2>/dev/null | grep -q "healthy"; then
    echo -e "${GREEN}✓ Voice AI Service (v1) is healthy${NC}"
else
    echo -e "${YELLOW}⚠ Voice AI Service (v1) not responding at $VOICE_AI_URL${NC}"
    echo "  Make sure Docker containers are running: docker compose up -d"
fi

# Test v2 (WebSocket)
REALTIME_HOST=$(echo "$VOICE_AI_REALTIME_URL" | sed 's|ws://||' | cut -d: -f1)
REALTIME_PORT=$(echo "$VOICE_AI_REALTIME_URL" | sed 's|ws://||' | cut -d: -f2 | cut -d/ -f1)
if nc -z "$REALTIME_HOST" "$REALTIME_PORT" 2>/dev/null; then
    echo -e "${GREEN}✓ Voice AI Realtime (v2) is accepting connections${NC}"
else
    echo -e "${YELLOW}⚠ Voice AI Realtime (v2) not responding at $VOICE_AI_REALTIME_URL${NC}"
    echo "  Make sure voice-ai-realtime container is running"
fi

# 5. Reload FreeSWITCH
echo -e "${BLUE}5. Reloading FreeSWITCH dialplan...${NC}"

if command -v fs_cli &> /dev/null; then
    fs_cli -x "reloadxml"
    echo -e "${GREEN}✓ FreeSWITCH reloaded${NC}"
else
    echo -e "${YELLOW}⚠ fs_cli not found. Please reload FreeSWITCH manually:${NC}"
    echo "  fs_cli -x 'reloadxml'"
fi

# 6. Summary
echo ""
echo -e "${GREEN}=== Installation Complete ===${NC}"
echo ""
echo "Modes available:"
echo "  - Turn-based (v1): Uses voice-ai-service on :8100"
echo "  - Realtime (v2):   Uses voice-ai-realtime on :8085 (ou override via VOICE_AI_REALTIME_URL)"
echo ""
echo "Files installed:"
echo "  - $FREESWITCH_SCRIPTS_DIR/voice_ai/*.lua"
echo "  - $FREESWITCH_SCRIPTS_DIR/voice_ai/lib/*.lua"
echo "  - $FREESWITCH_DIALPLAN_DIR/*voice_ai*.xml"
echo ""
echo "Extensions:"
echo "  - 8000-8099: Secretárias virtuais (auto-routing)"
echo "  - 8999: Realtime test extension"
echo ""
echo "To test:"
echo "  curl $VOICE_AI_URL/health"
echo "  Call extension 8000"
echo ""
