#!/bin/bash
# Build script for macOS/Linux

set -e

echo "CrankBoy Manager Builder"
echo "========================"

if [ "$1" == "clean" ]; then
    echo "Cleaning build artifacts..."
    rm -rf build dist __pycache__
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    echo "Clean complete!"
    exit 0
fi

echo "Installing requirements..."
# Note: On Linux, you may need the following system packages for PyQt6:
# sudo apt-get update && sudo apt-get install -y libxcb-cursor0 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-util1 libxcb-xkb1 libxcb-glx0 libxcb-randr0 libxkbcommon-x11-0 libdbus-1-3
pip install -r requirements.txt
pip install pyinstaller

echo "Building executable..."
python3 build.py

echo ""
echo "Build complete! Check the dist/ folder."
