#!/usr/bin/env bash
set -euo pipefail

LEDFX_URL="${LEDFX_URL:-http://127.0.0.1:8889}"
LEDFX_CONFIG_DIR="${LEDFX_CONFIG_DIR:-$HOME/.ledfx}"
LEDFX_VIRTUAL_ID="${LEDFX_VIRTUAL_ID:-office-wled}"
LEDFX_AUDIO_DEVICE_MATCH="${LEDFX_AUDIO_DEVICE_MATCH:-BlackHole 2ch}"

api_ready() { curl --silent --fail "${LEDFX_URL%/}/api/info" >/dev/null 2>&1; }

find_app() {
  if [[ -n "${LEDFX_APP:-}" && -d "$LEDFX_APP" ]]; then printf '%s\n' "$LEDFX_APP"; return; fi
  find /Applications -maxdepth 1 -type d -name 'LedFx*.app' -print -quit
}

select_audio_device() {
  local devices device
  devices="$(curl --silent --fail "${LEDFX_URL%/}/api/audio/devices")"
  device="$(printf '%s' "$devices" | python3 -c '
import json, os, sys
devices=json.load(sys.stdin).get("devices", {})
match=os.environ.get("LEDFX_AUDIO_DEVICE_MATCH", "BlackHole 2ch").lower()
for key, name in devices.items():
    if match in str(name).lower(): print(key); break
')"
  [[ -n "$device" ]] || { printf 'Audio device matching "%s" was not found.\n' "$LEDFX_AUDIO_DEVICE_MATCH" >&2; return 1; }
  curl --silent --fail -X PUT -H 'Content-Type: application/json' \
    --data "{\"audio_device\":${device}}" "${LEDFX_URL%/}/api/audio/devices" >/dev/null
  printf 'Audio input: %s (device %s)\n' "$LEDFX_AUDIO_DEVICE_MATCH" "$device"
}

start_ledfx() {
  if ! api_ready; then
    local app
    app="$(find_app)"
    [[ -n "$app" ]] || { printf 'LedFx application not found. Set LEDFX_APP.\n' >&2; exit 1; }
    mkdir -p "$LEDFX_CONFIG_DIR"
    open -n "$app" --args --no-tray --offline -c "$LEDFX_CONFIG_DIR" -p "${LEDFX_URL##*:}"
    for _ in {1..40}; do api_ready && break; sleep 0.5; done
  fi
  api_ready || { printf 'LedFx did not become ready at %s\n' "$LEDFX_URL" >&2; exit 1; }
  select_audio_device
  printf 'LedFx is ready at %s\n' "$LEDFX_URL"
}

activate_scene() {
  local scene_var="$1" fallback="$2" scene
  scene="${!scene_var:-$fallback}"
  curl --fail --show-error --silent -X PUT -H 'Content-Type: application/json' \
    --data "{\"id\":\"${scene}\",\"action\":\"activate\"}" "${LEDFX_URL%/}/api/scenes" >/dev/null
  printf 'Activated scene: %s\n' "$scene"
}

case "${1:-help}" in
  start) start_ledfx ;;
  energy) start_ledfx; activate_scene LEDFX_SCENE_ENERGY music-energy ;;
  spectrum) start_ledfx; activate_scene LEDFX_SCENE_SPECTRUM music-spectrum ;;
  wavelength) start_ledfx; activate_scene LEDFX_SCENE_WAVELENGTH music-wavelength ;;
  stop)
    effect="$(curl --silent --fail "${LEDFX_URL%/}/api/virtuals/${LEDFX_VIRTUAL_ID}/effects" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("effect") or {}).get("type", ""))')"
    [[ -z "$effect" ]] || curl --silent --fail -X POST -H 'Content-Type: application/json' \
      --data "{\"type\":\"${effect}\"}" "${LEDFX_URL%/}/api/virtuals/${LEDFX_VIRTUAL_ID}/effects/delete" >/dev/null
    ;;
  status) api_ready && printf 'LedFx is running.\n' || { printf 'LedFx is stopped.\n'; exit 1; } ;;
  *) printf 'Usage: %s {start|energy|spectrum|wavelength|status|stop}\n' "$0" ;;
esac
