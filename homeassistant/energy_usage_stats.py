#!/usr/bin/env python3
"""Read daily energy totals from Home Assistant's SQLite statistics."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
from zoneinfo import ZoneInfo


def daily_total(connection: sqlite3.Connection, statistic_id: str, day: dt.date, timezone: ZoneInfo) -> float:
    start = dt.datetime.combine(day, dt.time.min, timezone).timestamp()
    stop = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, timezone).timestamp()
    row = connection.execute(
        """
        SELECT MAX(s.sum) - MIN(s.sum)
        FROM statistics s
        JOIN statistics_meta m ON m.id = s.metadata_id
        WHERE m.statistic_id = ? AND s.start_ts >= ? AND s.start_ts < ?
        """,
        (statistic_id, start, stop),
    ).fetchone()
    return float(row[0] or 0.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("yesterday", "average7", "high"))
    parser.add_argument("--db", default=os.getenv("HA_DB_PATH", "/config/home-assistant_v2.db"))
    parser.add_argument("--statistic-id", default=os.getenv("HA_ENERGY_STATISTIC_ID"))
    parser.add_argument("--timezone", default=os.getenv("HA_TIMEZONE", "UTC"))
    parser.add_argument("--multiplier", type=float, default=float(os.getenv("HA_HIGH_USAGE_MULTIPLIER", "1.25")))
    parser.add_argument("--today", type=dt.date.fromisoformat, help="Override today's date for testing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.statistic_id:
        raise SystemExit("Set HA_ENERGY_STATISTIC_ID or pass --statistic-id.")
    timezone = ZoneInfo(args.timezone)
    today = args.today or dt.datetime.now(timezone).date()
    with sqlite3.connect(f"file:{args.db}?mode=ro", uri=True) as connection:
        yesterday = daily_total(connection, args.statistic_id, today - dt.timedelta(days=1), timezone)
        history = [
            daily_total(connection, args.statistic_id, today - dt.timedelta(days=days), timezone)
            for days in range(2, 9)
        ]
    average = sum(history) / len(history) if history else 0.0
    if args.mode == "yesterday":
        print(f"{yesterday:.3f}")
    elif args.mode == "average7":
        print(f"{average:.3f}")
    else:
        print("true" if average > 0 and yesterday > average * args.multiplier else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
