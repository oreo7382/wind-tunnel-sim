#!/usr/bin/env bash
# Creates a "Virtual Wind Tunnel" launcher on the desktop and in the app menu.
# Run install_mac_linux.sh once first.
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
    echo "The simulator is not installed yet. Run: bash scripts/install_mac_linux.sh"
    exit 1
fi
.venv/bin/python -m windtunnel.shortcut "$@"
