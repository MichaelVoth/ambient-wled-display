# Architecture

The project deliberately leaves WLED firmware unchanged. WLED remains the
pixel renderer and Home Assistant decides when information deserves attention.

## Layers

1. **WLED** stores durable looks as numbered presets and accepts temporary JSON
   state changes.
2. **Controllers** snapshot WLED, show information, and restore the snapshot.
3. **Home Assistant** supplies time, weather, presence, service health, and
   other context.
4. **LedFx** owns WLED only while realtime music output is active.

The controllers check WLED's `live` flag before displaying anything. This
prevents an hourly marker or notification from interrupting LedFx. Temporary
signals also restore the complete WLED segment state rather than guessing which
preset was previously visible.

## Priority model

Use these priorities when adding automations:

1. Safety and urgent failures
2. Realtime music
3. Short notifications
4. Persistent context such as rain
5. Decorative baseline

Home Assistant automation modes matter. Use `queued` for short signals that
must be seen, `restart` when only the newest state matters, and `single` for
scheduled events that should not overlap themselves.

## Why the hourly gaps use explicit segments

Some animated effects do not render WLED's grouping and spacing gaps clearly.
The controller leaves the original full-strip segment and its animation
untouched. It then overlays one solid-black segment for each hour. Removing the
overlays reveals the continuously running animation underneath, so the display
does not need a replacement effect and the restoration can fade cleanly.
Twelve bars need 13 segments, and the controller checks the device's advertised
segment limit before changing the display.
