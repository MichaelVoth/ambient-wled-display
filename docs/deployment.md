# Deployment and Operations

## Current office installation

The Raspberry Pi runs the renderer as a separate Docker service so it cannot
collide with the development site or Home Assistant ports.

| Service | Address | Role |
| --- | --- | --- |
| Home Assistant | `http://raspberrypi.local:8123` | Context, schedules, and semantic triggers |
| Ambient Display Control Center | `http://raspberrypi.local:8090` | Simulator, compositor, arbitration, and logs |
| WLED | `http://wled-02b934.local` | LED hardware, electrical limits, and fallback presets |

The deployed files live in `/home/michaelvoth/ambient-renderer` on the Pi. Its
configuration starts in simulator-only mode. Home Assistant uses temporary
output ownership for the hourly clock, so all older WLED preset automations
continue to function during migration.

## Everyday use

No terminal command is required. Docker starts the renderer after a Pi reboot.

1. Open the control center.
2. Enter an hour.
3. Select **Preview hour** to inspect only the simulator, or **Run on WLED** for
   a real test that releases the strip automatically.
4. Use **Cancel** at any point to release a temporary output lease.

In Home Assistant, run **Test Ambient Renderer Hour** and choose a value from
0–23. The normal hourly automation calls this same renderer path when the WLED
light is on. Midnight, 6 AM, and Tuesday–Friday at 4 PM remain reserved for the
existing off, morning, and workday celebration behaviors.

## Adding another strip

1. Give the WLED controller a DHCP reservation and a descriptive mDNS name.
2. Add a device entry to `renderer-config.json` with its total logical pixel
   count and one lane per physical run.
3. Set `top_at_high_index` from the strip's real wiring direction.
4. Restart the renderer in preview mode and verify the new simulator lanes.
5. Run a targeted test using the device or lane ID.
6. Enable continuous output only after every legacy behavior for that room has
   a renderer equivalent.

DDP frames are automatically split for large controllers. Every output is
preflighted against WLED's reported pixel count before physical ownership.

## Recovery and rollback

- Select **Simulator only** or **Output stopped** to stop renderer frames.
- Select **Music / LedFx owns WLED** before music-reactive playback.
- The included LedFx launcher makes that mode change automatically on start and
  returns the renderer to preview when LedFx stops. `AMBIENT_RENDERER_URL`
  identifies the Pi control center.
- If the renderer container stops, WLED returns to its current preset after the
  realtime timeout.
- Home Assistant backups created during this installation end in
  `.bak-renderer-20260824` beside the active YAML files.
- The legacy hourly Python controller remains in the Home Assistant config as a
  rollback artifact, but nothing calls it now.

## Rollout path

The current system is deliberately in mixed mode: the renderer owns the robust
hourly timeline, while legacy WLED presets still own morning, midnight, rain,
arrival, lunch, and celebration behaviors. The next migration step is to model
each of those as a named renderer layer or event. When that list is complete,
set `output_enabled` to `true` and make the renderer the continuous owner.
