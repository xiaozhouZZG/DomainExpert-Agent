@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo ========================================
echo DomainExpert-Agent
echo 闲鱼智能客服与自动化运营平台
echo ========================================
echo.

set PYTHON_EXE=

if exist ".venv\Scripts\python.exe" (
    set PYTHON_EXE=.venv\Scripts\python.exe
) else (
    py -3.11 --version >nul 2>nul
    if not errorlevel 1 (
        echo [INFO] Creating local .venv with Python 3.11...
        py -3.11 -m venv .venv
        if errorlevel 1 (
            echo [ERROR] Failed to create .venv with Python 3.11.
            pause
            exit /b 1
        )
        set PYTHON_EXE=.venv\Scripts\python.exe
    ) else (
        py -3.12 --version >nul 2>nul
        if not errorlevel 1 (
            echo [INFO] Creating local .venv with Python 3.12...
            py -3.12 -m venv .venv
            if errorlevel 1 (
                echo [ERROR] Failed to create .venv with Python 3.12.
                pause
                exit /b 1
            )
            set PYTHON_EXE=.venv\Scripts\python.exe
        ) else (
            where python >nul 2>nul
            if errorlevel 1 (
                echo [ERROR] python not found in PATH.
                echo Install Python 3.11 from https://www.python.org/downloads/release/python-3119/
                pause
                exit /b 1
            )
            set PYTHON_EXE=python
        )
    )
)

for /f "tokens=2 delims= " %%v in ('%PYTHON_EXE% --version 2^>^&1') do set PY_VERSION=%%v
echo [INFO] Python: %PY_VERSION%
echo [INFO] Python executable: %PYTHON_EXE%

%PYTHON_EXE% -c "import sys; raise SystemExit(0 if sys.version_info[:2] in [(3,11),(3,12)] else 1)"
if errorlevel 1 (
    echo [ERROR] This project requires Python 3.11 or 3.12. Current Python is %PY_VERSION%.
    echo.
    echo Fix:
    echo   1. Install Python 3.11 for Windows.
    echo   2. Make sure the Python Launcher option is enabled during install.
    echo   3. Run this start.bat again. It will create .venv automatically.
    echo.
    echo Download: https://www.python.org/downloads/release/python-3119/
    pause
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] .env not found. Creating from .env.example.
        copy ".env.example" ".env" >nul
        echo [WARN] Please edit .env and set LLM_API_KEY before using LLM features.
    ) else (
        echo [WARN] .env and .env.example not found.
    )
)

if not exist "data" mkdir "data"
if not exist "logs" mkdir "logs"
if not exist "data\browser_state" mkdir "data\browser_state"

echo.
echo [INFO] Checking Python dependencies...
%PYTHON_EXE% -c "import fastapi, uvicorn, langchain_core, langgraph" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing requirements.txt...
    %PYTHON_EXE% -m pip install --upgrade pip
    %PYTHON_EXE% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

%PYTHON_EXE% -c "import playwright" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Playwright package is not installed. Run:
    echo %PYTHON_EXE% -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "%LOCALAPPDATA%\ms-playwright" (
    echo [INFO] Installing Playwright Chromium browser...
    %PYTHON_EXE% -m playwright install chromium
    if errorlevel 1 (
        echo [WARN] Playwright browser install failed. Chat/RAG can still start, but Goofish browser automation will not work.
    )
)

echo.
echo [INFO] Initializing database and seed data...
%PYTHON_EXE% -c "from database.connection import ensure_db_ready; print(ensure_db_ready())"
if errorlevel 1 (
    echo [ERROR] Database initialization failed.
    pause
    exit /b 1
)

set DB_PATH=data/platform.db
set LISTING_PLATFORM=goofish

if "%API_HOST%"=="" set API_HOST=0.0.0.0
set API_PORT=8802

for /f "tokens=*" %%p in ('powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort 8802 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)"') do set PORT_8802_PID=%%p
if defined PORT_8802_PID (
    echo [ERROR] Port 8802 already in use by PID %PORT_8802_PID%
    pause
    exit /b 1
)

echo.
echo [INFO] Starting server: http://127.0.0.1:%API_PORT%
echo [INFO] Main page:       http://127.0.0.1:%API_PORT%/
echo [INFO] Admin page:      http://127.0.0.1:%API_PORT%/admin
echo [INFO] KB page:         http://127.0.0.1:%API_PORT%/kb
echo [INFO] Chat page:       http://127.0.0.1:%API_PORT%/chat
echo [INFO] Listing platform mode: %LISTING_PLATFORM%
echo.
echo Press Ctrl+C to stop.
echo.

%PYTHON_EXE% app.py

echo.
echo [INFO] Server stopped.
pause
