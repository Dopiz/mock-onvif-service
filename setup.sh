#!/bin/bash
# MockCameraService Quick Setup Script
# Supports: macOS (Apple Silicon/Intel) and Linux (Ubuntu/Debian AMD64)

set -e

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
echo " MockCameraService Setup Script"
echo "=================================="
echo ""
echo -n "Detecting system: "
detect_os
echo ""

# Color definitions
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check command function
check_command() {
    if command -v $1 &> /dev/null; then
        echo -e "${GREEN}✓${NC} $1 is installed"
        return 0
    else
        echo -e "${RED}✗${NC} $1 is not installed"
        return 1
    fi
}

# Check port (cross-platform)
check_port() {
    if [[ "$OS" == "macos" ]]; then
        # macOS uses lsof
        if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo -e "${YELLOW}⚠${NC} Port $1 is in use, please check if the port is used by other services"
            return 0
        else
            echo -e "${GREEN}✓${NC} Port $1 is available"
            return 0
        fi
    else
        # Linux uses ss or netstat
        if ss -tuln 2>/dev/null | grep -q ":$1 " || netstat -tuln 2>/dev/null | grep -q ":$1 "; then
            echo -e "${YELLOW}⚠${NC} Port $1 is in use, please check if the port is used by other services"
            return 0
        else
            echo -e "${GREEN}✓${NC} Port $1 is available"
            return 0
        fi
    fi
}

echo "Step 1: Check system dependencies"
echo "-----------------------------------"

# Check uv
if check_command uv; then
    UV_VERSION=$(uv --version | awk '{print $2}')
    echo "  Version: $UV_VERSION"
else
    echo -e "${YELLOW}uv is not installed. Install now? (y/n)${NC}"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        echo "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"
        
        # Add to shell configuration
        if [[ "$OS" == "macos" ]]; then
            # macOS typically uses zsh
            if [ -f "$HOME/.zshrc" ]; then
                if ! grep -q 'cargo/bin' "$HOME/.zshrc"; then
                    echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> "$HOME/.zshrc"
                fi
            fi
        else
            # Linux typically uses bash
            if [ -f "$HOME/.bashrc" ]; then
                if ! grep -q 'cargo/bin' "$HOME/.bashrc"; then
                    echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> "$HOME/.bashrc"
                fi
            fi
        fi
        
        echo -e "${GREEN}✓ uv installed successfully${NC}"
        if [[ "$OS" == "macos" ]]; then
            echo -e "${YELLOW}⚠ Please run: source ~/.zshrc or restart terminal${NC}"
        else
            echo -e "${YELLOW}⚠ Please run: source ~/.bashrc or restart terminal${NC}"
        fi
    else
        echo -e "${RED}uv is required. Please install manually: curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
        exit 1
    fi
fi

# Check Python - prefer .venv if it exists
if [ -f ".venv/bin/python3" ]; then
    PYTHON_CMD=".venv/bin/python3"
    PYTHON_VERSION=$($PYTHON_CMD --version | awk '{print $2}')
    echo -e "${GREEN}✓${NC} python3 (from .venv)"
    echo "  - Version: $PYTHON_VERSION"
elif check_command python3; then
    PYTHON_CMD="python3"
    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    echo "  - Version: $PYTHON_VERSION"
else
    if [[ "$OS" == "macos" ]]; then
        echo -e "${RED}Please install Python 3: brew install python3${NC}"
    else
        echo -e "${RED}Please install Python 3: sudo apt update && sudo apt install -y python3 python3-pip python3-venv${NC}"
    fi
    exit 1
fi

# Check FFmpeg
if check_command ffmpeg; then
    FFMPEG_VERSION=$(ffmpeg -version | head -n1 | awk '{print $3}')
    echo "  Version: $FFMPEG_VERSION"
else
    echo -e "${YELLOW}FFmpeg is not installed. Install now? (y/n)${NC}"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        echo "Installing FFmpeg..."
        if [[ "$OS" == "macos" ]]; then
            # macOS uses Homebrew
            if ! command -v brew &> /dev/null; then
                echo -e "${RED}Homebrew is required. Please install first: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"${NC}"
                exit 1
            fi
            brew install ffmpeg
        else
            # Linux uses apt
            sudo apt update
            sudo apt install -y ffmpeg
        fi
        echo -e "${GREEN}✓ FFmpeg installed successfully${NC}"
    else
        if [[ "$OS" == "macos" ]]; then
            echo -e "${RED}FFmpeg is required. Please install manually: brew install ffmpeg${NC}"
        else
            echo -e "${RED}FFmpeg is required. Please install manually: sudo apt install ffmpeg${NC}"
        fi
        exit 1
    fi
fi

# Check mediamtx
if check_command mediamtx; then
    MEDIAMTX_VERSION=$(mediamtx --version | awk '{print $1}')
    echo "  Version: $MEDIAMTX_VERSION"
else
    echo -e "${YELLOW}mediamtx is not installed. Install now? (y/n)${NC}"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        MEDIAMTX_VERSION="v1.15.5"
        
        if [[ "$OS" == "macos" ]]; then
            if [[ "$ARCH" == "arm64" ]]; then
                echo "Downloading mediamtx for macOS ARM64..."
                MEDIAMTX_FILE="mediamtx_${MEDIAMTX_VERSION}_darwin_arm64.tar.gz"
            else
                echo "Downloading mediamtx for macOS AMD64..."
                MEDIAMTX_FILE="mediamtx_${MEDIAMTX_VERSION}_darwin_amd64.tar.gz"
            fi
        else
            echo "Downloading mediamtx for Linux AMD64..."
            MEDIAMTX_FILE="mediamtx_${MEDIAMTX_VERSION}_linux_amd64.tar.gz"
        fi
        
        # Download and install
        if command -v wget &> /dev/null; then
            wget -q "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/${MEDIAMTX_FILE}"
        else
            curl -L -o "${MEDIAMTX_FILE}" "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/${MEDIAMTX_FILE}"
        fi
        
        tar -xzf "${MEDIAMTX_FILE}"
        sudo mv mediamtx /usr/local/bin/
        sudo chmod +x /usr/local/bin/mediamtx
        rm "${MEDIAMTX_FILE}"
        rm -f mediamtx.yml LICENSE README.md  # Remove extracted extra files
        echo -e "${GREEN}✓ mediamtx installed successfully${NC}"
    else
        echo -e "${RED}mediamtx is required. Please install manually${NC}"
        exit 1
    fi
fi

echo ""
echo "Step 2: Create directory structure"
echo "-----------------------------------"

mkdir -p data/videos
mkdir -p data/cameras
mkdir -p logs/onvif
mkdir -p logs/ffmpeg
mkdir -p static

echo -e "${GREEN}✓${NC} Directories created successfully"

echo ""
echo "Step 3: Install Python dependencies"
echo "-----------------------------------"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    uv venv --python=3.13
fi

echo "Installing dependencies..."
source .venv/bin/activate
uv pip install -r requirements.txt

echo -e "${GREEN}✓${NC} Python dependencies installed successfully"

echo ""
echo "Step 4: Check port availability"
echo "-----------------------------------"

check_port 9999  # Flask Web UI
check_port 8554  # mediamtx RTSP
check_port 12000 # ONVIF Camera 1

echo ""
echo "=================================="
echo " Setup Complete! Please review the results above."
echo "=================================="
