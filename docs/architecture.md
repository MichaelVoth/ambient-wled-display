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

Scheduled collisions should be resolved before they reach WLED. The example
gives midnight shutdown, 6 AM startup, and the Tuesday-Friday 4 PM workday
celebration exclusive ownership of those times. The hourly controller also
checks that it still owns the displayed state before restoring; an alert or
weather change that arrives mid-animation is therefore allowed to take over.

## Why the hourly display uses rendered pixels

WLED effects are continuous loops, so a fixed delay cannot guarantee that a
one-way sweep has reached the bottom before the next phase begins. The hourly
controller instead captures WLED's rendered RGB framebuffer, freezes that exact
visible palette, and progressively blacks the pixels from each physical top to
its bottom over a fixed duration. The last sweep step is therefore guaranteed
to be the fully dark frame.

The controller reads physical output boundaries from `/json/cfg`. Each output
is swept independently but simultaneously, and each receives the same toll
pattern near its own top. WLED's individual-pixel JSON control allows the hour
to use one segment per physical output, so two 139-LED wall strips consume only
two segments even at twelve o'clock. With the default spacing, twelve tolls
occupy the top 34 LEDs of each output.

The controller snapshots the complete original state before it begins and fades
back to that snapshot after holding the completed hour for five seconds. Before
each phase and before restoration, it verifies that no other automation has
taken control. If ownership changed, the hourly display yields instead of
restoring stale state over a newer alert, weather state, or manual selection.
