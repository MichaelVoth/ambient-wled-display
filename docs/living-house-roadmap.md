# Living House Roadmap

The display is organized around three kinds of behavior:

1. **Instincts** continuously shape the ambient base from time, sunlight,
   weather, temperature, wind, humidity, and presence.
2. **Emotions** are short, composited animations such as welcome, comfort,
   curiosity, goodbye, success, warning, celebration, and storm.
3. **Information** is deliberately countable or glanceable, such as the hourly
   bell toll, rain, service health, and energy warnings.

## Connected now

| Input or event | Meaning on the strip |
| --- | --- |
| Time and solar elevation | Dawn, daylight, afternoon, evening, and night palettes |
| Wind | Organic spatial gusts through the nebula rather than only faster motion |
| Weather and outdoor temperature | Palette warmth, saturation, cloud size, and emotional label |
| Rain | Clinging beads, fast heavy drops, merging rivulets, and evaporating wet trails |
| Lightning weather | Irregular cool-white storm-flash cluster |
| Presence | Inhabited mood context and a warm welcome animation on arrival |
| Hour boundary | Top-down blackout sweep and cumulative bell tolls |
| Workday schedule | Lunch reminders and end-of-work celebration |
| Pi-hole, energy, and build health | Calm warning vocabulary rather than arbitrary effects |
| Music mode | Exclusive handoff from the renderer to LedFx |
| Calm inhabited periods | Sparse glimmers with independent timing between major events |

## Best next connections

These are useful but need one product decision or additional sensor before they
should be automated:

| Candidate | Proposed display | What is still needed |
| --- | --- | --- |
| Personal calendar | A small amber horizon that grows during the final 15 minutes before an event | Choose which calendar and whether all-day events count |
| Office HomePod | Music ownership and track-derived palette | A reliable audio-analysis path for AirPlay/HomePod playback |
| Indoor temperature and air quality | Warm/cool tint plus a slow breathing-rate change | Indoor climate or air-quality entities are not currently available |
| Doors and locks | Brief arrival/departure ripples and an unsecured warning | Door/lock contact entities are not currently available |
| Sleep or focus state | Quieter motion, lower expression, and suppressed nonurgent events | A dependable focus/sleep source and quiet-hour policy |
| Multiple rooms | Emotions can travel between rooms or target only the relevant space | Add the next WLED device and assign room groups |
| Stairs | Directional path lighting plus navigation and alert information | Complete the stair hardware layout and safety rules |

## Interface direction

Everyday controls should describe intent—**Live automatically**, **Dance to
music**, **Quiet**, **Welcome**, or **Comfort**—and hide transport ownership and
raw renderer state in collapsed technical sections. Automatic triggers should
remain visible as a readable list even when they are managed by Home Assistant.
Future trigger toggles should be backed by Home Assistant helpers rather than
pretending the renderer owns those schedules.
