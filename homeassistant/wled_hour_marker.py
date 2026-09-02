#!/usr/bin/env python3
"""Sweep WLED dark, toll the hour with cumulative top-down dots, then restore it."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import time
from typing import Any

from wled_client import WLEDError, get_info, get_state, realtime_active, request_json, restore_state


BLACK = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
FALLBACK_DOT_COLOR = [255, 160, 0]


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def hour_count(hour: int) -> int:
    return hour % 12 or 12


def configured_outputs(config: dict[str, Any], led_count: int) -> list[tuple[int, int]]:
    """Return the absolute LED range of each configured physical WLED output."""
    buses = config.get("hw", {}).get("led", {}).get("ins", [])
    outputs = []
    for bus in buses:
        start = int(bus.get("start", 0))
        length = int(bus.get("len", 0))
        if length > 0:
            outputs.append((start, start + length))
    if not outputs and led_count > 0:
        outputs.append((0, led_count))
    if not outputs:
        raise ValueError("WLED does not report any configured LED outputs")
    return outputs


def toll_positions(
    led_count: int,
    hour: int,
    top_offset: int,
    dot_gap: int,
    top_at_high_index: bool,
) -> list[int]:
    """Return one LED index per toll, beginning just below the physical top."""
    if led_count < 1 or top_offset < 0 or dot_gap < 0:
        raise ValueError("LED count must be positive; top offset and dot gap cannot be negative")

    stride = dot_gap + 1
    if top_at_high_index:
        positions = [led_count - 1 - top_offset - number * stride for number in range(hour_count(hour))]
    else:
        positions = [top_offset + number * stride for number in range(hour_count(hour))]
    if not positions or min(positions) < 0 or max(positions) >= led_count:
        raise ValueError("hour toll does not fit on this LED strip with the configured offset and gap")
    return positions


def dot_color(state: dict[str, Any]) -> list[int]:
    source = next((segment for segment in state.get("seg", []) if segment.get("on", True)), {})
    colors = source.get("col", [])
    primary = colors[0] if colors and isinstance(colors[0], list) else FALLBACK_DOT_COLOR
    if len(primary) < 3 or not any(primary[:3]):
        primary = FALLBACK_DOT_COLOR
    return [int(channel) for channel in primary[:3]]


def stop_unused_segments(segments: list[dict[str, Any]], max_segments: int) -> list[dict[str, Any]]:
    if len(segments) > max_segments:
        raise ValueError(
            f"hour display needs {len(segments)} segments but this WLED device supports {max_segments}"
        )
    return segments + [{"id": segment_id, "stop": 0} for segment_id in range(len(segments), max_segments)]


def blackout_payload(args: argparse.Namespace) -> dict[str, Any]:
    segments = [{
        "id": segment_id,
        "start": start,
        "stop": stop,
        "on": True,
        "frz": False,
        "bri": 255,
        "fx": 0,
        "pal": 0,
        "col": copy.deepcopy(BLACK),
    } for segment_id, (start, stop) in enumerate(args.outputs)]
    return {
        "on": True,
        "transition": args.blackout_transition,
        "seg": stop_unused_segments(segments, args.max_segments),
    }


def toll_payload(state: dict[str, Any], args: argparse.Namespace, count: int) -> dict[str, Any]:
    color = dot_color(state)
    segments = []
    for segment_id, (start, stop) in enumerate(args.outputs):
        length = stop - start
        positions = toll_positions(
            length, args.hour, args.top_offset, args.dot_gap, args.top_at_high_index
        )[:count]
        pixels: list[Any] = [0, length, [0, 0, 0]]
        for position in positions:
            pixels.extend([position, color])
        segments.append({
            "id": segment_id,
            "start": start,
            "stop": stop,
            "on": True,
            "frz": True,
            "bri": 255,
            "fx": 0,
            "pal": 0,
            "col": copy.deepcopy(BLACK),
            "i": pixels,
        })
    return {
        "on": True,
        "transition": 0,
        "seg": stop_unused_segments(segments, args.max_segments),
    }


def capture_rendered_pixels(wled_url: str, expected_count: int) -> list[list[int]]:
    """Capture the RGB framebuffer WLED is currently rendering."""
    try:
        import websocket
    except ImportError as exc:
        raise WLEDError("the Python websocket-client package is required to capture the current display") from exc

    ws_url = wled_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    connection = websocket.create_connection(ws_url, timeout=1)
    try:
        connection.send(json.dumps({"lv": True}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                frame = connection.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(frame, bytes) or not frame.startswith(b"L\x01"):
                continue
            pixels = [list(frame[index:index + 3]) for index in range(2, len(frame), 3)]
            if len(pixels) >= expected_count:
                return pixels[:expected_count]
    finally:
        try:
            connection.send(json.dumps({"lv": False}))
        finally:
            connection.close()
    raise WLEDError("WLED did not provide a complete rendered framebuffer within 5 seconds")


def encode_pixel_runs(pixels: list[list[int]]) -> list[Any]:
    """Encode framebuffer colors using WLED's compact individual-pixel range format."""
    encoded: list[Any] = []
    start = 0
    while start < len(pixels):
        stop = start + 1
        while stop < len(pixels) and pixels[stop] == pixels[start]:
            stop += 1
        encoded.extend([start, stop, pixels[start]])
        start = stop
    return encoded


