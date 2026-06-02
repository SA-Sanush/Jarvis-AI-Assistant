#!/usr/bin/env bash
# JARVIS Launcher for Linux (Fedora / Ubuntu)
# Usage: ./launch.sh [ui|voice|text|ptt]

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo -e "${CYAN}  ============================================${NC}"
echo -e "${CYAN}    J.A.R.V.I.S  --  AI Desktop Assistant   ${NC}"
echo -e "${CYAN}  ============================================${NC}"
echo ""

# ── Check Python ────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}[ERROR] Python 3 not found.${NC}"
    echo "  Fedora:  sudo dnf install python3"
    echo "  Ubuntu:  sudo apt install python3"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYMIN=$(python3 -c "import sys; print(1 if sys.version_info >= (3,11) else 0)")
if [ "$PYMIN" = "0" ]; then
    echo -e "${RED}[ERROR] Python 3.11+ required (found $PYVER)${NC}"
    exit 1
fi
echo -e "${GREEN}[OK]${NC} Python $PYVER"

# ── Check .env ──────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}[SETUP]${NC} Creating .env from template..."
    cp .env.example .env 2>/dev/null || touch .env
    echo -e "${YELLOW}[INFO]${NC}  Add your API keys to .env to enable cloud providers."
fi

# ── Check UI deps ───────────────────────────────────────────
MODE="${1:-ui}"

# ── Check Python deps ────────────────────────────────────────
# UI mode also needs these because Electron starts server.py.
if ! python3 - <<'PY' 2>/dev/null
import importlib.util
required = ("aiohttp", "dotenv", "psutil", "pyautogui", "sounddevice", "yaml", "cv2", "PIL")
raise SystemExit(0 if all(importlib.util.find_spec(m) for m in required) else 1)
PY
then
    echo -e "${YELLOW}[SETUP]${NC} Installing Python dependencies..."
    pip3 install -r requirements.txt --break-system-packages 2>/dev/null || \
    pip3 install -r requirements.txt
fi

if [ "$MODE" = "ui" ]; then
    if ! command -v node &>/dev/null; then
        echo -e "${RED}[ERROR] Node.js not found. Install from https://nodejs.org${NC}"
        echo "  Fedora:  sudo dnf install nodejs"
        exit 1
    fi
    if [ ! -d "ui/node_modules" ]; then
        echo -e "${YELLOW}[SETUP]${NC} Installing UI dependencies..."
        cd ui && npm install && cd ..
    fi
    echo -e "${GREEN}[LAUNCH]${NC} Starting JARVIS with Electron UI..."
    cd ui && env -u ELECTRON_RUN_AS_NODE ELECTRON_DISABLE_SANDBOX=1 ./node_modules/.bin/electron --no-sandbox .
    exit 0
fi

# ── Launch modes ─────────────────────────────────────────────
case "$MODE" in
    voice)
        echo -e "${GREEN}[LAUNCH]${NC} Voice mode (wake word: 'Jarvis')..."
        python3 main.py --voice
        ;;
    ptt)
        echo -e "${GREEN}[LAUNCH]${NC} Push-to-talk mode (hold SPACE)..."
        python3 main.py --ptt
        ;;
    text)
        echo -e "${GREEN}[LAUNCH]${NC} Text mode (CLI)..."
        python3 main.py
        ;;
    ml|malayalam)
        echo -e "${GREEN}[LAUNCH]${NC} Malayalam mode (മലയാളം)..."
        python3 main.py --lang ml
        ;;
    hi|hindi)
        echo -e "${GREEN}[LAUNCH]${NC} Hindi mode (हिंदी)..."
        python3 main.py --lang hi
        ;;
    ta|tamil)
        echo -e "${GREEN}[LAUNCH]${NC} Tamil mode (தமிழ்)..."
        python3 main.py --lang ta
        ;;
    voice-ml)
        echo -e "${GREEN}[LAUNCH]${NC} Malayalam voice mode..."
        python3 main.py --voice --lang ml
        ;;
    voice-hi)
        echo -e "${GREEN}[LAUNCH]${NC} Hindi voice mode..."
        python3 main.py --voice --lang hi
        ;;
    voice-ta)
        echo -e "${GREEN}[LAUNCH]${NC} Tamil voice mode..."
        python3 main.py --voice --lang ta
        ;;
    server)
        echo -e "${GREEN}[LAUNCH]${NC} HTTP server only (port 7771)..."
        python3 server.py
        ;;
    *)
        echo "Usage: ./launch.sh [ui|voice|ptt|text|server]"
        echo ""
        echo "  ui     — Electron desktop UI (default)"
        echo "  voice  — Voice mode with wake word"
        echo "  ptt    — Push-to-talk voice mode"
        echo "  text   — Text CLI mode"
        echo "  server — HTTP server only"
        exit 1
        ;;
esac
