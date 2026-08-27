"""Configuration models and validation for the ambient renderer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LaneConfig:
    id: str
    name: str
    start: int
    length: int
    top_at_high_index: bool = True


@dataclass(frozen=True)
class DeviceConfig:
    id: str
    name: str
    host: str
    pixel_count: int
    lanes: tuple[LaneConfig, ...]
    transport: str = "udp_realtime"
    realtime_port: int = 21324
    realtime_timeout: int = 2
    ddp_port: int = 4048
    brightness: float = 0.5


@dataclass(frozen=True)
class RendererConfig:
    fps: int
    output_enabled: bool
    palette: tuple[str, ...]
    palette_speed: float
    devices: tuple[DeviceConfig, ...]
    log_path: str
    settings_path: str = "/data/ambient-settings.json"
    cloud_scale: float = 1.0
    saturation: float = 1.0
    ambient_brightness: float = 1.0
    music_companion_url: str = "http://Michaels-Laptop.local:8091"


def _required(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise ValueError(f"{context} is missing required field {key!r}")
    return data[key]


def load_config(path: str | Path) -> RendererConfig:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    fps = int(data.get("fps", 30))
    if not 10 <= fps <= 60:
        raise ValueError("fps must be between 10 and 60")

    devices = []
    seen_device_ids: set[str] = set()
    for device_data in _required(data, "devices", "renderer config"):
        device_id = str(_required(device_data, "id", "device"))
        if device_id in seen_device_ids:
            raise ValueError(f"duplicate device id {device_id!r}")
        seen_device_ids.add(device_id)
        pixel_count = int(_required(device_data, "pixel_count", f"device {device_id}"))
        lanes = []
        seen_lane_ids: set[str] = set()
        occupied: set[int] = set()
        for lane_data in _required(device_data, "lanes", f"device {device_id}"):
            lane = LaneConfig(
                id=str(_required(lane_data, "id", f"device {device_id} lane")),
                name=str(lane_data.get("name", lane_data["id"])),
                start=int(_required(lane_data, "start", f"device {device_id} lane")),
                length=int(_required(lane_data, "length", f"device {device_id} lane")),
                top_at_high_index=bool(lane_data.get("top_at_high_index", True)),
            )
            if lane.id in seen_lane_ids:
                raise ValueError(f"duplicate lane id {lane.id!r} on device {device_id!r}")
            seen_lane_ids.add(lane.id)
            if lane.start < 0 or lane.length < 1 or lane.start + lane.length > pixel_count:
                raise ValueError(f"lane {lane.id!r} is outside device {device_id!r}")
            indexes = set(range(lane.start, lane.start + lane.length))
            if occupied & indexes:
                raise ValueError(f"lane {lane.id!r} overlaps another lane on device {device_id!r}")
            occupied.update(indexes)
            lanes.append(lane)
        brightness = float(device_data.get("brightness", 0.5))
        if not 0.0 <= brightness <= 1.0:
            raise ValueError(f"brightness for device {device_id!r} must be between 0 and 1")
        transport = str(device_data.get("transport", "udp_realtime"))
        if transport not in {"udp_realtime", "ddp"}:
            raise ValueError(
                f"transport for device {device_id!r} must be 'udp_realtime' or 'ddp'"
            )
        realtime_timeout = int(device_data.get("realtime_timeout", 2))
        if not 1 <= realtime_timeout <= 255:
            raise ValueError(
                f"realtime_timeout for device {device_id!r} must be between 1 and 255"
            )
        devices.append(DeviceConfig(
            id=device_id,
            name=str(device_data.get("name", device_id)),
            host=str(_required(device_data, "host", f"device {device_id}")),
            pixel_count=pixel_count,
            lanes=tuple(lanes),
            transport=transport,
            realtime_port=int(device_data.get("realtime_port", 21324)),
            realtime_timeout=realtime_timeout,
            ddp_port=int(device_data.get("ddp_port", 4048)),
            brightness=brightness,
        ))

    if not devices:
        raise ValueError("renderer config must contain at least one device")

    palette = tuple(data.get("palette", ["#07152f", "#0d7781", "#a7c4c7"]))
    if len(palette) < 2:
        raise ValueError("palette must contain at least two colors")
    palette_speed = float(data.get("palette_speed", 0.018))
    cloud_scale = float(data.get("cloud_scale", 1.0))
    saturation = float(data.get("saturation", 1.0))
    ambient_brightness = float(data.get("ambient_brightness", 1.0))
    for name, value, low, high in (
        ("palette_speed", palette_speed, 0.001, 0.08),
        ("cloud_scale", cloud_scale, 0.3, 3.0),
        ("saturation", saturation, 0.0, 2.0),
        ("ambient_brightness", ambient_brightness, 0.05, 1.0),
    ):
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
    return RendererConfig(
        fps=fps,
        output_enabled=bool(data.get("output_enabled", False)),
        palette=palette,
        palette_speed=palette_speed,
        devices=tuple(devices),
        log_path=str(data.get("log_path", "/tmp/ambient-renderer.jsonl")),
        settings_path=str(data.get("settings_path", "/data/ambient-settings.json")),
        cloud_scale=cloud_scale,
        saturation=saturation,
        ambient_brightness=ambient_brightness,
        music_companion_url=str(
            data.get("music_companion_url", "http://Michaels-Laptop.local:8091")
        ).rstrip("/"),
    )
