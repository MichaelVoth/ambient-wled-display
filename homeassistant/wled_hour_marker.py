#!/usr/bin/env python3
"""Sweep the current WLED look, then show one top-down gap per hour."""

from __future__ import annotations

import argparse
import copy
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

    count = hour_count(hour)
    marker_length = min(led_count, count * pixels_per_mark)
    segments: list[dict[str, Any]] = []
    segment_id = 0

    if top_at_high_index:
        cursor = led_count - marker_length
        if cursor > 0:
            segments.append({"id": segment_id, "start": 0, "stop": cursor, "kind": "content"})
            segment_id += 1
        while cursor < led_count:
            content_stop = min(led_count, cursor + pixels_per_mark - gap_width)
            if content_stop > cursor:
                segments.append({"id": segment_id, "start": cursor, "stop": content_stop, "kind": "content"})
                segment_id += 1
            gap_stop = min(led_count, content_stop + gap_width)
            if gap_stop > content_stop:
                segments.append({"id": segment_id, "start": content_stop, "stop": gap_stop, "kind": "gap"})
                segment_id += 1
            cursor = gap_stop
    else:
        cursor = 0
        while cursor < marker_length:
            gap_stop = min(marker_length, cursor + gap_width)
            segments.append({"id": segment_id, "start": cursor, "stop": gap_stop, "kind": "gap"})
            segment_id += 1
            content_stop = min(marker_length, cursor + pixels_per_mark)
            if content_stop > gap_stop:
                segments.append({"id": segment_id, "start": gap_stop, "stop": content_stop, "kind": "content"})
                segment_id += 1
            cursor = content_stop
        if marker_length < led_count:
            segments.append({"id": segment_id, "start": marker_length, "stop": led_count, "kind": "content"})
    return segments


def marker_payload(state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    source = next((segment for segment in state.get("seg", []) if segment.get("on", True)), {})
    appearance = {
        key: source[key]
        for key in ("bri", "cct", "col", "fx", "sx", "ix", "pal", "c1", "c2", "c3", "rev", "mi")
        if key in source
    }
    segments = build_marker_segments(
        args.led_count, args.hour, args.pixels_per_mark, args.gap_width, args.top_at_high_index
    )
    for segment in segments:
        kind = segment.pop("kind")
        segment["on"] = True
        if kind == "content":
            segment.update(appearance)
        else:
            segment.update({"bri": 255, "fx": 0, "pal": 0, "col": [[0, 0, 0], [0, 0, 0], [0, 0, 0]]})
    if len(segments) > args.max_segments:
        raise ValueError(
            f"hour display needs {len(segments)} segments but this WLED device supports {args.max_segments}"
        )
    segments.extend(
        {"id": segment_id, "stop": 0} for segment_id in range(len(segments), args.max_segments)
    )
    return {"on": True, "transition": args.marker_transition, "seg": segments}


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
    return {"on": True, "transition": args.transition, "seg": [segment]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wled-url", default=os.getenv("WLED_URL", "http://wled.local"))
    parser.add_argument("--hour", type=int, default=dt.datetime.now().hour)
    parser.add_argument("--led-count", type=int, default=int(os.getenv("WLED_LED_COUNT", "0")))
    parser.add_argument("--pixels-per-mark", type=int, default=int(os.getenv("WLED_PIXELS_PER_MARK", "12")))
    parser.add_argument("--gap-width", type=int, default=int(os.getenv("WLED_GAP_WIDTH", "4")))
    parser.add_argument("--top-at-high-index", action=argparse.BooleanOptionalAction,
                        default=env_bool("WLED_TOP_AT_HIGH_INDEX", True))
    parser.add_argument("--sweep-effect", type=int, default=6, help="WLED effect ID used for the sweep")
    parser.add_argument("--sweep-speed", type=int, default=50)
    parser.add_argument("--sweep-intensity", type=int, default=180)
    parser.add_argument("--sweep-seconds", type=float, default=8.0)
    parser.add_argument("--marker-seconds", type=float, default=10.0)
    parser.add_argument("--transition", type=int, default=7, help="Sweep transition in 100 ms units")
    parser.add_argument("--marker-transition", type=int, default=5, help="Bar fade-in in 100 ms units")
    parser.add_argument("--restore-transition", type=int, default=20, help="Restoration fade in 100 ms units")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


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
        marker = marker_payload(state, args)
        if args.dry_run:
            print(json.dumps({"sweep": sweep, "marker": marker, "restore": state}, indent=2))
            return 0
        changed = False
        try:
            request_json(args.wled_url, "/json/state", sweep)
            changed = True
            time.sleep(args.sweep_seconds)
            request_json(args.wled_url, "/json/state", marker)
            time.sleep(args.marker_seconds)
        finally:
            if changed:
                restore = copy.deepcopy(state)
                restore["transition"] = args.restore_transition
                restore_state(args.wled_url, restore)
        return 0
    except (ValueError, WLEDError) as exc:
        print(f"Hourly display failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
