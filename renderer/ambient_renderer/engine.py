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


LOGGER = logging.getLogger(__name__)


class RendererEngine:
    def __init__(self, config: RendererConfig) -> None:
        self.config = config
        self.palette = tuple(parse_hex(color) for color in config.palette)
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
        self.log_path = Path(config.log_path)

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
