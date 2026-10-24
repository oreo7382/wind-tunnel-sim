@echo off
REM Creates a "Virtual Wind Tunnel" shortcut on the desktop and in the Start menu.
REM Run install_windows.bat once first.
cd /d "%~dp0\.."
if not exist .venv\Scripts\python.exe (
    echo The simulator is not installed yet. Run scripts\install_windows.bat first.
    if not defined WINDTUNNEL_SKIP_LAUNCH pause
    exit /b 1
)
.venv\Scripts\python -m windtunnel.shortcut %*
if errorlevel 1 (
    echo Could not create the shortcut. See the message above.
    if not defined WINDTUNNEL_SKIP_LAUNCH pause
    exit /b 1
)
echo.
echo Done. Double-click "Virtual Wind Tunnel" on your desktop to start the simulator.
if not defined WINDTUNNEL_SKIP_LAUNCH pause
