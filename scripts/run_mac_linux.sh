#!/usr/bin/env bash
# Starts the Virtual Wind Tunnel (run install_mac_linux.sh once first).
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
    echo "The simulator is not installed yet. Run: bash scripts/install_mac_linux.sh"
    exit 1
fi
exec .venv/bin/python -m windtunnel "$@"