def frozen_frame_payload(args: argparse.Namespace, pixels: list[list[int]]) -> dict[str, Any]:
    """Freeze each physical output on the exact RGB frame visible before the sweep."""
    segments = []
    for segment_id, (start, stop) in enumerate(args.outputs):
        segments.append({
            "id": segment_id,
            "start": start,
            "stop": stop,
            "on": True,
            "frz": True,
            "bri": 255,
            "fx": 0,
            "pal": 0,
            "col": copy.deepcopy(BLACK),
            "i": encode_pixel_runs(pixels[start:stop]),
        })
    return {
        "on": True,
        "transition": 0,
        "seg": stop_unused_segments(segments, args.max_segments),
    }


def wipe_step_payload(
    outputs: list[tuple[int, int]],
    step: int,
    pixels_per_step: int,
    top_at_high_index: bool,
) -> dict[str, Any]:
    """Create one simultaneous blackening step for every physical output."""
    segments = []
    for segment_id, (start, stop) in enumerate(outputs):
        length = stop - start
        if top_at_high_index:
            local_stop = max(0, length - step * pixels_per_step)
            local_start = max(0, local_stop - pixels_per_step)
        else:
            local_start = min(length, step * pixels_per_step)
            local_stop = min(length, local_start + pixels_per_step)
        if local_start < local_stop:
            segments.append({"id": segment_id, "i": [local_start, local_stop, [0, 0, 0]]})
    return {"transition": 0, "seg": segments}


def run_pixel_sweep(
    args: argparse.Namespace,
    ownership_payload: dict[str, Any],
) -> float:
    """Blacken each output from its physical top to bottom over a fixed duration."""
    max_length = max(stop - start for start, stop in args.outputs)
    steps = (max_length + args.sweep_pixels_per_step - 1) // args.sweep_pixels_per_step
    started = time.monotonic()
    for step in range(steps):
        if step % 5 == 0 and not still_owns_display(args, ownership_payload, "the sweep"):
            return -1.0
        request_json(
            args.wled_url,
            "/json/state",
            wipe_step_payload(args.outputs, step, args.sweep_pixels_per_step, args.top_at_high_index),
        )
        target = started + (step + 1) * args.sweep_seconds / steps
        time.sleep(max(0, target - time.monotonic()))
    return time.monotonic() - started


