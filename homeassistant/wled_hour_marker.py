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

    gaps: list[tuple[int, int]] = []
    for number in range(hour_count(hour)):
        if top_at_high_index:
            stop = led_count - number * pixels_per_mark
            start = max(0, stop - gap_width)
        else:
            start = number * pixels_per_mark
            stop = min(led_count, start + gap_width)
        if 0 <= start < stop <= led_count:
            gaps.append((start, stop))

    boundaries = sorted({0, led_count, *(point for gap in gaps for point in gap)})
    segments: list[dict[str, Any]] = []
    for start, stop in zip(boundaries, boundaries[1:]):
        kind = "gap" if any(start >= gap_start and stop <= gap_stop for gap_start, gap_stop in gaps) else "content"
        segments.append({"id": len(segments), "start": start, "stop": stop, "kind": kind})
    return segments


def marker_payload(state: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    source = next((segment for segment in state.get("seg", []) if segment.get("on", True)), {})
    color = copy.deepcopy(source.get("col", [[255, 160, 0], [0, 0, 0], [0, 0, 0]]))
    segments = build_marker_segments(
        args.led_count, args.hour, args.pixels_per_mark, args.gap_width, args.top_at_high_index
    )
    for segment in segments:
        kind = segment.pop("kind")
        segment["on"] = True
        if kind == "content":
            segment.update({"bri": 255, "fx": 0, "pal": 0, "col": color})
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
    parser.add_argument("--pixels-per-mark", type=int, default=int(os.getenv("WLED_PIXELS_PER_MARK", "22")))
    parser.add_argument("--gap-width", type=int, default=int(os.getenv("WLED_GAP_WIDTH", "10")))
    parser.add_argument("--top-at-high-index", action=argparse.BooleanOptionalAction,
                        default=env_bool("WLED_TOP_AT_HIGH_INDEX", True))
    parser.add_argument("--sweep-effect", type=int, default=6, help="WLED effect ID used for the sweep")
    parser.add_argument("--sweep-speed", type=int, default=50)
    parser.add_argument("--sweep-intensity", type=int, default=180)
    parser.add_argument("--sweep-seconds", type=float, default=8.0)
    parser.add_argument("--marker-seconds", type=float, default=20.0)
    parser.add_argument("--transition", type=int, default=7, help="Sweep transition in 100 ms units")
    parser.add_argument("--marker-transition", type=int, default=5, help="Bar fade-in in 100 ms units")
    parser.add_argument("--restore-transition", type=int, default=30, help="Restoration fade in 100 ms units")
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
        owns_display = False
        try:
            request_json(args.wled_url, "/json/state", sweep)
            changed = True
            owns_display = True
            time.sleep(args.sweep_seconds)
            if not display_matches(get_state(args.wled_url), sweep):
                owns_display = False
                print("Hourly display yielded to another WLED change during the sweep.")
                return 0
            request_json(args.wled_url, "/json/state", marker)
            time.sleep(args.marker_seconds)
            if not display_matches(get_state(args.wled_url), marker):
                owns_display = False
                print("Hourly display yielded to another WLED change during the hour readout.")
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
