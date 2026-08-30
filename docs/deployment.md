# Deployment and Operations

## Current office installation

The Raspberry Pi runs the renderer as a separate Docker service so it cannot
collide with the development site or Home Assistant ports.

| Service | Address | Role |
| --- | --- | --- |
| Home Assistant | `http://raspberrypi.local:8123` | Context, schedules, and semantic triggers |
| Ambient Display Control Center | `http://raspberrypi.local:8090` | Simulator, compositor, arbitration, and logs |
| WLED | `http://wled-02b934.local` | LED hardware, electrical limits, and fallback presets |

The deployed files live in `/home/michaelvoth/ambient-renderer` on the Pi. The
connected 139-LED office lane now starts in continuous renderer mode. The
unplugged second output is intentionally absent from active lane configuration.

## Everyday use

No terminal command is required. Docker starts the renderer after a Pi reboot.

1. Open the control center.
2. Enter an hour.
3. Select **Preview hour** to inspect only the simulator, or **Run on WLED** for
   a real test. In continuous mode both use the same central timeline.
4. Use **Cancel** at any point to return to the renderer's ambient base.

Use the **Ambient nebula** panel to choose a named color look or edit all seven
palette colors directly. Unequal lava-like bodies create both very short and
whole-wall color regions.
Changes are written to the renderer's persistent data volume and do not require
a container restart.

In Home Assistant, run **Test Ambient Renderer Hour** and choose a value from
0–23. The normal hourly automation calls this same renderer path when the WLED
renderer is active. Home Assistant now calls renderer-native signals for the
Tuesday–Friday 4 PM celebration, weekday lunch reminders, Pi-hole failures,
GitHub build failures, and high-energy-use warnings. Rain is a persistent
renderer layer. Morning, midnight, and arrival/departure now use the renderer's
explicit power endpoint. Morning On runs in all weather and retries until
confirmed; a saved daily receipt preserves later manual Off commands. The old
startup preset writer has been removed. Midnight and departure switch off
directly rather than playing legacy sweep presets.

## Adding another strip

1. Give the WLED controller a DHCP reservation and a descriptive mDNS name.
2. Add a device entry to `renderer-config.json` with its total logical pixel
   count and one lane per physical run.
3. Set `top_at_high_index` from the strip's real wiring direction.
4. Restart the renderer in preview mode and verify the new simulator lanes.
5. Run a targeted test using the device or lane ID.
6. Enable continuous output only after every legacy behavior for that room has
   a renderer equivalent.

The default UDP realtime transport supports up to 490 pixels in one complete
frame. Larger controllers can opt into DDP, which automatically splits frames.
Every output is preflighted against WLED's reported pixel count before physical
ownership.

## Recovery and rollback

- Select **Simulator only** or **Output stopped** to stop renderer frames.
- Use the **Music lights** card for normal playback. Speaker routing, private
  audio analysis, and renderer activation happen behind one Start/Stop button.
- The Mac companion starts automatically at login and re-registers its current
  network address with the Pi. A brief Pi or Wi-Fi interruption no longer ends
  the analysis session.
- If the renderer container stops, WLED returns to its current preset after the
  realtime timeout.
- Ambient color controls are stored in `/data/ambient-settings.json` inside the
  renderer data volume. Deployment configuration remains the fallback.
- Receiver health is not inferred from the Pi sender. The control center probes
  WLED every five seconds and reports its display FPS and current realtime owner.
- Home Assistant backups created during this installation end in
  `.bak-renderer-20260824` or `.bak-semantic-signals-20260824` beside the active
  YAML files.
- The legacy hourly Python controller remains in the Home Assistant config as a
  rollback artifact, but nothing calls it now.

## Rollout path

The renderer owns the ambient base, hourly timeline, rain, and master power
and brightness for the connected office lane. Home Assistant supplies
schedules and context. The work celebration, lunch reminder, Pi-hole warning,
GitHub build warning, and high-energy-use warning are renderer-native events.
Legacy sweep scripts remain available for recovery but are no longer called
by the morning, midnight, or departure automations.

See [the performance audit](performance-audit.md) for the 9 PM stutter root
cause, live benchmark, and remaining capacity work.
