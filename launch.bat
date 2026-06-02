@echo off
REM JARVIS Launcher for Windows
REM Double-click this file to start JARVIS

title JARVIS AI Assistant

echo.
echo  ============================================
echo    J.A.R.V.I.S  --  AI Desktop Assistant
echo  ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.11+
    echo         from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Check if node_modules exist for UI
if not exist "ui\node_modules" (
    echo [SETUP] Installing UI dependencies...
    cd ui
    npm install
    cd ..
)

REM Check if .env exists
if not exist ".env" (
    echo [SETUP] Creating .env from template...
    copy ".env.example" ".env" >nul 2>&1
    echo [INFO]  Add your API keys to .env to enable cloud AI providers.
    echo [INFO]  JARVIS will use local Ollama if available.
    echo.
)

REM Launch mode selection
set MODE=%1
if "%MODE%"=="" set MODE=ui

if "%MODE%"=="ui" (
    echo [LAUNCH] Starting JARVIS with UI...
    cd ui && npm start
) else if "%MODE%"=="voice" (
    echo [LAUNCH] Starting JARVIS in voice mode...
    python main.py --voice
) else if "%MODE%"=="text" (
    echo [LAUNCH] Starting JARVIS in text mode...
    python main.py
) else (
    echo Usage: launch.bat [ui^|voice^|text]
    pause
)
