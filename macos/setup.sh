#!/bin/bash
# First-launch installer for the AutoShorts engine (called by the app).
#   setup.sh <bundled backend dir> <runtime dir>
set -euo pipefail
SRC="$1"
RUNTIME="$2"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/miniforge3/bin:$HOME/.local/bin:$PATH"

echo "→ Checking for Python 3.11+"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])')
    if [ "$v" -ge 311 ]; then PY=$(command -v "$c"); break; fi
  fi
done
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    echo "→ Installing Python with Homebrew"; brew install python@3.13; PY=$(brew --prefix)/bin/python3.13
  else
    echo "✗ Python 3.11 or newer is required. Install Homebrew (https://brew.sh) then run: brew install python@3.13 ffmpeg"; exit 1
  fi
fi
echo "  using $PY"

echo "→ Checking for ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then echo "→ Installing ffmpeg with Homebrew"; brew install ffmpeg
  else echo "✗ ffmpeg is required: brew install ffmpeg"; exit 1; fi
fi

mkdir -p "$RUNTIME"
if [ ! -x "$RUNTIME/venv/bin/python" ]; then
  echo "→ Creating the engine environment"
  "$PY" -m venv "$RUNTIME/venv"
fi
"$RUNTIME/venv/bin/python" -m pip install --upgrade pip >/dev/null
echo "→ Installing AutoShorts (this downloads the AI libraries once)"
rm -rf "$RUNTIME/src" && cp -R "$SRC" "$RUNTIME/src"
"$RUNTIME/venv/bin/pip" install --progress-bar off "$RUNTIME/src[faceless]" 2>&1 | grep -vE "^\s*(Requirement already|Using cached)" || true
cp "$SRC/VERSION" "$RUNTIME/VERSION"
echo "✓ Engine installed"
