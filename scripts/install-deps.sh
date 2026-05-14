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

# Use mise for a consistent Python
if ! command -v mise >/dev/null 2>&1; then
    echo "[memesis] ERROR: mise is required. Install from https://mise.jdx.dev" >&2
    exit 1
fi

mise install python@3.13 >/dev/null 2>&1 || true
PYTHON=$(mise which python 2>/dev/null) || {
    echo "[memesis] ERROR: Failed to get Python from mise" >&2
    exit 1
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
