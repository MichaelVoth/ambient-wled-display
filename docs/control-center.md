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
- **Music / LedFx owns WLED** stops renderer output and clears temporary events.
- **Output stopped** is an administrative stopped state.

## Test controls

Choose an hour from 0–23 and select **Run hour**. The 12-hour count is derived
automatically. **Toggle rain** and **Toggle focus** demonstrate persistent
layers. **Test urgent alert** demonstrates priority replacement. **Cancel**
returns immediately to the continuously rendered base.

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

```bash
curl -X POST http://raspberrypi.local:8090/api/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode":"music"}'
```

## Recovery

Stopping the renderer does not rewrite or delete WLED configuration. Once DDP
traffic stops, WLED leaves realtime mode after its normal timeout and returns
to its existing preset. The WLED web interface therefore remains a usable
fallback and troubleshooting tool.

The service also performs an HTTP preflight before taking output ownership. A
wrong address, offline controller, or configured pixel count larger than WLED's
reported count rejects the handoff instead of silently playing to nowhere.
