import pathlib
import sys
import unittest
from types import SimpleNamespace


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "homeassistant"))

from wled_hour_marker import (  # noqa: E402
    BLACK,
    blackout_payload,
    display_matches,
    hour_count,
    static_segments,
    toll_payload,
    toll_positions,
)


class HourMarkerTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "led_count": 278,
            "hour": 15,
            "top_offset": 5,
            "dot_gap": 2,
            "top_at_high_index": True,
            "max_segments": 32,
            "blackout_transition": 0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_clock_hours(self):
        self.assertEqual(hour_count(0), 12)
        self.assertEqual(hour_count(13), 1)
        self.assertEqual(hour_count(17), 5)

    def test_three_pm_tolls_from_high_index_top(self):
        self.assertEqual(toll_positions(278, 15, 5, 2, True), [272, 269, 266])

    def test_low_index_orientation(self):
        self.assertEqual(toll_positions(278, 15, 5, 2, False), [5, 8, 11])

    def test_noon_fits_inside_top_34_leds(self):
        positions = toll_positions(278, 12, 5, 2, True)
        self.assertEqual(len(positions), 12)
        self.assertEqual((min(positions), max(positions)), (239, 272))

    def test_toll_that_does_not_fit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not fit"):
            toll_positions(20, 12, 5, 2, True)

    def test_static_segments_are_black_except_for_single_pixel_dots(self):
        segments = static_segments(20, [5, 8, 11], [10, 20, 30])
        lit = [segment for segment in segments if segment["col"] != BLACK]
        self.assertEqual([(segment["start"], segment["stop"]) for segment in lit], [
            (5, 6), (8, 9), (11, 12)
        ])
        self.assertTrue(all(segment["stop"] - segment["start"] == 1 for segment in lit))
        self.assertTrue(all(segment["fx"] == 0 for segment in segments))

    def test_toll_payload_is_cumulative(self):
        state = {"seg": [{"on": True, "fx": 88, "pal": 1, "col": [[10, 20, 30]]}]}
        payload = toll_payload(state, self.args(), 3)
        active = [segment for segment in payload["seg"] if segment.get("stop", 0)]
        lit = [segment for segment in active if segment.get("col") != BLACK]
        self.assertEqual([(segment["start"], segment["stop"]) for segment in lit], [
            (266, 267), (269, 270), (272, 273)
        ])
        self.assertEqual([segment["col"][0] for segment in lit], [[10, 20, 30]] * 3)

    def test_every_toll_uses_a_stable_segment_layout(self):
        state = {"seg": [{"on": True, "col": [[10, 20, 30]]}]}
        payloads = [toll_payload(state, self.args(), count) for count in (1, 2, 3)]
        layouts = [[
            (segment["id"], segment["start"], segment["stop"])
            for segment in payload["seg"] if segment.get("stop", 0)
        ] for payload in payloads]
        self.assertEqual(layouts[0], layouts[1])
        self.assertEqual(layouts[1], layouts[2])

    def test_blackout_payload_explicitly_blacks_entire_strip(self):
        payload = blackout_payload(self.args())
        active = [segment for segment in payload["seg"] if segment.get("stop", 0)]
        self.assertEqual(len(active), 1)
        self.assertEqual((active[0]["start"], active[0]["stop"]), (0, 278))
        self.assertEqual(active[0]["col"], BLACK)

    def test_noon_uses_25_segments_and_fits_device(self):
        state = {"seg": [{"on": True, "col": [[255, 255, 255]]}]}
        payload = toll_payload(state, self.args(hour=12), 12)
        active = [segment for segment in payload["seg"] if segment.get("stop", 0)]
        self.assertEqual(len(active), 25)

    def test_toll_rejects_device_with_too_few_segments(self):
        state = {"seg": [{"on": True, "col": [[255, 255, 255]]}]}
        with self.assertRaisesRegex(ValueError, "supports 4"):
            toll_payload(state, self.args(max_segments=4), 3)

    def test_display_ownership_detects_external_change(self):
        payload = {"seg": [
            {"id": 0, "start": 0, "stop": 10, "fx": 0, "col": [[255, 160, 0]]},
            {"id": 1, "start": 10, "stop": 18, "fx": 0, "col": BLACK},
        ]}
        self.assertTrue(display_matches({"seg": payload["seg"]}, payload))
        changed = {"seg": [dict(payload["seg"][0], fx=88), payload["seg"][1]]}
        self.assertFalse(display_matches(changed, payload))


if __name__ == "__main__":
    unittest.main()
