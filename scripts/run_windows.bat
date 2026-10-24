@echo off
REM Starts the Virtual Wind Tunnel (run install_windows.bat once first).
cd /d "%~dp0\.."
if not exist .venv\Scripts\pythonw.exe (
    echo The simulator is not installed yet. Run scripts\install_windows.bat first.
    pause
    exit /b 1
)
start "" .venv\Scripts\pythonw.exe -m windtunnel
