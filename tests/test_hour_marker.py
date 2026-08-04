import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "homeassistant"))

from wled_hour_marker import build_marker_segments, hour_count  # noqa: E402


class HourMarkerTests(unittest.TestCase):
    def test_clock_hours(self):
        self.assertEqual(hour_count(0), 12)
        self.assertEqual(hour_count(13), 1)
        self.assertEqual(hour_count(17), 5)

    def test_five_pm_grows_from_high_index_top(self):
        marker, remainder = build_marker_segments(300, 17, 9, 2, True)
        self.assertEqual((marker["start"], marker["stop"]), (255, 300))
        self.assertEqual((marker["grp"], marker["spc"]), (7, 2))
        self.assertEqual((remainder["start"], remainder["stop"]), (0, 255))

    def test_low_index_orientation(self):
        marker, remainder = build_marker_segments(300, 3, 9, 2, False)
        self.assertEqual((marker["start"], marker["stop"]), (0, 27))
        self.assertEqual((remainder["start"], remainder["stop"]), (27, 300))

    def test_marker_is_clamped_to_strip(self):
        marker, remainder = build_marker_segments(20, 12, 9, 2, True)
        self.assertEqual((marker["start"], marker["stop"]), (0, 20))
        self.assertEqual((remainder["start"], remainder["stop"]), (0, 0))

    def test_invalid_spacing_is_rejected(self):
        with self.assertRaises(ValueError):
            build_marker_segments(300, 5, 2, 2, True)


if __name__ == "__main__":
    unittest.main()
