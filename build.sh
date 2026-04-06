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
pip install -r requirements.txt
pip install pyinstaller

echo "Building executable..."
python3 build.py

echo ""
echo "Build complete! Check the dist/ folder."
