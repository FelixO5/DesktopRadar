#!/bin/bash
# Build RadarScope.app and put it in your Applications folder.
#
# Run from the DesktopRadar folder (the one with README.md in it):
#     bash packaging/macos/build_app.sh
#
# It uses the project's .venv if there is one, installs the build tool
# (PyInstaller) into it, builds the app, and copies it to /Applications
# (or ~/Applications if /Applications isn't writable). Takes a few minutes.
set -euo pipefail

cd "$(dirname "$0")/../.."
# Absolute paths throughout: PyInstaller resolves relative --icon/--paths
# against the spec file's folder (build/), not the folder you ran it from.
ROOT="$(pwd)"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "This script builds a macOS app and must be run on a Mac." >&2
    exit 1
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PY="$VIRTUAL_ENV/bin/python"
elif [[ -x .venv/bin/python ]]; then
    PY=".venv/bin/python"
else
    echo "No virtual environment found. Create it first:" >&2
    echo "    python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

"${PY}" - <<'PYCHECK'
import sys
if sys.version_info < (3, 11):
    sys.exit(f"Python 3.11+ is needed, this is {sys.version.split()[0]}")
PYCHECK

echo "==> Installing RadarScope's libraries and the app builder (PyInstaller)..."
"${PY}" -m pip install --quiet --disable-pip-version-check -r requirements.txt
"${PY}" -m pip install --quiet --disable-pip-version-check --upgrade pyinstaller pillow

echo "==> Drawing the app icon..."
ICON="${ROOT}/build/RadarScope-icon.png"
"${PY}" "${ROOT}/packaging/make_icon.py" "${ICON}" 1024 >/dev/null

echo "==> Building RadarScope.app (this can take a few minutes)..."
"${PY}" -m PyInstaller --noconfirm --clean --log-level WARN \
    --windowed \
    --name RadarScope \
    --icon "${ICON}" \
    --osx-bundle-identifier io.github.felixo5.radarscope \
    --paths "${ROOT}" \
    --distpath "${ROOT}/dist" --workpath "${ROOT}/build/pyinstaller" --specpath "${ROOT}/build" \
    "${ROOT}/packaging/radarscope_app.py"

APP="${ROOT}/dist/RadarScope.app"
if [[ ! -d "${APP}" ]]; then
    echo "Build failed: ${APP} was not created." >&2
    exit 1
fi

DEST="/Applications"
if [[ ! -w "${DEST}" ]]; then
    DEST="${HOME}/Applications"
    mkdir -p "${DEST}"
fi
echo "==> Copying to ${DEST}..."
rm -rf "${DEST}/RadarScope.app"
ditto "${APP}" "${DEST}/RadarScope.app"

echo
echo "Done! RadarScope is in ${DEST}."
echo "Open it from Launchpad or Spotlight (Cmd+Space, type RadarScope)."
echo "To keep it in the Dock: while it's running, right-click its Dock icon > Options > Keep in Dock."
