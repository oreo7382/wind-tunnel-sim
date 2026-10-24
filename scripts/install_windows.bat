@echo off
REM ---------------------------------------------------------------
REM  Virtual Wind Tunnel - one-click installer for Windows
REM  Creates a private Python environment in .venv and installs
REM  the simulator and its libraries (numpy, matplotlib, PySide6).
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0\.."

REM Find a Python 3.10+ that really runs. The Microsoft Store placeholder
REM "python.exe" exists on many PCs but only prints a message, so every
REM candidate is tested instead of just checking that the command exists.
set "PY="
call :try py -3
if not defined PY call :try python
if not defined PY call :try python3
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" call :try "%%D\python.exe"
    )
)
if not defined PY (
    echo.
    echo  Python 3.10 or newer was not found.
    echo  Install it from https://www.python.org/downloads/
    echo  and tick "Add python.exe to PATH" in the installer, then run this again.
    goto :fail_quiet
)
echo Using Python:
%PY% --version

echo Creating virtual environment in .venv ...
%PY% -m venv .venv
if errorlevel 1 goto :fail

echo Installing the simulator and its libraries (this can take a few minutes) ...
.venv\Scripts\python -m pip install --upgrade pip
if errorlevel 1 goto :fail
.venv\Scripts\python -m pip install -e .
if errorlevel 1 goto :fail

echo.
echo  Installation finished.
echo  Start the simulator by double-clicking scripts\run_windows.bat
echo  or make a desktop icon with scripts\create_shortcut_windows.bat
echo.
if not defined WINDTUNNEL_SKIP_LAUNCH pause
exit /b 0

:try
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set PY=%*
exit /b 0

:fail
echo.
echo  Installation failed. See the messages above and the Troubleshooting
echo  section of README.md.
:fail_quiet
if not defined WINDTUNNEL_SKIP_LAUNCH pause
exit /b 1
