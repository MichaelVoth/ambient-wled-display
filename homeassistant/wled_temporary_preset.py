#!/usr/bin/env python3
"""Show a WLED preset briefly and restore the exact previous state."""

from __future__ import annotations

import argparse
import os
import time

from wled_client import WLEDError, get_info, get_state, realtime_active, request_json, restore_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wled-url", default=os.getenv("WLED_URL", "http://wled.local"))
    parser.add_argument("--preset", type=int, required=True)
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--allow-realtime", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if not args.allow_realtime and realtime_active(get_info(args.wled_url)):
            print("WLED realtime input is active; temporary preset skipped.")
            return 0
        previous = get_state(args.wled_url)
        request_json(args.wled_url, "/json/state", {"on": True, "ps": args.preset})
        time.sleep(max(0, args.duration))
        restore_state(args.wled_url, previous)
        return 0
    except WLEDError as exc:
        print(f"Temporary preset failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
