#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install.sh — Bootstrap script for yappy-cli-manager
#
# Run once after cloning:
#   bash install.sh
#
# This script:
#   1. Installs the package in editable mode (pip install -e .)
#   2. Ensures the Python Scripts directory is on PATH in .bashrc
#   3. Sources .bashrc so yappy is available immediately
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== yappy-cli-manager installer ==="
echo ""

# 1. Install the package
echo "[1/3] Installing package (pip install -e .) ..."
pip install -e . || { echo "ERROR: pip install failed"; exit 1; }

# 2. Determine Python Scripts directory (Windows/MINGW64 path)
SCRIPTS_DIR="$(python -c "import sys; from pathlib import Path; print(str(Path(sys.executable).parent / 'Scripts'))")"

# Convert Windows path to POSIX for .bashrc
win_to_posix() {
    local p="${1//\\//}"
    if [[ "$p" =~ ^[A-Za-z]:/ ]]; then
        p="/${p:0:1}${p:2}"
        p="${p,,}"  # lowercase drive letter
    fi
    echo "$p"
}

SCRIPTS_POSIX="$(win_to_posix "$SCRIPTS_DIR")"
BASHRC="$HOME/.bashrc"
PATH_EXPORT="export PATH=\"\$PATH:$SCRIPTS_POSIX\""

# 3. Add to .bashrc if not already present
if [ -f "$BASHRC" ] && grep -qF "$SCRIPTS_POSIX" "$BASHRC"; then
    echo "[2/3] Python Scripts PATH already in .bashrc ✓"
else
    echo "" >> "$BASHRC"
    echo "# yappy: Python Scripts on PATH" >> "$BASHRC"
    echo "$PATH_EXPORT" >> "$BASHRC"
    echo "[2/3] Added Python Scripts to PATH in .bashrc ✓"
fi

# 4. Export PATH for this session
export PATH="$PATH:$SCRIPTS_POSIX"

# 5. Verify yappy is accessible
echo "[3/3] Verifying installation..."
if command -v yappy &>/dev/null; then
    echo ""
    echo "✓ yappy installed successfully! ($(yappy version 2>&1 || true))"
    echo ""
    echo "Next steps:"
    echo "  source ~/.bashrc   # or open a new terminal"
    echo "  yappy setup        # one-time project setup"
else
    echo ""
    echo "⚠ yappy.exe not found after install."
    echo "  Scripts directory: $SCRIPTS_DIR"
    echo "  Try: source ~/.bashrc && yappy setup"
fi
