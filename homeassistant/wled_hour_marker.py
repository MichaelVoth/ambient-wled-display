#!/usr/bin/env python3
"""Sweep WLED dark, toll the hour with cumulative top-down dots, then restore it."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import time
from typing import Any, Optional

from wled_client import WLEDError, get_info, get_state, realtime_active, request_json, restore_state


BLACK = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
FALLBACK_DOT_COLOR = [255, 160, 0]


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def hour_count(hour: int) -> int:
    return hour % 12 or 12


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
    return segments + [
        {"id": segment_id, "stop": 0} for segment_id in range(len(segments), max_segments)
    ]


def static_segments(
    led_count: int,
    lit_positions: list[int],
    color: list[int],
    reserved_positions: Optional[list[int]] = None,
) -> list[dict[str, Any]]:
    """Build an explicit all-black strip with only the requested single LEDs illuminated."""
    lit = set(lit_positions)
    positions = sorted(set(reserved_positions if reserved_positions is not None else lit_positions))
    if not lit.issubset(positions):
        raise ValueError("every illuminated toll position must be reserved in the segment layout")
    if any(position < 0 or position >= led_count for position in positions):
        raise ValueError("a toll position is outside the LED strip")

    segments: list[dict[str, Any]] = []
    cursor = 0

    def append_segment(start: int, stop: int, segment_color: list[list[int]]) -> None:
        if start >= stop:
            return
        segments.append({
            "id": len(segments),
            "start": start,
            "stop": stop,
            "on": True,
            "bri": 255,
            "fx": 0,
            "pal": 0,
            "col": copy.deepcopy(segment_color),
        })

    lit_color = [color, [0, 0, 0], [0, 0, 0]]
    for position in positions:
        append_segment(cursor, position, BLACK)
        append_segment(position, position + 1, lit_color if position in lit else BLACK)
        cursor = position + 1
    append_segment(cursor, led_count, BLACK)
    return segments


def blackout_payload(args: argparse.Namespace) -> dict[str, Any]:
    segments = static_segments(args.led_count, [], FALLBACK_DOT_COLOR)
    return {
        "on": True,
        "transition": args.blackout_transition,
        "seg": stop_unused_segments(segments, args.max_segments),
    }


def toll_payload(state: dict[str, Any], args: argparse.Namespace, count: int) -> dict[str, Any]:
    all_positions = toll_positions(
        args.led_count, args.hour, args.top_offset, args.dot_gap, args.top_at_high_index
    )
    segments = static_segments(
        args.led_count, all_positions[:count], dot_color(state), reserved_positions=all_positions
    )
    return {
        "on": True,
        "transition": 0,
        "seg": stop_unused_segments(segments, args.max_segments),
    }


def sweep_payload(state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    source = next((segment for segment in state.get("seg", []) if segment.get("on", True)), {})
    segment = {
        "id": 0,
        "start": 0,
        "stop": args.led_count,
        "on": True,
        "fx": args.sweep_effect,
        "sx": args.sweep_speed,
        "ix": args.sweep_intensity,
        "rev": args.top_at_high_index,
    }
    for key in ("bri", "cct", "col", "pal"):
        if key in source:
            segment[key] = source[key]
    return {
        "on": True,
        "transition": args.transition,
        "seg": stop_unused_segments([segment], args.max_segments),
    }


def display_matches(state: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Return true only while WLED still shows the state this controller posted."""
    actual = {int(segment.get("id", 0)): segment for segment in state.get("seg", []) if segment.get("stop", 0)}
    expected = {int(segment.get("id", 0)): segment for segment in payload.get("seg", []) if segment.get("stop", 0)}
    if set(actual) != set(expected):
        return False
    for segment_id, wanted in expected.items():
        shown = actual[segment_id]
        for key in ("start", "stop", "fx", "col"):
            if key in wanted and shown.get(key) != wanted[key]:
                return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wled-url", default=os.getenv("WLED_URL", "http://wled.local"))
    parser.add_argument("--hour", type=int, default=dt.datetime.now().hour)
    parser.add_argument("--led-count", type=int, default=int(os.getenv("WLED_LED_COUNT", "0")))
    parser.add_argument("--top-offset", type=int, default=int(os.getenv("WLED_TOP_OFFSET", "5")))
    parser.add_argument("--dot-gap", type=int, default=int(os.getenv("WLED_DOT_GAP", "2")))
    parser.add_argument("--top-at-high-index", action=argparse.BooleanOptionalAction,
                        default=env_bool("WLED_TOP_AT_HIGH_INDEX", True))
    parser.add_argument("--sweep-effect", type=int, default=6, help="WLED effect ID used for the sweep")
    parser.add_argument("--sweep-speed", type=int, default=50)
    parser.add_argument("--sweep-intensity", type=int, default=180)
    parser.add_argument("--sweep-seconds", type=float, default=8.0)
    parser.add_argument("--blackout-seconds", type=float, default=0.75)
    parser.add_argument("--toll-seconds", type=float, default=1.0)
    parser.add_argument("--hold-seconds", type=float, default=5.0)
    parser.add_argument("--transition", type=int, default=7, help="Sweep transition in 100 ms units")
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
        if realtime_active(info):
            print("WLED realtime input is active; hourly display skipped.")
            return 0
        if not state.get("on") or not state.get("seg"):
            print("WLED is off or has no active segment; hourly display skipped.")
            return 0
        if args.led_count <= 0:
            args.led_count = int(info.get("leds", {}).get("count", 0))
        args.max_segments = int(info.get("leds", {}).get("maxseg", 32))

        sweep = sweep_payload(state, args)
        blackout = blackout_payload(args)
        tolls = [toll_payload(state, args, count) for count in range(1, hour_count(args.hour) + 1)]
        if args.dry_run:
            print(json.dumps({"sweep": sweep, "blackout": blackout, "tolls": tolls, "restore": state}, indent=2))
            return 0

        changed = False
        owns_display = False
        try:
            request_json(args.wled_url, "/json/state", sweep)
            changed = True
            owns_display = True
            time.sleep(args.sweep_seconds)
            if not still_owns_display(args, sweep, "the sweep"):
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
