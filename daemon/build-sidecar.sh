#!/bin/sh
# Freeze the daemon into one executable for the desktop app's sidecar slot.
# Leaves out the SDK's bundled Claude Code (~350 MB): the daemon drives the user's installed `claude`.
set -e
cd "$(dirname "$0")"
PY=${PY:-../.venv/bin/python}
TRIPLE=$(rustc -vV | sed -n 's/^host: //p')
EXTRA=""
case "$TRIPLE" in *windows*) EXTRA="--collect-all winpty" ;; esac  # ConPTY binding ships native DLLs
OUT=../desktop/src-tauri/binaries
# the web app goes inside the daemon, so phones and other browsers can open it from the daemon's address
[ -f ../desktop/build/index.html ] || (cd ../desktop && npm run build)
SEP=":"; case "$TRIPLE" in *windows*) SEP=";" ;; esac
"$PY" -m PyInstaller --onefile --clean --noconfirm --name switchboard-daemon \
  --exclude-module claude_agent_sdk._bundled --collect-submodules switchboard_daemon $EXTRA \
  --add-data "../../desktop/build${SEP}webui" \
  --distpath build/dist --workpath build/work --specpath build \
  sidecar_entry.py
EXT=""
case "$TRIPLE" in *windows*) EXT=".exe" ;; esac
mkdir -p "$OUT"
cp "build/dist/switchboard-daemon$EXT" "$OUT/switchboard-daemon-$TRIPLE$EXT"
ls -la "$OUT"
