@echo off
REM Build script for Windows

echo CrankBoy Transfer GUI Builder
echo =============================

if "%1"=="clean" (
    echo Cleaning build artifacts...
    rmdir /s /q build dist 2>nul
    rmdir /s /q __pycache__ 2>nul
    echo Clean complete!
    exit /b 0
)

echo Installing requirements...
pip install -r requirements.txt
pip install pyinstaller

echo Building executable...
python build.py

echo.
echo Build complete! Check the dist/ folder.
pause
