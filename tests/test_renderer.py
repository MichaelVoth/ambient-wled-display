import json
import pathlib
import struct
import sys
import tempfile
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "renderer"))

from ambient_renderer.color import BLACK  # noqa: E402
from ambient_renderer.config import DeviceConfig, LaneConfig, RendererConfig, load_config  # noqa: E402
from ambient_renderer.ddp import DDP_PUSH, encode_packet, encode_packets  # noqa: E402
from ambient_renderer.effects import HourEvent, HourTiming, render_hour  # noqa: E402
from ambient_renderer.engine import RendererEngine  # noqa: E402


class RendererTests(unittest.TestCase):
    def device(self):
        return DeviceConfig(
            id="office",
            name="Office",
            host="127.0.0.1",
            pixel_count=278,
            brightness=1.0,
            lanes=(
                LaneConfig("a", "A", 0, 139, True),
                LaneConfig("b", "B", 139, 139, True),
            ),
        )

    def test_ddp_packet_matches_wled_header(self):
        frame = [(1, 2, 3), (4, 5, 6)]
        packet = encode_packet(frame, sequence=7)
        self.assertEqual(packet[:4], bytes([0x41, 7, 0x0B, 1]))
        self.assertEqual(struct.unpack(">I", packet[4:8])[0], 0)
        self.assertEqual(struct.unpack(">H", packet[8:10])[0], 6)
        self.assertEqual(packet[10:], bytes([1, 2, 3, 4, 5, 6]))

    def test_ddp_splits_large_frames_and_pushes_only_the_last_packet(self):
        packets = encode_packets([(1, 2, 3)] * 1001, sequence=14)
        self.assertEqual(len(packets), 3)
        self.assertFalse(packets[0][0] & DDP_PUSH)
        self.assertFalse(packets[1][0] & DDP_PUSH)
        self.assertTrue(packets[2][0] & DDP_PUSH)
        self.assertEqual([packet[1] for packet in packets], [14, 15, 1])
        self.assertEqual([struct.unpack(">I", packet[4:8])[0] for packet in packets], [0, 1440, 2880])
        self.assertEqual(sum(struct.unpack(">H", packet[8:10])[0] for packet in packets), 3003)

    def test_hour_sweep_is_monotonic_top_to_bottom(self):
        device = self.device()
        base = [(100, 80, 60)] * 278
        event = HourEvent(3, 0, HourTiming(sweep=8, feather_fraction=0.06))
        early = render_hour(base, device, event, 2)
        late = render_hour(base, device, event, 6)
        self.assertLess(sum(sum(pixel) for pixel in late), sum(sum(pixel) for pixel in early))
        self.assertEqual(early[138], BLACK)
        self.assertNotEqual(early[0], BLACK)
        self.assertEqual(early[277], BLACK)
        self.assertNotEqual(early[139], BLACK)

    def test_tolls_are_mirrored_on_both_lanes(self):
        device = self.device()
        base = [(100, 80, 60)] * 278
        timing = HourTiming(sweep=1, blackout=0.1, toll_interval=1, dot_fade=0.01)
        event = HourEvent(3, 0, timing)
        frame = render_hour(base, device, event, 3.2)
        first = [index for index in range(139) if frame[index] != BLACK]
        second = [index - 139 for index in range(139, 278) if frame[index] != BLACK]
        self.assertEqual(first, [125, 129, 133])
        self.assertEqual(second, first)

    def test_restore_blends_back_to_the_continuous_base(self):
        device = self.device()
        base = [(90, 60, 30)] * 278
        timing = HourTiming(sweep=1, blackout=0.1, toll_interval=1, hold=1, restore=2, dot_fade=0.01)
        event = HourEvent(1, 0, timing)
        restored = render_hour(base, device, event, event.duration)
        self.assertEqual(restored, base)

    def test_sweep_finishes_at_black_before_tolls_begin(self):
        device = self.device()
        base = [(90, 60, 30)] * 278
        timing = HourTiming(sweep=8, blackout=0.6)
        event = HourEvent(4, 0, timing)
        at_sweep_end = render_hour(base, device, event, 8.0)
        self.assertEqual(at_sweep_end, [BLACK] * 278)

    def test_tolls_accumulate_one_dot_per_second(self):
        device = self.device()
        base = [(90, 60, 30)] * 278
        timing = HourTiming(sweep=1, blackout=0.5, toll_interval=1, dot_fade=0.01)
        event = HourEvent(4, 0, timing)
        for expected, elapsed in ((1, 1.6), (2, 2.6), (3, 3.6), (4, 4.6)):
            frame = render_hour(base, device, event, elapsed)
            lit_first_lane = sum(pixel != BLACK for pixel in frame[:139])
            lit_second_lane = sum(pixel != BLACK for pixel in frame[139:])
            self.assertEqual((lit_first_lane, lit_second_lane), (expected, expected))

    def test_config_rejects_overlapping_lanes(self):
        config = {
            "devices": [{
                "id": "bad", "host": "wled.local", "pixel_count": 10,
                "lanes": [
                    {"id": "one", "start": 0, "length": 6},
                    {"id": "two", "start": 5, "length": 5}
                ]
            }]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlaps"):
                load_config(path)

    def test_urgent_alert_replaces_hour_and_music_rejects_events(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RendererConfig(
                fps=30,
                output_enabled=False,
                palette=("#000000", "#ffffff"),
                palette_speed=0.01,
                devices=(self.device(),),
                log_path=str(pathlib.Path(directory) / "events.jsonl"),
            )
            engine = RendererEngine(config)
            try:
                self.assertEqual(engine.trigger_hour(9)["disposition"], "started")
                self.assertEqual(engine.trigger_alert()["disposition"], "replaced_lower_priority")
                self.assertEqual(engine.status()["active_event"]["kind"], "alert")
                engine.set_mode("music")
                rejected = engine.trigger_hour(10)
                self.assertFalse(rejected["accepted"])
                self.assertIn("music", rejected["reason"])
            finally:
                engine.stop()

    def test_temporary_output_lease_releases_after_final_restored_frame(self):
        class FakeOutput:
            def __init__(self):
                self.frames = []

            def send(self, frame):
                self.frames.append(frame)

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            config = RendererConfig(
                fps=30,
                output_enabled=False,
                palette=("#000000", "#ffffff"),
                palette_speed=0.01,
                devices=(self.device(),),
                log_path=str(pathlib.Path(directory) / "events.jsonl"),
            )
            engine = RendererEngine(config)
            engine.outputs["office"].close()
            fake = FakeOutput()
            engine.outputs["office"] = fake
            engine._validate_outputs = lambda: None
            try:
                result = engine.trigger_hour(1, now=0, take_output=True)
                self.assertTrue(result["accepted"])
                self.assertEqual(engine.status()["mode"], "renderer")
                self.assertEqual(engine.status()["output_lease_return_mode"], "preview")
                engine._render(HourEvent(1, 0).duration + 0.1)
                self.assertEqual(engine.status()["mode"], "preview")
                self.assertIsNone(engine.status()["output_lease_return_mode"])
                self.assertEqual(len(fake.frames), 1)
                self.assertEqual(fake.frames[0], engine.frames["office"])
            finally:
                engine.stop()

    def test_hour_can_target_one_physical_lane_without_blacking_the_other(self):
        device = self.device()
        base = [(90, 60, 30)] * 278
        timing = HourTiming(sweep=8, blackout=0.6)
        event = HourEvent(4, 0, timing, targets=("a",))
        frame = render_hour(base, device, event, 8.1, (device.lanes[0],))
        self.assertEqual(frame[:139], [BLACK] * 139)
        self.assertEqual(frame[139:], base[139:])

    def test_unknown_device_or_lane_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RendererConfig(
                fps=30,
                output_enabled=False,
                palette=("#000000", "#ffffff"),
                palette_speed=0.01,
                devices=(self.device(),),
                log_path=str(pathlib.Path(directory) / "events.jsonl"),
            )
            engine = RendererEngine(config)
            try:
                with self.assertRaisesRegex(ValueError, "unknown targets"):
                    engine.trigger_hour(3, targets=["kitchen-that-does-not-exist"])
            finally:
                engine.stop()


if __name__ == "__main__":
    unittest.main()