def display_matches(state: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Return true only while WLED still shows the state this controller posted."""
    actual = {int(segment.get("id", 0)): segment for segment in state.get("seg", []) if segment.get("stop", 0)}
    expected = {int(segment.get("id", 0)): segment for segment in payload.get("seg", []) if segment.get("stop", 0)}
    if set(actual) != set(expected):
        return False
    for segment_id, wanted in expected.items():
        shown = actual[segment_id]
        for key in ("start", "stop", "fx", "frz", "col"):
            if key in wanted and shown.get(key) != wanted[key]:
                return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wled-url", default=os.getenv("WLED_URL", "http://wled.local"))
    parser.add_argument("--hour", type=int, default=dt.datetime.now().hour)
    parser.add_argument("--led-count", type=int, default=int(os.getenv("WLED_LED_COUNT", "0")))
    parser.add_argument("--top-offset", type=int, default=int(os.getenv("WLED_TOP_OFFSET", "5")))
    parser.add_argument("--dot-gap", type=int, default=int(os.getenv("WLED_DOT_GAP", "3")))
    parser.add_argument("--top-at-high-index", action=argparse.BooleanOptionalAction,
                        default=env_bool("WLED_TOP_AT_HIGH_INDEX", True))
    parser.add_argument("--sweep-seconds", type=float, default=8.0)
    parser.add_argument("--sweep-pixels-per-step", type=int, default=2)
    parser.add_argument("--blackout-seconds", type=float, default=0.75)
    parser.add_argument("--toll-seconds", type=float, default=1.0)
    parser.add_argument("--hold-seconds", type=float, default=5.0)
    parser.add_argument("--blackout-transition", type=int, default=0)
    parser.add_argument("--restore-transition", type=int, default=30, help="Restoration fade in 100 ms units")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def still_owns_display(args: argparse.Namespace, payload: dict[str, Any], phase: str) -> bool:
    if display_matches(get_state(args.wled_url), payload):
        return True
    print(f"Hourly display yielded to another WLED change during {phase}.")
    return False


def main() -> int:
    args = parse_args()
    try:
        state = get_state(args.wled_url)
        info = get_info(args.wled_url)
        config = request_json(args.wled_url, "/json/cfg")
        if realtime_active(info):
            print("WLED realtime input is active; hourly display skipped.")
            return 0
        if not state.get("on") or not state.get("seg"):
            print("WLED is off or has no active segment; hourly display skipped.")
            return 0
        if args.led_count <= 0:
            args.led_count = int(info.get("leds", {}).get("count", 0))
        args.max_segments = int(info.get("leds", {}).get("maxseg", 32))
        args.outputs = configured_outputs(config, args.led_count)

        blackout = blackout_payload(args)
        tolls = [toll_payload(state, args, count) for count in range(1, hour_count(args.hour) + 1)]
        if args.dry_run:
            print(json.dumps({
                "outputs": args.outputs,
                "sweep": {
                    "seconds": args.sweep_seconds,
                    "pixels_per_step": args.sweep_pixels_per_step,
                    "direction": "high-to-low" if args.top_at_high_index else "low-to-high",
                },
                "blackout": blackout,
                "tolls": tolls,
                "restore": state,
            }, indent=2))
            return 0

        if args.sweep_pixels_per_step < 1 or args.sweep_seconds <= 0:
            raise ValueError("sweep duration and pixels per step must both be positive")

        rendered_pixels = capture_rendered_pixels(args.wled_url, args.led_count)
        frozen_frame = frozen_frame_payload(args, rendered_pixels)

        changed = False
        owns_display = False
        try:
            request_json(args.wled_url, "/json/state", frozen_frame)
            changed = True
            owns_display = True
            sweep_elapsed = run_pixel_sweep(args, frozen_frame)
            if sweep_elapsed < 0:
                owns_display = False
                return 0
            print(
                f"Hourly sweep reached full darkness on every output after {sweep_elapsed:.2f} seconds.",
                flush=True,
            )
            if not still_owns_display(args, frozen_frame, "the sweep"):
                owns_display = False
                return 0

            request_json(args.wled_url, "/json/state", blackout)
            time.sleep(args.blackout_seconds)
            if not still_owns_display(args, blackout, "the blackout"):
                owns_display = False
                return 0

            for number, toll in enumerate(tolls, start=1):
                request_json(args.wled_url, "/json/state", toll)
                if number < len(tolls):
                    time.sleep(args.toll_seconds)
                    if not still_owns_display(args, toll, f"toll {number}"):
                        owns_display = False
                        return 0

            time.sleep(args.hold_seconds)
            if not still_owns_display(args, tolls[-1], "the completed hour"):
                owns_display = False
                return 0
        finally:
            if changed and owns_display:
                restore = copy.deepcopy(state)
                restore["transition"] = args.restore_transition
                restore_state(args.wled_url, restore)
        return 0
    except (ValueError, WLEDError) as exc:
        print(f"Hourly display failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
