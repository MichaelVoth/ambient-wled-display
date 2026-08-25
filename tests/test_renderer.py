import json
import pathlib
import struct
import sys
import tempfile
import time
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "renderer"))

from ambient_renderer.color import BLACK  # noqa: E402
from ambient_renderer.config import DeviceConfig, LaneConfig, RendererConfig, load_config  # noqa: E402
from ambient_renderer.ddp import DDP_PUSH, encode_packet, encode_packets  # noqa: E402
from ambient_renderer.effects import (  # noqa: E402
    HourEvent,
    HourTiming,
    SignalEvent,
    render_base,
    render_hour,
    render_signal,
)
from ambient_renderer.engine import RendererEngine  # noqa: E402
from ambient_renderer.output import DRGB_PROTOCOL, encode_drgb  # noqa: E402


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

    def test_udp_realtime_drgb_is_one_compact_complete_frame(self):
        frame = [(1, 2, 3), (4, 5, 6)]
        packet = encode_drgb(frame, timeout=2)
        self.assertEqual(packet, bytes([DRGB_PROTOCOL, 2, 1, 2, 3, 4, 5, 6]))

    def test_udp_realtime_rejects_frames_over_wled_limit(self):
        with self.assertRaisesRegex(ValueError, "at most 490"):
            encode_drgb([(1, 2, 3)] * 491)

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

    def test_every_semantic_signal_restores_exactly_to_base(self):
        device = self.device()
        base = [(90, 60, 30)] * 278
        for name in ("reminder", "success", "warning", "celebration"):
            event = SignalEvent(name, 0)
            active = render_signal(base, device, event, 1.0)
            restored = render_signal(base, device, event, float(event.duration))
            self.assertNotEqual(active, base, name)
            self.assertEqual(restored, base, name)

    def test_reminder_is_concentrated_in_visible_top_third(self):
        device = self.device()
        base = [(20, 20, 20)] * 278
        event = SignalEvent("reminder", 0)
        frame = render_signal(base, device, event, 2.0)
        top = frame[138]
        bottom = frame[0]
        self.assertGreater(sum(top), sum(bottom))

    def test_rain_has_smooth_downward_blue_drops(self):
        device = self.device()
        palette = ((6, 20, 46), (66, 166, 161))
        dry = render_base(device, 10.0, palette, 0.018, False, False)
        rainy_now = render_base(device, 10.0, palette, 0.018, True, False)
        rainy_later = render_base(device, 10.2, palette, 0.018, True, False)
        self.assertNotEqual(rainy_now, dry)
        self.assertNotEqual(rainy_now, rainy_later)

    def test_nebula_continuously_introduces_new_colors(self):
        device = self.device()
        palette = ((5, 10, 35), (20, 130, 150), (180, 70, 170), (230, 170, 60))
        first = render_base(device, 10.0, palette, 0.006, False, False, cloud_scale=1.4)
        later = render_base(device, 30.0, palette, 0.006, False, False, cloud_scale=1.4)
        self.assertNotEqual(first, later)
        self.assertGreater(len(set(first[:139])), 20)

    def test_ambient_controls_persist_and_crossfade(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = pathlib.Path(directory) / "ambient.json"
            config = RendererConfig(
                fps=30,
                output_enabled=False,
                palette=("#000000", "#ffffff"),
                palette_speed=0.01,
                devices=(self.device(),),
                log_path=str(pathlib.Path(directory) / "events.jsonl"),
                settings_path=str(settings_path),
            )
            engine = RendererEngine(config)
            try:
                before = engine._ambient_at(time.monotonic())
                saved = engine.set_ambient({
                    "preset": "cosmic",
                    "speed": 0.006,
                    "cloud_scale": 1.6,
                    "saturation": 1.25,
                    "brightness": 0.7,
                })
                transition_start = engine._ambient_at(engine.ambient_changed_at)
                transition_end = engine._ambient_at(engine.ambient_changed_at + 3.1)
                self.assertEqual(transition_start["speed"], before["speed"])
                self.assertEqual(transition_end["speed"], 0.006)
                self.assertEqual(saved["brightness"], 0.7)
                self.assertTrue(settings_path.exists())
            finally:
                engine.stop()

            restored = RendererEngine(config)
            try:
                self.assertEqual(restored.status()["ambient"]["speed"], 0.006)
                self.assertEqual(restored.status()["ambient"]["cloud_scale"], 1.6)
            finally:
                restored.stop()

    def test_ambient_controls_reject_unsafe_values(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RendererConfig(
                fps=30,
                output_enabled=False,
                palette=("#000000", "#ffffff"),
                palette_speed=0.01,
                devices=(self.device(),),
                log_path=str(pathlib.Path(directory) / "events.jsonl"),
                settings_path=str(pathlib.Path(directory) / "ambient.json"),
            )
            engine = RendererEngine(config)
            try:
                with self.assertRaisesRegex(ValueError, "speed"):
                    engine.set_ambient({"speed": 1.0})
                with self.assertRaisesRegex(ValueError, "between 2 and 8"):
                    engine.set_ambient({"palette": ["#ffffff"]})
            finally:
                engine.stop()

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

    def test_config_defaults_to_udp_realtime_transport(self):
        config = {
            "devices": [{
                "id": "office", "host": "wled.local", "pixel_count": 10,
                "lanes": [{"id": "one", "start": 0, "length": 10}],
            }]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            device = load_config(path).devices[0]
            self.assertEqual(device.transport, "udp_realtime")
            self.assertEqual(device.realtime_port, 21324)

    def test_receiver_slowdown_makes_renderer_health_fail(self):
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
                engine.mode = "renderer"
                engine.receiver_status["office"] = {
                    "ok": True,
                    "checked_at": time.time(),
                    "fps": 3,
                    "live_mode": "UDP",
                }
                status = engine.status()
                self.assertFalse(status["ok"])
                self.assertIn("displays 3 FPS", status["health_issues"][0])
            finally:
                engine.stop()

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

    def test_semantic_signal_priority_and_validation(self):
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
            engine._validate_outputs = lambda: None
            try:
                with self.assertRaisesRegex(ValueError, "unknown signal"):
                    engine.trigger_signal("mystery", take_output=True)
                self.assertEqual(engine.status()["mode"], "preview")
                self.assertEqual(engine.trigger_signal("reminder")["disposition"], "started")
                result = engine.trigger_signal("warning")
                self.assertEqual(result["disposition"], "replaced_lower_priority")
                self.assertEqual(engine.status()["active_event"]["signal"], "warning")
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
