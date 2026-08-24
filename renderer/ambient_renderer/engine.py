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

from .color import RGB, parse_hex
from .config import RendererConfig
from .ddp import DDPOutput
from .effects import AlertEvent, HourEvent, render_alert, render_base, render_hour


LOGGER = logging.getLogger(__name__)


class RendererEngine:
    def __init__(self, config: RendererConfig) -> None:
        self.config = config
        self.palette = tuple(parse_hex(color) for color in config.palette)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.mode = "renderer" if config.output_enabled else "preview"
        self.layers = {"rain": False, "focus": False}
        self.layer_targets: dict[str, tuple[str, ...] | None] = {"rain": None, "focus": None}
        self.active_event: HourEvent | AlertEvent | None = None
        self.event_queue: deque[HourEvent | AlertEvent] = deque()
        self.frames: dict[str, list[RGB]] = {
            device.id: [(0, 0, 0)] * device.pixel_count for device in config.devices
        }
        self.outputs = {device.id: DDPOutput(device.host, device.ddp_port) for device in config.devices}
        self.frames_rendered = 0
        self.frames_sent = 0
        self.started_at = time.monotonic()
        self.last_frame_at = 0.0
        self.last_error: str | None = None
        self.last_event: dict[str, Any] | None = None
        self.return_mode_after_events: str | None = None
        self.output_validation: dict[str, dict[str, Any]] = {}
        self.log_path = Path(config.log_path)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="ambient-renderer", daemon=True)
        self.thread.start()
        self._record("renderer_started", mode=self.mode, fps=self.config.fps)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
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
        self._record(
            "layer_changed",
            layer=name,
            enabled=bool(enabled),
            targets=normalized_targets,
        )

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

    def _submit_event(self, event: HourEvent | AlertEvent) -> dict[str, Any]:
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

        for device in self.config.devices:
            rain_lanes = self._target_lanes(device, layer_targets["rain"])
            focus_lanes = self._target_lanes(device, layer_targets["focus"])
            frame = render_base(
                device,
                now,
                self.palette,
                self.config.palette_speed,
                rain=layers["rain"] and bool(rain_lanes),
                focus=layers["focus"] and bool(focus_lanes),
                rain_lanes=rain_lanes,
                focus_lanes=focus_lanes,
            )
            if isinstance(event, HourEvent):
                target_lanes = self._target_lanes(device, event.targets)
                if target_lanes:
                    frame = render_hour(frame, device, event, now, target_lanes)
            elif isinstance(event, AlertEvent):
                target_lanes = self._target_lanes(device, event.targets)
                if target_lanes:
                    frame = render_alert(frame, device, event, now, target_lanes)
            self.frames[device.id] = frame
            if mode == "renderer":
                self.outputs[device.id].send(frame)
                self.frames_sent += 1
        self.frames_rendered += 1
        self.last_frame_at = now
        if release_after_frame:
            with self.lock:
                released_to = self.return_mode_after_events
                self.return_mode_after_events = None
                if released_to:
                    self.mode = released_to
            self._record("output_lease_released", mode=released_to)

    def _event_summary(
        self,
        event: HourEvent | AlertEvent,
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
        summary["targets"] = list(event.targets) if event.targets else "all"
        if result:
            summary["result"] = result
        return summary

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        with self.lock:
            event = self._event_summary(self.active_event, now) if self.active_event else None
            queue = [self._event_summary(item, now) for item in self.event_queue]
            return {
                "ok": self.last_error is None,
                "mode": self.mode,
                "fps_target": self.config.fps,
                "fps_average": round(self.frames_rendered / max(0.001, now - self.started_at), 2),
                "frames_rendered": self.frames_rendered,
                "frames_sent": self.frames_sent,
                "output_lease_return_mode": self.return_mode_after_events,
                "output_validation": dict(self.output_validation),
                "layers": dict(self.layers),
                "layer_targets": {
                    name: list(targets) if targets else "all"
                    for name, targets in self.layer_targets.items()
                },
                "active_event": event,
                "queued_events": queue,
                "last_event": self.last_event,
                "last_error": self.last_error,
                "devices": [
                    {
                        "id": device.id,
                        "name": device.name,
                        "host": device.host,
                        "pixel_count": device.pixel_count,
                        "lanes": [asdict(lane) for lane in device.lanes],
                    }
                    for device in self.config.devices
                ],
            }

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
            raise ValueError("WLED preflight failed: " + "; ".join(failures))
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
