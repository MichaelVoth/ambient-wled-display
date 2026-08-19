import pathlib
import sys
import unittest
from types import SimpleNamespace


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "homeassistant"))

from wled_hour_marker import build_marker_segments, display_matches, hour_count, marker_payload  # noqa: E402


class HourMarkerTests(unittest.TestCase):
    def test_clock_hours(self):
        self.assertEqual(hour_count(0), 12)
        self.assertEqual(hour_count(13), 1)
        self.assertEqual(hour_count(17), 5)

    def test_five_pm_grows_from_high_index_top(self):
        segments = build_marker_segments(300, 17, 22, 10, True)
        gaps = [segment for segment in segments if segment["kind"] == "gap"]
        self.assertEqual(len(gaps), 5)
        self.assertEqual((segments[0]["start"], segments[0]["stop"]), (0, 202))
        self.assertEqual((gaps[0]["start"], gaps[0]["stop"]), (202, 212))
        self.assertEqual((gaps[-1]["start"], gaps[-1]["stop"]), (290, 300))

    def test_low_index_orientation(self):
        segments = build_marker_segments(300, 3, 22, 10, False)
        gaps = [segment for segment in segments if segment["kind"] == "gap"]
        self.assertEqual(len(gaps), 3)
        self.assertEqual((gaps[0]["start"], gaps[0]["stop"]), (0, 10))
        self.assertEqual((gaps[-1]["start"], gaps[-1]["stop"]), (44, 54))

    def test_marker_is_clamped_to_strip(self):
        segments = build_marker_segments(20, 12, 9, 2, True)
        self.assertEqual(segments[0]["start"], 0)
        self.assertEqual(segments[-1]["stop"], 20)
        self.assertTrue(all(segment["stop"] > segment["start"] for segment in segments))

    def test_invalid_spacing_is_rejected(self):
        with self.assertRaises(ValueError):
            build_marker_segments(300, 5, 2, 2, True)

    def test_marker_payload_makes_explicit_black_bars(self):
        state = {"seg": [{"on": True, "fx": 88, "pal": 1, "col": [[255, 160, 0]]}]}
        args = SimpleNamespace(
            led_count=278,
            hour=17,
            pixels_per_mark=22,
            gap_width=10,
            top_at_high_index=True,
            max_segments=32,
            marker_transition=5,
        )
        payload = marker_payload(state, args)
        active = [segment for segment in payload["seg"] if segment.get("stop", 0)]
        gaps = [segment for segment in active if segment.get("col") == [[0, 0, 0]] * 3]
        self.assertEqual([(gap["start"], gap["stop"]) for gap in gaps], [
            (180, 190), (202, 212), (224, 234), (246, 256), (268, 278)
        ])
        content = [segment for segment in active if segment not in gaps]
        self.assertEqual(len(content), 5)
        self.assertTrue(all(segment.get("fx") == 0 for segment in content))

    def test_marker_rejects_device_with_too_few_segments(self):
        state = {"seg": [{"on": True, "fx": 0, "col": [[255, 255, 255]]}]}
        args = SimpleNamespace(
            led_count=278,
            hour=12,
            pixels_per_mark=22,
            gap_width=10,
            top_at_high_index=True,
            max_segments=4,
            marker_transition=5,
        )
        with self.assertRaisesRegex(ValueError, "supports 4"):
            marker_payload(state, args)

    def test_display_ownership_detects_external_change(self):
        payload = {"seg": [
            {"id": 0, "start": 0, "stop": 10, "fx": 0, "col": [[255, 160, 0]]},
            {"id": 1, "start": 10, "stop": 18, "fx": 0, "col": [[0, 0, 0]]},
        ]}
        self.assertTrue(display_matches({"seg": payload["seg"]}, payload))
        changed = {"seg": [dict(payload["seg"][0], fx=88), payload["seg"][1]]}
        self.assertFalse(display_matches(changed, payload))


if __name__ == "__main__":
    unittest.main()
