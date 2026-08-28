#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${HOME}/Library/Application Support/AmbientWLED"
LAUNCH_DIR="${HOME}/Library/LaunchAgents"
PLIST="${LAUNCH_DIR}/com.michaelvoth.ambient-wled-music.plist"

mkdir -p "$INSTALL_DIR" "$LAUNCH_DIR" "$INSTALL_DIR/logs"
xcrun clang "$SOURCE_DIR/audio-route.c" -o "$INSTALL_DIR/audio-route" -framework CoreAudio -framework CoreFoundation
cp "$SOURCE_DIR/companion.py" "$INSTALL_DIR/companion.py"
if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
  cp "$SOURCE_DIR/config.example.json" "$INSTALL_DIR/config.json"
fi
python3 - "$INSTALL_DIR/config.json" "$INSTALL_DIR/audio-route" <<'PY'
import json, pathlib, sys
path=pathlib.Path(sys.argv[1])
data=json.loads(path.read_text())
data["audio_route_tool"]=sys.argv[2]
# Directly analyze the BlackHole audio copy. This removes LedFx from the live
# path, avoids its virtual-device controller, and keeps audio capture local.
if data.get("capture_backend", "ledfx") == "ledfx":
    data["capture_backend"]="ffmpeg"
path.write_text(json.dumps(data, indent=2)+"\n")
PY

sed \
  -e "s|__PYTHON__|/usr/bin/python3|g" \
  -e "s|__COMPANION__|${INSTALL_DIR}/companion.py|g" \
  -e "s|__CONFIG__|${INSTALL_DIR}/config.json|g" \
  -e "s|__LOG_DIR__|${INSTALL_DIR}/logs|g" \
  "$SOURCE_DIR/launch-agent.plist.template" > "$PLIST"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/com.michaelvoth.ambient-wled-music"
printf 'Music companion installed. It will start automatically when you sign in.\n'
