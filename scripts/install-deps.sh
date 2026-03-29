#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="${1:?Usage: install-deps.sh <PLUGIN_ROOT> <PLUGIN_DATA>}"
PLUGIN_DATA="${2:?Usage: install-deps.sh <PLUGIN_ROOT> <PLUGIN_DATA>}"
VENV_DIR="$PLUGIN_DATA/venv"
REQUIREMENTS="$PLUGIN_ROOT/requirements.txt"
STAMP="$PLUGIN_DATA/requirements.txt"

# Skip if deps are current
if diff -q "$REQUIREMENTS" "$STAMP" >/dev/null 2>&1; then
    exit 0
fi

echo "[memesis] Installing dependencies..." >&2

# Find a Python with enable_load_extension support
find_good_python() {
    for candidate in python3 python3.12 python3.13 python3.14 /opt/homebrew/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c "import sqlite3; sqlite3.connect(':memory:').enable_load_extension(True)" 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_good_python) || {
    echo "[memesis] No Python with sqlite extension loading found. Installing via mise..." >&2
    if command -v mise >/dev/null 2>&1; then
        # mise-installed Python compiles from source with Homebrew SQLite
        # which includes enable_load_extension
        mise install python@3.13 >/dev/null 2>&1 || true
        PYTHON=$(mise which python 2>/dev/null) || PYTHON="python3"
        # Verify it worked
        if ! "$PYTHON" -c "import sqlite3; sqlite3.connect(':memory:').enable_load_extension(True)" 2>/dev/null; then
            echo "[memesis] WARNING: Could not find Python with extension loading. Vector search will be disabled." >&2
            PYTHON="python3"
        fi
    else
        echo "[memesis] WARNING: mise not installed. Vector search will be disabled." >&2
        PYTHON="python3"
    fi
}

echo "[memesis] Using Python: $PYTHON ($($PYTHON --version 2>&1))" >&2

# Create venv
mkdir -p "$PLUGIN_DATA"
"$PYTHON" -m venv --clear "$VENV_DIR"

# Install deps
"$VENV_DIR/bin/pip" install -q -r "$REQUIREMENTS"

# Download NLTK data into plugin data dir
NLTK_DATA="$PLUGIN_DATA/nltk_data" "$VENV_DIR/bin/python3" - <<'PYEOF'
import os, sys, nltk
data_dir = os.environ["NLTK_DATA"]
ok = nltk.download("stopwords", download_dir=data_dir, quiet=True)
if not ok:
    print("[memesis] WARNING: NLTK stopwords download failed", file=sys.stderr)
    sys.exit(1)
PYEOF

# Stamp
cp "$REQUIREMENTS" "$STAMP"
echo "[memesis] Dependencies installed." >&2
