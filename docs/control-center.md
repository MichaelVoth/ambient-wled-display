# Operating the Ambient Display Control Center

The renderer serves its control panel on port `8090`. On the current Pi this is
normally:

```text
http://raspberrypi.local:8090
```

The panel shows the exact two-lane frame being calculated, whether or not
physical output is enabled.

## Output modes

- **Renderer → WLED** continuously sends the composed ambient display to WLED.
- **Simulator only** keeps rendering the preview but sends no physical frames.
- **Legacy external music controller** is retained only as a recovery option.
- **Output stopped** is an administrative stopped state.

The **Everyday control** card translates these technical modes into intent:
Live automatically, Resume ambient, Dance to music, or Quiet. It also exposes
the emotional animations without requiring knowledge of WLED ownership. Raw
output selection, manual palettes, and live JSON are collapsed by default.

## Test controls

Choose an hour from 0–23 and select **Run hour**. The 12-hour count is derived
automatically. **Toggle rain** and **Toggle focus** demonstrate persistent
layers. The information-signal buttons run a calm reminder, green success
wave, amber warning, or multicolor work celebration. The urgent red alert
demonstrates priority replacement. **Cancel** returns immediately to the
continuously rendered base.

## Ambient nebula controls

The normal background is a continuous lava-lamp nebula rather than a looping
WLED preset. Six unequal color bodies range from a tiny pocket to wider than a
whole lane. They drift in different directions, slowly change size, overlap,
merge, and introduce new palette colors without an animation reset. This is why
one part of the wall may be almost entirely one color while small contrasting
islands appear elsewhere.

In **House chooses automatically**, local time selects the broad emotional arc:
dawn, morning, daylight, golden afternoon, evening, or night. Home Assistant
then modifies it with weather, temperature, and wind. Rain also enables a
stateful water layer: drops begin at irregular positions, fall at different
speeds, accelerate, and merge into faster rivulets. Tiny beads can cling and
stutter; heavy drops can fall quickly enough to catch smaller drops. Wet paths
remain briefly after the rain stops and then evaporate.

The mood card names the current emotional interpretation—such as radiant,
contemplative, brooding, stormy, cozy, or dreaming—and explains which inputs
caused it. **Emotional expression** changes the strength of that translation:
Quiet is subtle, Balanced is the default, and Expressive uses stronger color,
motion, and breathing without requiring a hand-picked palette.

Between explicit events, wind bends the color field into visible gusts and the
base may produce sparse, independently timed glimmers. These are intentionally
subtle and yield to rain, the hourly clock, music, and alerts.

On wider screens the exact physical-lane simulator is pinned in the left
column. The wider right column contains the controls and can scroll
independently beneath it.

The control center also provides a **manual** mode with:

- seven editable palette colors;
- Living, Ocean, Aurora, Cosmic, Sunset, and Ember starting looks;
- color-drift speed, shown as seconds per complete palette cycle;
- cloud size;
- saturation; and
- ambient brightness.

Select a starting look, adjust any colors or sliders, and choose **Use these
manual colors**. The old and new configurations crossfade over three seconds. The
result is stored in the renderer data volume and survives service, Pi, and Home
Assistant restarts. Rain, hourly tolls, and semantic signals are composited on
top of the chosen nebula.

## API examples

```bash
curl -X POST http://raspberrypi.local:8090/api/events/hour \
  -H 'Content-Type: application/json' \
  -d '{"hour":10,"take_output":true}'
```

`take_output` is the safe migration mode. If the service is in **Simulator
only**, it validates WLED, takes realtime control for the complete event, sends
one fully restored final frame, and automatically returns to **Simulator
only**. Existing WLED preset automations then continue normally. Once all
ambient behaviors have moved into renderer layers, use continuous **Renderer →
WLED** mode instead.

## Phone access

Open `http://raspberrypi.local:8090` in Safari while connected to home Wi-Fi.
Use **Share → Add to Home Screen** to install the control center as a home-screen
app. The Pi starts it automatically; no laptop terminal command is required.
If a page opened from a `file:///` address says it is the project file, follow
its live-controller link instead. The project file cannot control the house.

## Music lights

Choose **Play music through**, choose a light style, and select **Start music
lights**. That single action selects the named macOS Multi-Output Device,
starts private audio analysis, and adds a reactive layer inside the renderer.
The live meters confirm that bass, mids, treble, and beats are arriving. Hourly
and urgent semantic events can still be composed by the same central owner.

The four meters are a translation guide rather than a generic equalizer:

- **Bass** creates the largest waves and warmer heat.
- **Mids** (voices and instruments) shape the main body of the effect.
- **Highs** add small bright tips and sparks.
- **Beat** adds a short accent when a kick is detected.

Use **Background behind music** to control separation. **High contrast** turns
the ambient nebula into a mostly dark stage; **Balanced** keeps a dim sense of
the house behind the effect; **Soft blend** deliberately combines both layers.
The selected music layer still belongs to the renderer—not to WLED or LedFx.

Events and layers can be scoped to configured device or lane IDs. Omitting
`targets` means every configured lane:

```bash
curl -X POST http://raspberrypi.local:8090/api/events/hour \
  -H 'Content-Type: application/json' \
  -d '{"hour":3,"take_output":true,"targets":["office-wall-a"]}'
```

```bash
curl -X POST http://raspberrypi.local:8090/api/layers/rain \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true}'
```

Named signals use a fixed visual vocabulary rather than arbitrary presets:

```bash
curl -X POST http://raspberrypi.local:8090/api/events/signal \
  -H 'Content-Type: application/json' \
  -d '{"signal":"celebration","take_output":true}'
```

Supported signals are `welcome`, `comfort`, `curious`, `goodbye`, `storm`,
`reminder`, `success`, `warning`, and `celebration`.

Ambient settings can also be changed through the API:

```bash
curl -X POST http://raspberrypi.local:8090/api/ambient \
  -H 'Content-Type: application/json' \
  -d '{"preset":"cosmic","speed":0.006,"cloud_scale":1.5,"saturation":1.2,"brightness":0.8}'
```

Return control to the living house mood:

```bash
curl -X POST http://raspberrypi.local:8090/api/ambient \
  -H 'Content-Type: application/json' \
  -d '{"mode":"adaptive"}'
```

Home Assistant supplies the environmental context:

```bash
curl -X POST http://raspberrypi.local:8090/api/context \
  -H 'Content-Type: application/json' \
  -d '{"weather":"rainy","temperature":61,"temperature_unit":"°F","humidity":92}'
```

```bash
curl -X POST http://raspberrypi.local:8090/api/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode":"music"}'
```

## Recovery

Stopping the renderer does not rewrite or delete WLED configuration. Once UDP realtime
traffic stops, WLED leaves realtime mode after its normal timeout and returns
to its existing preset. The WLED web interface therefore remains a usable
fallback and troubleshooting tool.

The service also performs an HTTP preflight before taking output ownership. A
wrong address, offline controller, or configured pixel count larger than WLED's
reported count rejects the handoff instead of silently playing to nowhere.
