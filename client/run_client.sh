#!/usr/bin/env bash
# run_client.sh: Ensure venv, install requirements, and run main.py
set -e


VENV_DIR="venv"
REQ_FILE="requirements.txt"
MAIN_FILE="main.py"
PYTHON_BIN="$VENV_DIR/bin/python3"
STAMP_FILE=".venv_installed.stamp"


# 1. Create venv if it doesn't exist
venv_created=0
if [ ! -d "$VENV_DIR" ]; then
    echo "[INFO] Creating virtual environment in $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
    venv_created=1
fi

# 2. Activate venv
source "$VENV_DIR/bin/activate"

# 3. Install requirements if needed
install_needed=0
if [ $venv_created -eq 1 ]; then
    install_needed=1
elif [ ! -f "$STAMP_FILE" ]; then
    install_needed=1
elif [ "$REQ_FILE" -nt "$STAMP_FILE" ]; then
    install_needed=1
fi

if [ $install_needed -eq 1 ] && [ -f "$REQ_FILE" ]; then
    echo "[INFO] Installing requirements from $REQ_FILE..."
    pip install --upgrade pip
    pip install -r "$REQ_FILE"
    touch "$STAMP_FILE"
fi

echo "[INFO] Running $MAIN_FILE..."
exec "$PYTHON_BIN" "$MAIN_FILE"
