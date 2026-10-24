#!/usr/bin/env bash
# ---------------------------------------------------------------
#  Virtual Wind Tunnel - one-click installer for macOS and Linux
#  Creates a private Python environment in .venv and installs the
#  simulator and its libraries (numpy, matplotlib, PySide6).
# ---------------------------------------------------------------
set -e
cd "$(dirname "$0")/.."

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
            PY="$candidate"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.10 or newer was not found."
    echo "macOS: install it from https://www.python.org/downloads/ (or: brew install python)"
    echo "Ubuntu/Debian: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

echo "Creating virtual environment in .venv using $($PY --version) ..."
if ! "$PY" -m venv .venv; then
    echo "Could not create a virtual environment."
    echo "On Ubuntu/Debian install it with: sudo apt install python3-venv"
    exit 1
fi

echo "Installing the simulator and its libraries (this can take a few minutes) ..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

echo
echo "Installation finished. Start the simulator with:"
echo "    bash scripts/run_mac_linux.sh"
echo "or make a desktop icon with:"
echo "    bash scripts/create_shortcut_mac_linux.sh"
if [ "$(uname)" = "Linux" ]; then
    echo
    echo "If the window does not open on Linux, install the Qt system libraries:"
    echo "    sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libegl1"
fi
