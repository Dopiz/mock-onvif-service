#!/bin/bash
# MockCameraService One-Click Start Script
# Supports: macOS (Apple Silicon/Intel) and Linux (Ubuntu/Debian AMD64)

# Switch to script directory
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

# Detect operating system
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        if [[ $(uname -m) == "arm64" ]]; then
            ARCH="arm64"
            echo "macOS (Apple Silicon)"
        else
            ARCH="amd64"
            echo "macOS (Intel)"
        fi
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
        ARCH="amd64"
        echo "Linux (AMD64)"
    else
        echo "Unsupported operating system: $OSTYPE"
        exit 1
    fi
}

echo "=================================="
echo " MockCameraService Start Script"
echo "=================================="
echo ""
echo -n "Detecting system: "
detect_os
echo ""

# Color definitions
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Check if port is in use (cross-platform)
check_port_in_use() {
    if [[ "$OS" == "macos" ]]; then
        # macOS uses lsof
        if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null 2>&1; then
            return 0  # Port in use
        else
            return 1  # Port available
        fi
    else
        # Linux uses ss or netstat
        if ss -tuln 2>/dev/null | grep -q ":$1 " || netstat -tuln 2>/dev/null | grep -q ":$1 "; then
            return 0  # Port in use
        else
            return 1  # Port available
        fi
    fi
}

# Function: Start mediamtx
start_mediamtx() {
    echo -e "${BLUE}ℹ${NC} Starting mediamtx..."
    
    if [[ "$OS" == "macos" ]]; then
        # macOS: Use project yml config
        echo "  Config: $PROJECT_DIR/mediamtx.yml"
        nohup mediamtx "$PROJECT_DIR/mediamtx.yml" > /tmp/mediamtx.log 2>&1 &
    else
        # Linux: Use default config (no yml specified)
        echo "  Config: default"
        nohup mediamtx > /tmp/mediamtx.log 2>&1 &
    fi
    
    MEDIAMTX_PID=$!
    echo "  PID: $MEDIAMTX_PID"
    sleep 2
    
    if check_port_in_use 8554; then
        echo -e "${GREEN}✓${NC} mediamtx started successfully"
        return 0
    else
        echo -e "${RED}✗${NC} mediamtx failed to start"
        echo "  Log: /tmp/mediamtx.log"
        echo "  Check log: tail -50 /tmp/mediamtx.log"
        return 1
    fi
}

# Check if mediamtx is running
if check_port_in_use 8554 || pgrep -x mediamtx >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} mediamtx is already running"
    
    # Check if correct configuration is loaded (only for macOS)
    if [[ "$OS" == "macos" ]]; then
        if [ -f "/tmp/mediamtx.log" ]; then
            if tail -50 /tmp/mediamtx.log 2>/dev/null | grep -q "configuration loaded from $PROJECT_DIR/mediamtx.yml"; then
                echo -e "${GREEN}✓${NC} Configuration file loaded: mediamtx.yml"
            elif tail -50 /tmp/mediamtx.log 2>/dev/null | grep -q "mediamtx.yml"; then
                echo -e "${GREEN}✓${NC} Configuration file: mediamtx.yml"
            else
                echo -e "${YELLOW}⚠${NC} mediamtx may not have loaded project config, restarting..."
                pkill mediamtx
                sleep 1
                start_mediamtx || {
                    echo -e "${RED}✗${NC} Failed to restart mediamtx"
                    exit 1
                }
            fi
        fi
    else
        # Linux: Just confirm it's running, no config check needed
        echo -e "${GREEN}✓${NC} Using default configuration"
    fi
else
    echo -e "${YELLOW}⚠${NC} mediamtx is not running"
    start_mediamtx || {
        echo -e "${RED}✗${NC} mediamtx failed to start, please check installation"
        exit 1
    }
fi


# Check virtual environment
if [ ! -f ".venv/bin/python3" ]; then
    echo -e "${RED}✗${NC} Virtual environment does not exist, please run: ./setup.sh"
    exit 1
fi

# Start main service
.venv/bin/python3 run.py 2>&1 | tee /tmp/mockcamera.log

# Cleanup on exit (optional)
cleanup() {
    echo ""
    echo -e "${YELLOW}⚠${NC} Stopping services..."
    
    # Optional: stop mediamtx
    # pkill mediamtx
    # echo -e "${GREEN}✓${NC} mediamtx stopped"
}

trap cleanup EXIT
