#!/bin/bash
# TeslaLogger startup script
# Run this from the teslalogger folder: bash start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔴  Stopping any running TeslaLogger servers..."
pkill -f "uvicorn api_server" 2>/dev/null || true
pkill -f "python.*main.py" 2>/dev/null || true
sleep 1

echo "📦  Checking dependencies..."
pip3 install -q -r requirements.txt

echo ""
echo "✅  Starting TeslaLogger..."
echo "🌐  Dashboard → http://localhost:8000"
echo ""

python3 main.py
