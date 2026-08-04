#!/usr/bin/env python3
"""Sweep the current WLED look, then show one top-down gap per hour."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from typing import Any

from wled_client import WLEDError, get_info, get_state, realtime_active, request_json, restore_state


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def hour_count(hour: int) -> int:
    return hour % 12 or 12


def build_marker_segments(
    led_count: int,
    hour: int,
    pixels_per_mark: int,
    gap_width: int,
    top_at_high_index: bool,
) -> list[dict[str, Any]]:
    if led_count < 1 or pixels_per_mark < 1 or gap_width < 1:
        raise ValueError("LED count, spacing, and gap width must all be positive")
    if gap_width >= pixels_per_mark:
        raise ValueError("gap width must be smaller than pixels per mark")

    marker_length = min(led_count, hour_count(hour) * pixels_per_mark)
    marker_start = led_count - marker_length if top_at_high_index else 0
    marker_stop = led_count if top_at_high_index else marker_length
    marker = {
        "id": 0,
        "start": marker_start,
        "stop": marker_stop,
        "grp": pixels_per_mark - gap_width,
        "spc": gap_width,
        "of": 0,
        "on": True,
    }
    remainder = {
        "id": 1,
        "start": 0 if top_at_high_index else marker_stop,
        "stop": marker_start if top_at_high_index else led_count,
        "grp": 1,
        "spc": 0,
        "of": 0,
        "on": True,
    }
    return [marker, remainder]


def marker_payload(state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    source = next((segment for segment in state.get("seg", []) if segment.get("on", True)), {})
    appearance = {
        key: source[key]
        for key in ("bri", "cct", "col", "fx", "sx", "ix", "pal", "rev", "mi")
        if key in source
    }
    segments = build_marker_segments(
        args.led_count, args.hour, args.pixels_per_mark, args.gap_width, args.top_at_high_index
    )
    for segment in segments:
        if segment["on"]:
            segment.update(appearance)
    return {"on": True, "transition": args.transition, "seg": segments}


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
        "rev": not args.top_at_high_index,
    }
    for key in ("bri", "cct", "col", "pal"):
        if key in source:
            segment[key] = source[key]
    return {"on": True, "transition": args.transition, "seg": [segment]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wled-url", default=os.getenv("WLED_URL", "http://wled.local"))
    parser.add_argument("--hour", type=int, default=dt.datetime.now().hour)
    parser.add_argument("--led-count", type=int, default=int(os.getenv("WLED_LED_COUNT", "300")))
    parser.add_argument("--pixels-per-mark", type=int, default=int(os.getenv("WLED_PIXELS_PER_MARK", "9")))
    parser.add_argument("--gap-width", type=int, default=int(os.getenv("WLED_GAP_WIDTH", "2")))
    parser.add_argument("--top-at-high-index", action=argparse.BooleanOptionalAction,
                        default=env_bool("WLED_TOP_AT_HIGH_INDEX", True))
    parser.add_argument("--sweep-effect", type=int, default=47, help="WLED effect ID used for the sweep")
    parser.add_argument("--sweep-speed", type=int, default=140)
    parser.add_argument("--sweep-intensity", type=int, default=128)
    parser.add_argument("--sweep-seconds", type=float, default=3.0)
    parser.add_argument("--marker-seconds", type=float, default=8.0)
    parser.add_argument("--transition", type=int, default=7, help="WLED transition in 100 ms units")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        state = get_state(args.wled_url)
        if realtime_active(get_info(args.wled_url)):
            print("WLED realtime input is active; hourly display skipped.")
            return 0
        sweep = sweep_payload(state, args)
        marker = marker_payload(state, args)
        if args.dry_run:
            print(json.dumps({"sweep": sweep, "marker": marker, "restore": state}, indent=2))
            return 0
        request_json(args.wled_url, "/json/state", sweep)
        time.sleep(args.sweep_seconds)
        request_json(args.wled_url, "/json/state", marker)
        time.sleep(args.marker_seconds)
        restore_state(args.wled_url, state)
        return 0
    except (ValueError, WLEDError) as exc:
        print(f"Hourly display failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
