# Renderer Performance Audit

## Incident

At 9 PM on August 24, 2026, the hourly event looked close to one frame per
second even though the renderer reported 20 FPS. The event timing and generated
frames were correct. The failure was in the last hop: WLED 0.15.4 displayed only
2–3 FPS while receiving the continuous DDP stream.

The original metric counted frames sent by the Pi. It did not measure frames
accepted by the ESP32, so the control center could report healthy while the
physical strip was visibly failing.

## Critical work list

### P0 — required for trustworthy hourly events

- [x] Replace DDP on the current 278-pixel ESP32 with one-packet WLED DRGB UDP
  realtime frames.
- [x] Measure both Pi frame rate and WLED's reported display rate.
- [x] Make receiver lag, a stale probe, or the wrong realtime owner fail health.
- [x] Track recent FPS, 95th-percentile and worst frame spacing, send latency,
  byte count, and missed deadlines.
- [x] Log every event phase with its actual start time and frame number.
- [x] Replay the nine-o'clock sequence on the live connected lane.

### P1 — hardening and operations

- [x] Keep DDP as an explicit opt-in transport for future controllers while
  making UDP realtime the safe default for stock WLED controllers up to 490
  pixels.
- [x] Keep receiver probing off the rendering thread so a slow HTTP response
  cannot make the animation stutter.
- [x] Show sender FPS, receiver FPS, transport, jitter, and health issues in the
  control center.
- [x] Preserve automatic startup, Home Assistant event ownership, and LedFx
  music handoff.
- [ ] Add a visual recording test after the second physical lane is repaired.
- [ ] Run a 24-hour soak and alert Home Assistant if receiver health remains bad
  for two consecutive probes.

### P2 — future capacity

- [ ] Add per-controller adaptive frame-rate profiles when the house has mixed
  ESP8266 and ESP32 devices.
- [ ] Add packet sequence/latency telemetry for transports that acknowledge
  frames.
- [ ] Evaluate a stable WLED firmware upgrade only after exporting the WLED
  configuration and testing rollback on a spare controller.

## Live acceptance result

The replacement was tested at 30 FPS with the real Pi, Wi-Fi network, ESP32,
and connected 139-pixel lane. The complete 9 PM timeline held 30 FPS at the Pi
and WLED through sweep, blackout, nine tolls, hold, and restore. It recorded
zero missed deadlines; typical 95th-percentile frame spacing was 33.4 ms and
the observed maximum during the run was below 35 ms.

## Acceptance criteria for future changes

A physical animation is healthy only when all of these are true:

1. Sender and receiver remain at least 80% of the configured FPS.
2. No missed render deadlines occur during the event.
3. Worst recent frame spacing remains below twice the target interval.
4. WLED reports the configured realtime transport and the Pi as its source.
5. The event log contains each expected phase in order.
6. The physical lane is observed or recorded when visual behavior changes.
