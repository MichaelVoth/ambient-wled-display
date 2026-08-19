import pathlib
import sys
import unittest
from types import SimpleNamespace


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "homeassistant"))

from wled_hour_marker import (  # noqa: E402
    BLACK,
    blackout_payload,
    configured_outputs,
    display_matches,
    encode_pixel_runs,
    frozen_frame_payload,
    hour_count,
    toll_payload,
    toll_positions,
    wipe_step_payload,
)


class HourMarkerTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "led_count": 278,
            "outputs": [(0, 139), (139, 278)],
            "hour": 15,
            "top_offset": 5,
            "dot_gap": 2,
            "top_at_high_index": True,
            "max_segments": 32,
            "blackout_transition": 0,
            "sweep_seconds": 8,
            "sweep_pixels_per_step": 2,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_clock_hours(self):
        self.assertEqual(hour_count(0), 12)
        self.assertEqual(hour_count(13), 1)
        self.assertEqual(hour_count(17), 5)

    def test_physical_outputs_are_discovered_from_wled_buses(self):
        config = {"hw": {"led": {"ins": [
            {"start": 0, "len": 139},
            {"start": 139, "len": 139},
        ]}}}
        self.assertEqual(configured_outputs(config, 278), [(0, 139), (139, 278)])

    def test_falls_back_to_one_output_when_bus_details_are_unavailable(self):
        self.assertEqual(configured_outputs({}, 278), [(0, 278)])

    def test_three_pm_tolls_from_each_output_top(self):
        self.assertEqual(toll_positions(139, 15, 5, 2, True), [133, 130, 127])

    def test_low_index_orientation(self):
        self.assertEqual(toll_positions(139, 15, 5, 2, False), [5, 8, 11])

    def test_noon_fits_inside_top_34_leds_of_each_output(self):
        positions = toll_positions(139, 12, 5, 2, True)
        self.assertEqual(len(positions), 12)
        self.assertEqual((min(positions), max(positions)), (100, 133))

    def test_toll_that_does_not_fit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not fit"):
            toll_positions(20, 12, 5, 2, True)

    def test_toll_payload_uses_one_pixel_control_segment_per_output(self):
        state = {"seg": [{"on": True, "fx": 88, "pal": 1, "col": [[10, 20, 30]]}]}
        payload = toll_payload(state, self.args(), 3)
        active = [segment for segment in payload["seg"] if segment.get("stop", 0)]
        self.assertEqual([(segment["start"], segment["stop"]) for segment in active], [
            (0, 139), (139, 278)
        ])
        expected_pixels = [
            0, 139, [0, 0, 0],
            133, [10, 20, 30],
            130, [10, 20, 30],
            127, [10, 20, 30],
        ]
        self.assertEqual(active[0]["i"], expected_pixels)
        self.assertEqual(active[1]["i"], expected_pixels)
        self.assertTrue(all(segment["frz"] for segment in active))

    def test_every_toll_uses_the_same_two_segment_layout(self):
        state = {"seg": [{"on": True, "col": [[10, 20, 30]]}]}
        payloads = [toll_payload(state, self.args(), count) for count in (1, 2, 3)]
        layouts = [[
            (segment["id"], segment["start"], segment["stop"])
            for segment in payload["seg"] if segment.get("stop", 0)
        ] for payload in payloads]
        self.assertEqual(layouts[0], layouts[1])
        self.assertEqual(layouts[1], layouts[2])

    def test_blackout_payload_explicitly_blacks_both_outputs(self):
        payload = blackout_payload(self.args())
        active = [segment for segment in payload["seg"] if segment.get("stop", 0)]
        self.assertEqual([(segment["start"], segment["stop"]) for segment in active], [
            (0, 139), (139, 278)
        ])
        self.assertTrue(all(segment["col"] == BLACK for segment in active))

    def test_noon_still_uses_only_two_segments(self):
        state = {"seg": [{"on": True, "col": [[255, 255, 255]]}]}
        payload = toll_payload(state, self.args(hour=12), 12)
        active = [segment for segment in payload["seg"] if segment.get("stop", 0)]
        self.assertEqual(len(active), 2)

    def test_two_outputs_require_two_available_segments(self):
        state = {"seg": [{"on": True, "col": [[255, 255, 255]]}]}
        with self.assertRaisesRegex(ValueError, "supports 1"):
            toll_payload(state, self.args(max_segments=1), 3)

    def test_pixel_runs_are_compacted(self):
        self.assertEqual(encode_pixel_runs([
            [1, 2, 3], [1, 2, 3], [4, 5, 6], [4, 5, 6], [4, 5, 6]
        ]), [0, 2, [1, 2, 3], 2, 5, [4, 5, 6]])

    def test_current_frame_is_frozen_independently_on_both_outputs(self):
        pixels = [[10, 20, 30]] * 139 + [[40, 50, 60]] * 139
        payload = frozen_frame_payload(self.args(), pixels)
        active = [segment for segment in payload["seg"] if segment.get("stop", 0)]
        self.assertEqual([(segment["start"], segment["stop"]) for segment in active], [
            (0, 139), (139, 278)
        ])
        self.assertEqual(active[0]["i"], [0, 139, [10, 20, 30]])
        self.assertEqual(active[1]["i"], [0, 139, [40, 50, 60]])

    def test_sweep_steps_run_simultaneously_from_both_high_index_tops(self):
        first = wipe_step_payload([(0, 139), (139, 278)], 0, 2, True)
        second = wipe_step_payload([(0, 139), (139, 278)], 1, 2, True)
        self.assertEqual([segment["i"] for segment in first["seg"]], [
            [137, 139, [0, 0, 0]], [137, 139, [0, 0, 0]]
        ])
        self.assertEqual([segment["i"] for segment in second["seg"]], [
            [135, 137, [0, 0, 0]], [135, 137, [0, 0, 0]]
        ])

    def test_display_ownership_detects_external_change(self):
        payload = {"seg": [
            {"id": 0, "start": 0, "stop": 139, "fx": 0, "frz": True, "col": BLACK},
            {"id": 1, "start": 139, "stop": 278, "fx": 0, "frz": True, "col": BLACK},
        ]}
        self.assertTrue(display_matches({"seg": payload["seg"]}, payload))
        changed = {"seg": [dict(payload["seg"][0], frz=False), payload["seg"][1]]}
        self.assertFalse(display_matches(changed, payload))


if __name__ == "__main__":
    unittest.main()
