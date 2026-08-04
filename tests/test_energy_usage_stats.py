import datetime as dt
import pathlib
import sqlite3
import sys
import unittest
from zoneinfo import ZoneInfo


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "homeassistant"))

from energy_usage_stats import daily_total  # noqa: E402


class EnergyUsageTests(unittest.TestCase):
    def test_daily_total_uses_cumulative_difference(self):
        database = sqlite3.connect(":memory:")
        database.execute("CREATE TABLE statistics_meta (id INTEGER PRIMARY KEY, statistic_id TEXT)")
        database.execute("CREATE TABLE statistics (metadata_id INTEGER, start_ts REAL, sum REAL)")
        database.execute("INSERT INTO statistics_meta VALUES (1, 'energy:test')")
        timezone = ZoneInfo("UTC")
        day = dt.date(2026, 7, 28)
        base = dt.datetime(2026, 7, 28, tzinfo=timezone).timestamp()
        database.executemany(
            "INSERT INTO statistics VALUES (1, ?, ?)",
            [(base, 100.0), (base + 3600, 101.5), (base + 7200, 104.25)],
        )
        self.assertEqual(daily_total(database, "energy:test", day, timezone), 4.25)


if __name__ == "__main__":
    unittest.main()
