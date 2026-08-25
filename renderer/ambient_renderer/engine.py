"""Threaded renderer, event arbitration, state, and DDP frame delivery."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .color import RGB, mix, palette_color, parse_hex, smoothstep
from .config import RendererConfig
from .effects import (
    AlertEvent,
    HourEvent,
    SignalEvent,
    render_alert,
    render_base,
    render_hour,
    render_signal,
)
from .output import create_output
from .mood import adaptive_ambient
from .rain import RainField


LOGGER = logging.getLogger(__name__)

AMBIENT_PRESETS = {
    "living": ["#10143f", "#176b9e", "#25b88a", "#d6c54a", "#e8734f", "#bc4f9d", "#5d45ad"],
    "ocean": ["#06142e", "#075f73", "#42a6a1", "#bac7bd", "#315f8c"],
    "aurora": ["#07112f", "#164e8a", "#16a085", "#65d98b", "#7656b7"],
    "cosmic": ["#09051d", "#30206f", "#c43aa2", "#2179a8", "#35d0c5"],
    "sunset": ["#11183b", "#63305d", "#c64f68", "#f49a52", "#f3c87a"],
    "ember": ["#17080d", "#5c1720", "#b33a2e", "#e87838", "#f2bd65"],
}


class RendererEngine:
    def __init__(self, config: RendererConfig) -> None:
        self.config = config
        self.log_path = Path(config.log_path)
        self.settings_path = Path(config.settings_path)
        initial_ambient = self._validate_ambient({
            "mode": "adaptive",
            "palette": list(config.palette),
            "speed": config.palette_speed,
            "cloud_scale": config.cloud_scale,
            "saturation": config.saturation,
            "brightness": config.ambient_brightness,
        })
        try:
            persisted = json.loads(self.settings_path.read_text(encoding="utf-8"))
            initial_ambient = self._validate_ambient(persisted, initial_ambient)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            LOGGER.warning("could not load ambient settings: %s", exc)
        self.ambient_target = initial_ambient
        self.ambient_from = initial_ambient
        self.ambient_changed_at = time.monotonic() - 3.0
        self.ambient_transition = 3.0
        self.context_path = self.settings_path.with_name("house-context.json")
        self.context = {
            "weather": "unknown",
            "temperature": None,
            "temperature_unit": "°F",
            "humidity": None,
            "cloud_coverage": None,
            "wind_speed": None,
            "updated_at": None,
        }
        try:
            persisted_context = json.loads(self.context_path.read_text(encoding="utf-8"))
            self.context = self._validate_context(persisted_context, self.context)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            LOGGER.warning("could not load house context: %s", exc)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.monitor_thread: threading.Thread | None = None
        self.mode = "renderer" if config.output_enabled else "preview"
        self.layers = {"rain": False, "focus": False}
        self.layer_targets: dict[str, tuple[str, ...] | None] = {"rain": None, "focus": None}
        self.active_event: HourEvent | AlertEvent | SignalEvent | None = None
        self.event_queue: deque[HourEvent | AlertEvent | SignalEvent] = deque()
        self.frames: dict[str, list[RGB]] = {
            device.id: [(0, 0, 0)] * device.pixel_count for device in config.devices
        }
        self.rain_fields = {
            device.id: RainField(seed=sum(ord(character) for character in device.id) or 1)
            for device in config.devices
        }
        self.outputs = {device.id: create_output(device) for device in config.devices}
        self.frames_rendered = 0
        self.frames_sent = 0
        self.bytes_sent = 0
        self.deadline_misses = 0
        self.frame_times: deque[float] = deque(maxlen=max(120, config.fps * 10))
        self.send_durations: deque[float] = deque(maxlen=max(120, config.fps * 10))
        self.started_at = time.monotonic()
        self.last_frame_at = 0.0
        self.last_error: str | None = None
        self.output_error: str | None = None
        self.last_event: dict[str, Any] | None = None
        self.return_mode_after_events: str | None = None
        self.output_validation: dict[str, dict[str, Any]] = {}
        self.receiver_status: dict[str, dict[str, Any]] = {}
        self.last_phase_key: tuple[int, str] | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        if self.mode == "renderer":
            try:
                self._validate_outputs()
            except ValueError as exc:
                self.mode = "preview"
                self.output_error = str(exc)
                self._record("startup_output_rejected", error=self.output_error)
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="ambient-renderer", daemon=True)
        self.thread.start()
        self.monitor_thread = threading.Thread(
            target=self._monitor_outputs,
            name="ambient-output-monitor",
            daemon=True,
        )
        self.monitor_thread.start()
        self._record("renderer_started", mode=self.mode, fps=self.config.fps)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.monitor_thread:
            self.monitor_thread.join(timeout=3)
        for output in self.outputs.values():
            output.close()
        self._record("renderer_stopped")

    def set_mode(self, mode: str) -> None:
        if mode not in {"renderer", "preview", "music", "off"}:
            raise ValueError("mode must be renderer, preview, music, or off")
        with self.lock:
            current_mode = self.mode
        if mode == "renderer" and current_mode != "renderer":
            self._validate_outputs()
        with self.lock:
            self.mode = mode
            self.return_mode_after_events = None
            if mode in {"music", "off"}:
                self.active_event = None
                self.event_queue.clear()
        self._record("mode_changed", mode=mode)

    def set_layer(self, name: str, enabled: bool, targets: list[str] | tuple[str, ...] | None = None) -> None:
        if name not in self.layers:
            raise ValueError(f"unknown layer {name!r}")
        normalized_targets = self._normalize_targets(targets)
        with self.lock:
            self.layers[name] = bool(enabled)
            self.layer_targets[name] = normalized_targets
            if name == "rain" and not enabled:
                for field in self.rain_fields.values():
                    field.reset()
        self._record(
            "layer_changed",
            layer=name,
            enabled=bool(enabled),
            targets=normalized_targets,
        )

    def set_ambient(self, changes: dict[str, Any]) -> dict[str, Any]:
        now = time.monotonic()
        visual_keys = {"palette", "preset", "speed", "cloud_scale", "saturation", "brightness"}
        if visual_keys.intersection(changes) and "mode" not in changes:
            changes = {**changes, "mode": "manual"}
        with self.lock:
            current = self._ambient_at(now)
            updated = self._validate_ambient(changes, self.ambient_target)
            self.ambient_from = current
            self.ambient_target = updated
            self.ambient_changed_at = now
        self._persist_ambient(updated)
        self._record("ambient_changed", ambient=self._ambient_json(updated))
        return self._ambient_json(updated)

    def set_context(self, changes: dict[str, Any]) -> dict[str, Any]:
        now = time.monotonic()
        with self.lock:
            current_ambient = self._ambient_at(now)
            updated = self._validate_context(changes, self.context)
            updated["updated_at"] = time.time()
            self.context = updated
            weather = str(updated.get("weather") or "unknown").lower()
            raining = weather in {
                "rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"
            }
            was_raining = self.layers["rain"]
            self.layers["rain"] = raining
            if was_raining and not raining:
                for field in self.rain_fields.values():
                    field.reset()
            if self.ambient_target["mode"] == "adaptive":
                self.ambient_from = current_ambient
                self.ambient_changed_at = now
        self._persist_context(updated)
        self._record("house_context_changed", context=updated)
        return dict(updated)

    def trigger_hour(
        self,
        hour: int,
        now: float | None = None,
        take_output: bool = False,
        targets: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if not 0 <= hour <= 23:
            raise ValueError("hour must be between 0 and 23")
        if take_output:
            self._lease_output_for_events()
        event = HourEvent(
            hour=hour,
            started_at=now if now is not None else time.monotonic(),
            targets=self._normalize_targets(targets),
        )
        return self._submit_event(event)

    def _lease_output_for_events(self) -> None:
        with self.lock:
            current_mode = self.mode
        if current_mode in {"music", "off"}:
            raise ValueError(f"renderer is in {current_mode} mode")
        if current_mode == "preview":
            self._validate_outputs()
            with self.lock:
                self.mode = "renderer"
                self.return_mode_after_events = "preview"
            self._record("output_lease_started", return_mode="preview")

    def trigger_alert(
        self,
        color: RGB = (255, 40, 15),
        duration: float = 6.0,
        targets: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        event = AlertEvent(
            started_at=time.monotonic(),
            color=color,
            duration=duration,
            targets=self._normalize_targets(targets),
        )
        return self._submit_event(event)

    def trigger_signal(
        self,
        signal: str,
        duration: float | None = None,
        take_output: bool = False,
        targets: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if duration is not None and not 1.0 <= duration <= 60.0:
            raise ValueError("duration must be between 1 and 60 seconds")
        event = SignalEvent(
            signal=signal,
            started_at=time.monotonic(),
            duration=duration,
            targets=self._normalize_targets(targets),
        )
        if take_output:
            self._lease_output_for_events()
        return self._submit_event(event)

    def _submit_event(self, event: HourEvent | AlertEvent | SignalEvent) -> dict[str, Any]:
        with self.lock:
            if self.mode in {"music", "off"}:
                return {"accepted": False, "reason": f"renderer is in {self.mode} mode"}
            if self.active_event is None:
                self.active_event = event
                disposition = "started"
            elif event.priority >= self.active_event.priority:
                self.last_event = self._event_summary(self.active_event, time.monotonic(), result="interrupted")
                self.active_event = event
                disposition = "replaced_lower_priority"
            else:
                self.event_queue.append(event)
                disposition = "queued"
        self._record("event_submitted", kind=event.kind, disposition=disposition)
        return {"accepted": True, "disposition": disposition, "event": self._event_summary(event, time.monotonic())}

    def cancel_event(self) -> None:
        with self.lock:
            if self.active_event:
                self.last_event = self._event_summary(self.active_event, time.monotonic(), result="cancelled")
            self.active_event = None
            self.event_queue.clear()
            released_to = self.return_mode_after_events
            self.return_mode_after_events = None
            if released_to:
                self.mode = released_to
        self._record("events_cancelled")
        if released_to:
            self._record("output_lease_released", mode=released_to, reason="cancelled")

    def _run(self) -> None:
        interval = 1.0 / self.config.fps
        next_frame = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now < next_frame:
                self.stop_event.wait(next_frame - now)
                continue
            if now - next_frame > interval * 3:
                self.deadline_misses += max(1, int((now - next_frame) / interval))
                next_frame = now
            try:
                self._render(now)
                self.last_error = None
            except Exception as exc:  # keep the service alive and visible on failure
                self.last_error = f"{type(exc).__name__}: {exc}"
                LOGGER.exception("renderer frame failed")
                self._record("frame_error", error=self.last_error)
            next_frame += interval

    def _render(self, now: float) -> None:
        release_after_frame = False
        with self.lock:
            event = self.active_event
            if event and event.is_complete(now):
                self.last_event = self._event_summary(event, now, result="completed")
                self._record("event_completed", kind=event.kind)
                event = self.event_queue.popleft() if self.event_queue else None
                if event:
                    event.started_at = now
                self.active_event = event
                if event is None and self.return_mode_after_events:
                    release_after_frame = True
            layers = dict(self.layers)
            layer_targets = dict(self.layer_targets)
            mode = self.mode
            ambient = self._ambient_at(now)
            context = dict(self.context)

        if event:
            phase = event.phase(now)[0]
            phase_key = (id(event), phase)
            if phase_key != self.last_phase_key:
                self.last_phase_key = phase_key
                self._record(
                    "event_phase_started",
                    kind=event.kind,
                    phase=phase,
                    frame=self.frames_rendered + 1,
                    elapsed=round(now - event.started_at, 4),
                )
        else:
            self.last_phase_key = None

        for device in self.config.devices:
            rain_lanes = self._target_lanes(device, layer_targets["rain"])
            focus_lanes = self._target_lanes(device, layer_targets["focus"])
            frame = render_base(
                device,
                now,
                ambient["palette"],
                ambient["speed"],
                rain=False,
                focus=layers["focus"] and bool(focus_lanes),
                rain_lanes=rain_lanes,
                focus_lanes=focus_lanes,
                cloud_scale=ambient["cloud_scale"],
                saturation=ambient["saturation"],
                ambient_brightness=ambient["brightness"],
            )
            if layers["rain"] and rain_lanes:
                frame = self.rain_fields[device.id].render(
                    frame,
                    device,
                    now,
                    rain_lanes,
                    self._rain_intensity(context),
                )
            if isinstance(event, HourEvent):
                target_lanes = self._target_lanes(device, event.targets)
                if target_lanes:
                    frame = render_hour(frame, device, event, now, target_lanes)
            elif isinstance(event, AlertEvent):
                target_lanes = self._target_lanes(device, event.targets)
                if target_lanes:
                    frame = render_alert(frame, device, event, now, target_lanes)
            elif isinstance(event, SignalEvent):
                target_lanes = self._target_lanes(device, event.targets)
                if target_lanes:
                    frame = render_signal(frame, device, event, now, target_lanes)
            self.frames[device.id] = frame
            if mode == "renderer":
                send_started = time.monotonic()
                sent = self.outputs[device.id].send(frame)
                self.send_durations.append(time.monotonic() - send_started)
                self.bytes_sent += int(sent or 0)
                self.frames_sent += 1
        self.frames_rendered += 1
        self.last_frame_at = now
        self.frame_times.append(now)
        if release_after_frame:
            with self.lock:
                released_to = self.return_mode_after_events
                self.return_mode_after_events = None
                if released_to:
                    self.mode = released_to
            self._record("output_lease_released", mode=released_to)

    def _event_summary(
        self,
        event: HourEvent | AlertEvent | SignalEvent,
        now: float,
        result: str | None = None,
    ) -> dict[str, Any]:
        phase, progress = event.phase(now)
        summary: dict[str, Any] = {
            "kind": event.kind,
            "priority": event.priority,
            "phase": phase,
            "progress": round(progress, 4),
        }
        if isinstance(event, HourEvent):
            summary["hour"] = event.hour
            summary["count"] = event.count
        elif isinstance(event, SignalEvent):
            summary["signal"] = event.signal
            summary["duration"] = event.duration
        summary["targets"] = list(event.targets) if event.targets else "all"
        if result:
            summary["result"] = result
        return summary

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        with self.lock:
            recent_times = [stamp for stamp in self.frame_times if now - stamp <= 5.0]
            recent_fps = 0.0
            intervals: list[float] = []
            if len(recent_times) > 1:
                intervals = [
                    current - previous
                    for previous, current in zip(recent_times, recent_times[1:])
                ]
                recent_fps = (len(recent_times) - 1) / max(
                    0.001, recent_times[-1] - recent_times[0]
                )
            sorted_intervals = sorted(intervals)
            p95_interval = (
                sorted_intervals[min(len(sorted_intervals) - 1, int(len(sorted_intervals) * 0.95))]
                if sorted_intervals
                else 0.0
            )
            sorted_sends = sorted(self.send_durations)
            p95_send = (
                sorted_sends[min(len(sorted_sends) - 1, int(len(sorted_sends) * 0.95))]
                if sorted_sends
                else 0.0
            )
            event = self._event_summary(self.active_event, now) if self.active_event else None
            queue = [self._event_summary(item, now) for item in self.event_queue]
            health_issues: list[str] = []
            if self.mode == "renderer":
                for device in self.config.devices:
                    receiver = self.receiver_status.get(device.id)
                    if not receiver:
                        continue
                    if not receiver.get("ok"):
                        health_issues.append(f"{device.name} receiver probe failed")
                        continue
                    if time.time() - float(receiver.get("checked_at", 0)) > 15:
                        health_issues.append(f"{device.name} receiver telemetry is stale")
                    receiver_fps = float(receiver.get("fps") or 0)
                    if receiver_fps < self.config.fps * 0.8:
                        health_issues.append(
                            f"{device.name} displays {receiver_fps:g} FPS; target is {self.config.fps}"
                        )
                    expected_mode = "UDP" if device.transport == "udp_realtime" else "DDP"
                    if receiver.get("live_mode") != expected_mode:
                        health_issues.append(
                            f"{device.name} reports {receiver.get('live_mode') or 'no'} realtime owner; "
                            f"expected {expected_mode}"
                        )
            active_ambient = self._ambient_at(now)
            return {
                "ok": (
                    self.last_error is None
                    and self.output_error is None
                    and not health_issues
                ),
                "health_issues": health_issues,
                "mode": self.mode,
                "fps_target": self.config.fps,
                "fps_average": round(self.frames_rendered / max(0.001, now - self.started_at), 2),
                "fps_recent": round(recent_fps, 2),
                "frame_interval_p95_ms": round(p95_interval * 1000, 2),
                "frame_interval_max_ms": round(max(intervals, default=0.0) * 1000, 2),
                "send_duration_p95_ms": round(p95_send * 1000, 3),
                "deadline_misses": self.deadline_misses,
                "frames_rendered": self.frames_rendered,
                "frames_sent": self.frames_sent,
                "bytes_sent": self.bytes_sent,
                "output_lease_return_mode": self.return_mode_after_events,
                "output_validation": dict(self.output_validation),
                "receiver_status": dict(self.receiver_status),
                "layers": dict(self.layers),
                "ambient": {
                    **self._ambient_json(active_ambient),
                    "transitioning": now - self.ambient_changed_at < self.ambient_transition,
                },
                "context": dict(self.context),
                "rain_simulation": {
                    device_id: field.status() for device_id, field in self.rain_fields.items()
                },
                "layer_targets": {
                    name: list(targets) if targets else "all"
                    for name, targets in self.layer_targets.items()
                },
                "active_event": event,
                "queued_events": queue,
                "last_event": self.last_event,
                "last_error": self.last_error,
                "output_error": self.output_error,
                "devices": [
                    {
                        "id": device.id,
                        "name": device.name,
                        "host": device.host,
                        "pixel_count": device.pixel_count,
                        "transport": device.transport,
                        "transport_port": (
                            device.realtime_port
                            if device.transport == "udp_realtime"
                            else device.ddp_port
                        ),
                        "lanes": [asdict(lane) for lane in device.lanes],
                    }
                    for device in self.config.devices
                ],
            }

    @staticmethod
    def _validate_ambient(
        changes: dict[str, Any],
        base: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {"mode", "palette", "speed", "cloud_scale", "saturation", "brightness", "preset"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError("unknown ambient settings: " + ", ".join(sorted(unknown)))
        result = dict(base or {})
        if "mode" in changes:
            if changes["mode"] not in {"adaptive", "manual"}:
                raise ValueError("ambient mode must be adaptive or manual")
            result["mode"] = changes["mode"]
        if "mode" not in result:
            result["mode"] = "adaptive"
        preset = changes.get("preset")
        if preset is not None:
            if preset not in AMBIENT_PRESETS:
                raise ValueError("unknown ambient preset")
            result["palette"] = tuple(parse_hex(color) for color in AMBIENT_PRESETS[preset])
        if "palette" in changes:
            palette = changes["palette"]
            if not isinstance(palette, (list, tuple)) or not 2 <= len(palette) <= 8:
                raise ValueError("palette must contain between 2 and 8 colors")
            result["palette"] = tuple(
                color if isinstance(color, tuple) else parse_hex(str(color))
                for color in palette
            )
        ranges = {
            "speed": (0.001, 0.08),
            "cloud_scale": (0.3, 3.0),
            "saturation": (0.0, 2.0),
            "brightness": (0.05, 1.0),
        }
        for name, (low, high) in ranges.items():
            if name in changes:
                result[name] = float(changes[name])
            if name not in result:
                raise ValueError(f"ambient setting {name!r} is required")
            if not low <= float(result[name]) <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
        if "palette" not in result:
            raise ValueError("ambient palette is required")
        return result

    def _ambient_at(self, now: float) -> dict[str, Any]:
        second = (
            {**adaptive_ambient(time.time(), self.context), "mode": "adaptive"}
            if self.ambient_target["mode"] == "adaptive"
            else dict(self.ambient_target)
        )
        progress = smoothstep(
            0.0,
            self.ambient_transition,
            now - self.ambient_changed_at,
        )
        if progress >= 1.0:
            return second
        first = self.ambient_from
        count = max(len(first["palette"]), len(second["palette"]))
        palette = tuple(
            mix(
                palette_color(first["palette"], index / count),
                palette_color(second["palette"], index / count),
                progress,
            )
            for index in range(count)
        )
        blended = {
            "mode": second["mode"],
            "palette": palette,
            **{
                name: float(first[name]) + (float(second[name]) - float(first[name])) * progress
                for name in ("speed", "cloud_scale", "saturation", "brightness")
            },
        }
        for name in ("mood", "time_mood"):
            if name in second:
                blended[name] = second[name]
        return blended

    @staticmethod
    def _ambient_json(ambient: dict[str, Any]) -> dict[str, Any]:
        result = {
            "mode": ambient.get("mode", "adaptive"),
            "palette": ["#%02x%02x%02x" % color for color in ambient["palette"]],
            "speed": round(float(ambient["speed"]), 4),
            "cloud_scale": round(float(ambient["cloud_scale"]), 3),
            "saturation": round(float(ambient["saturation"]), 3),
            "brightness": round(float(ambient["brightness"]), 3),
        }
        for name in ("mood", "time_mood"):
            if name in ambient:
                result[name] = ambient[name]
        return result

    def _persist_ambient(self, ambient: dict[str, Any]) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._ambient_json(ambient), indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.settings_path)
        except OSError as exc:
            raise ValueError(f"could not save ambient settings: {exc}") from exc

    @staticmethod
    def _validate_context(
        changes: dict[str, Any],
        base: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "weather", "temperature", "temperature_unit", "humidity",
            "cloud_coverage", "wind_speed", "updated_at",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError("unknown house context: " + ", ".join(sorted(unknown)))
        result = dict(base or {})
        if "weather" in changes:
            result["weather"] = str(changes["weather"] or "unknown").strip().lower()
        if "temperature_unit" in changes:
            result["temperature_unit"] = str(changes["temperature_unit"] or "°F")[:8]
        ranges = {
            "temperature": (-100.0, 180.0),
            "humidity": (0.0, 100.0),
            "cloud_coverage": (0.0, 100.0),
            "wind_speed": (0.0, 250.0),
        }
        for name, (low, high) in ranges.items():
            if name in changes:
                value = changes[name]
                result[name] = None if value is None or value == "" else float(value)
            if result.get(name) is not None and not low <= float(result[name]) <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
        if "updated_at" in changes:
            result["updated_at"] = changes["updated_at"]
        return result

    def _persist_context(self, context: dict[str, Any]) -> None:
        try:
            self.context_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.context_path.with_suffix(self.context_path.suffix + ".tmp")
            temporary.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.context_path)
        except OSError as exc:
            raise ValueError(f"could not save house context: {exc}") from exc

    @staticmethod
    def _rain_intensity(context: dict[str, Any]) -> float:
        weather = str(context.get("weather") or "unknown").lower()
        intensity = {
            "pouring": 1.8,
            "lightning-rainy": 1.7,
            "hail": 1.55,
            "rainy": 1.0,
            "snowy-rainy": 0.8,
        }.get(weather, 0.75)
        humidity = context.get("humidity")
        if humidity is not None:
            intensity += max(0.0, (float(humidity) - 75.0) / 100.0)
        return intensity

    def _monitor_outputs(self) -> None:
        """Probe the receiver outside the render loop so diagnostics cannot cause jitter."""
        while not self.stop_event.is_set():
            checked_at = time.time()
            for device in self.config.devices:
                try:
                    with urllib.request.urlopen(
                        f"http://{device.host}/json/info", timeout=2.0
                    ) as response:
                        info = json.load(response)
                    leds = info.get("leds", {})
                    reading = {
                        "ok": True,
                        "checked_at": checked_at,
                        "fps": leds.get("fps"),
                        "live": info.get("live"),
                        "live_mode": info.get("lm"),
                        "source_ip": info.get("lip"),
                        "version": info.get("ver"),
                    }
                except Exception as exc:
                    reading = {
                        "ok": False,
                        "checked_at": checked_at,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                with self.lock:
                    self.receiver_status[device.id] = reading
            self.stop_event.wait(5.0)

    def _normalize_targets(
        self,
        targets: list[str] | tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if not targets:
            return None
        known = {
            item
            for device in self.config.devices
            for item in (device.id, *(lane.id for lane in device.lanes))
        }
        normalized = tuple(dict.fromkeys(str(target) for target in targets))
        unknown = sorted(set(normalized) - known)
        if unknown:
            raise ValueError("unknown targets: " + ", ".join(unknown))
        return normalized

    @staticmethod
    def _target_lanes(device: Any, targets: tuple[str, ...] | None) -> tuple[Any, ...]:
        if targets is None or device.id in targets:
            return device.lanes
        return tuple(lane for lane in device.lanes if lane.id in targets)

    def _validate_outputs(self) -> None:
        results: dict[str, dict[str, Any]] = {}
        failures = []
        for device in self.config.devices:
            try:
                with urllib.request.urlopen(f"http://{device.host}/json/info", timeout=2.0) as response:
                    info = json.load(response)
                reported_pixels = int(info.get("leds", {}).get("count", 0))
                if reported_pixels < device.pixel_count:
                    raise ValueError(
                        f"reports {reported_pixels} pixels; renderer requires {device.pixel_count}"
                    )
                results[device.id] = {
                    "ok": True,
                    "reported_pixels": reported_pixels,
                    "version": info.get("ver"),
                }
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                results[device.id] = {"ok": False, "error": message}
                failures.append(f"{device.name}: {message}")
        with self.lock:
            self.output_validation = results
        if failures:
            self.output_error = "WLED preflight failed: " + "; ".join(failures)
            raise ValueError(self.output_error)
        self.output_error = None
        self._record("outputs_validated", devices=list(results))

    def frame_snapshot(self) -> dict[str, list[str]]:
        with self.lock:
            return {
                device_id: ["#%02x%02x%02x" % pixel for pixel in frame]
                for device_id, frame in self.frames.items()
            }

    def _record(self, event: str, **details: Any) -> None:
        record = {"timestamp": time.time(), "event": event, **details}
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            LOGGER.exception("could not write renderer event log")
