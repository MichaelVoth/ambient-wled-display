#!/usr/bin/env bash
set -euo pipefail

WLED_URL="${WLED_URL:-http://wled.local}"
WLED_LED_COUNT="${WLED_LED_COUNT:-300}"

save_preset() {
  local id="$1" name="$2" state="$3"
  curl --fail --show-error --silent \
    -H 'Content-Type: application/json' \
    --data "{\"psave\":${id},\"n\":\"${name}\",\"ql\":\"\",${state}}" \
    "${WLED_URL%/}/json/state" >/dev/null
  printf 'Installed preset %s: %s\n' "$id" "$name"
}

# IDs 1 and 2 are stable restoration targets used by the Home Assistant examples.
save_preset 1 "Ambient Morning" "\"on\":true,\"bri\":150,\"transition\":12,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":0,\"pal\":39,\"on\":true}]"
save_preset 2 "Ambient Off" '"on":false,"transition":12'
save_preset 8 "Happy Sparkle" "\"on\":true,\"bri\":190,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":20,\"sx\":150,\"ix\":120,\"pal\":11}]"
save_preset 9 "Rain" "\"on\":true,\"bri\":125,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":43,\"sx\":75,\"ix\":150,\"pal\":1}]"
save_preset 10 "Angry Pulse" "\"on\":true,\"bri\":210,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":2,\"sx\":185,\"ix\":180,\"col\":[[255,0,0],[80,0,0],[0,0,0]]}]"
save_preset 11 "Urgent Alarm" "\"on\":true,\"bri\":230,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":1,\"sx\":210,\"ix\":220,\"col\":[[255,0,0],[255,150,0],[0,0,0]]}]"
save_preset 12 "Workday Party" "\"on\":true,\"bri\":190,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":9,\"sx\":145,\"ix\":160,\"pal\":11}]"
save_preset 13 "Calm Ping" "\"on\":true,\"bri\":135,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":2,\"sx\":80,\"ix\":80,\"col\":[[0,180,190],[0,50,70],[0,0,0]]}]"
save_preset 14 "Success" "\"on\":true,\"bri\":175,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":46,\"sx\":120,\"ix\":110,\"col\":[[0,255,70],[0,70,15],[0,0,0]]}]"
save_preset 15 "Warning" "\"on\":true,\"bri\":180,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":2,\"sx\":105,\"ix\":100,\"col\":[[255,130,0],[80,25,0],[0,0,0]]}]"
save_preset 16 "Sweep Up" "\"on\":true,\"bri\":165,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":47,\"sx\":135,\"ix\":128,\"rev\":false,\"pal\":0}]"
save_preset 22 "Sad Droop" "\"on\":true,\"bri\":105,\"seg\":[{\"id\":0,\"start\":0,\"stop\":${WLED_LED_COUNT},\"fx\":47,\"sx\":70,\"ix\":100,\"rev\":true,\"col\":[[20,70,255],[0,10,45],[0,0,0]]}]"

curl --fail --show-error --silent "${WLED_URL%/}/presets.json" | \
  python3 -c 'import json,sys; data=json.load(sys.stdin); print("Verified:"); [print(f"  {key}: {value.get(chr(110), chr(63))}") for key,value in sorted(data.items(), key=lambda item: int(item[0])) if key in {"1","2","8","9","10","11","12","13","14","15","16","22"}]'
