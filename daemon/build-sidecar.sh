#!/bin/sh
# Freeze the daemon into one executable for the desktop app's sidecar slot.
# Leaves out the SDK's bundled Claude Code (~350 MB): the daemon drives the user's installed `claude`.
set -e
cd "$(dirname "$0")"
PY=${PY:-../.venv/bin/python}
TRIPLE=$(rustc -vV | sed -n 's/^host: //p')
OUT=../desktop/src-tauri/binaries
"$PY" -m PyInstaller --onefile --clean --noconfirm --name switchboard-daemon \
  --exclude-module claude_agent_sdk._bundled --collect-submodules switchboard_daemon \
  --distpath build/dist --workpath build/work --specpath build \
  sidecar_entry.py
mkdir -p "$OUT"
cp build/dist/switchboard-daemon "$OUT/switchboard-daemon-$TRIPLE"
ls -la "$OUT"
