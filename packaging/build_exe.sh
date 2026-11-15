#!/usr/bin/env bash
# Builds a LogScope executable (no console window where supported).
# Run from anywhere: ./packaging/build_exe.sh
set -euo pipefail

cd "$(dirname "$0")/.."

uv sync
uv run python -m unittest discover -s tests
uv run --with pyinstaller pyinstaller packaging/logscope.spec --noconfirm --clean

zip -r dist/LogScope-0.1.0-linux.zip dist/LogScope

echo ""
echo "Done: dist/LogScope/LogScope (or LogScope.exe on Windows)"
echo "Zip:  dist/LogScope-0.1.0-linux.zip"
echo "Copy the whole dist/LogScope folder, or upload the zip to a GitHub Release."