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
from ambient_renderer.mood import adaptive_ambient  # noqa: E402
from ambient_renderer.music import render_music  # noqa: E402
from ambient_renderer.output import DRGB_PROTOCOL, encode_drgb  # noqa: E402
from ambient_renderer.rain import Drop, RainField  # noqa: E402


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
        for name in (
            "welcome", "comfort", "curious", "goodbye",
            "storm", "reminder", "success", "warning", "celebration",
        ):
            event = SignalEvent(name, 0)
            active = render_signal(base, device, event, 1.0)
            restored = render_signal(base, device, event, float(event.duration))
            self.assertNotEqual(active, base, name)
            self.assertEqual(restored, base, name)

    def test_emotional_animations_have_distinct_motion_languages(self):
        device = self.device()
        base = [(30, 40, 50)] * device.pixel_count
        frames = {
            name: render_signal(base, device, SignalEvent(name, 0), 2.2)
            for name in ("welcome", "comfort", "curious", "goodbye")
        }
        for name, frame in frames.items():
            self.assertNotEqual(frame, base, name)
        self.assertEqual(len({tuple(frame) for frame in frames.values()}), 4)

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

    def test_stateful_rain_varies_speed_and_merges_drops(self):
        device = self.device()
        field = RainField(seed=7)
        field.drops["a"] = [
            Drop(0.20, 0.05, 0.8, 0.1),
            Drop(0.207, 0.12, 1.1, 0.8),
        ]
        field.last_at = 10.0
        field.update((device.lanes[0],), 10.03, intensity=1.0)
        self.assertEqual(len(field.drops["a"]), 1)
        self.assertEqual(field.status()["merged"], 1)
        self.assertGreater(field.drops["a"][0].velocity, 0.12)

    def test_rain_supports_clinging_dribbles_and_fast_catchup_drops(self):
        device = self.device()
        field = RainField(seed=11)
        field.drops["a"] = [Drop(0.2, 0.008, 0.4, 0.2, adhesion=0.95, previous_position=0.2)]
        field.drops["b"] = [Drop(0.2, 0.72, 2.4, 0.8, adhesion=0.05, previous_position=0.2)]
        field.last_at = 10.0
        base = [(40, 50, 60)] * device.pixel_count
        field.render(base, device, 10.1, device.lanes, intensity=0.0)
        self.assertGreater(field.drops["b"][0].position - 0.2, 0.06)
        self.assertLess(field.drops["a"][0].position - 0.2, 0.01)
        self.assertGreater(field.status()["wet_pixels"], 0)
        self.assertTrue(field.has_residue())
        field.reset()
        self.assertEqual(field.status()["active_drops"], 0)
        self.assertEqual(field.status()["wet_pixels"], 0)
        self.assertFalse(field.has_residue())

    def test_adaptive_mood_responds_to_weather_and_temperature(self):
        timestamp = 1_786_000_000.0
        mild = adaptive_ambient(timestamp, {"weather": "sunny", "temperature": 68, "temperature_unit": "°F"})
        storm = adaptive_ambient(timestamp, {"weather": "pouring", "temperature": 48, "temperature_unit": "°F"})
        self.assertNotEqual(mild["palette"], storm["palette"])
        self.assertIn("sunny", mild["mood"])
        self.assertIn("pouring", storm["mood"])
        self.assertGreater(storm["speed"], mild["speed"])

    def test_adaptive_mood_uses_house_timezone_and_sunset(self):
        timestamp = 1_786_000_000.0
        utc = adaptive_ambient(timestamp, {"weather": "sunny", "timezone": "UTC"})
        pacific = adaptive_ambient(
            timestamp,
            {"weather": "sunny", "timezone": "America/Los_Angeles", "sun_elevation": -5},
        )
        self.assertNotEqual(utc["time_mood"], pacific["time_mood"])
        self.assertIn("clear night", pacific["mood"])

    def test_house_expression_amplifies_color_motion_and_breath(self):
        timestamp = 1_786_000_000.0
        context = {"weather": "sunny", "temperature": 82, "temperature_unit": "°F", "wind_speed": 12}
        quiet = adaptive_ambient(timestamp, context, expression=0.5)
        expressive = adaptive_ambient(timestamp, context, expression=1.5)
        self.assertGreater(expressive["saturation"], quiet["saturation"])
        self.assertGreater(expressive["speed"], quiet["speed"])
        self.assertGreater(expressive["breath_depth"], quiet["breath_depth"])
        self.assertGreater(expressive["life_activity"], quiet["life_activity"])
        self.assertTrue(expressive["emotion"])
        self.assertTrue(expressive["reason"])

    def test_wind_and_organic_glimmers_change_the_living_base(self):
        device = self.device()
        palette = ((15, 30, 70), (40, 150, 135), (210, 120, 70))
        still = render_base(device, 42.0, palette, 0.004, False, False)
        windy = render_base(
            device, 42.0, palette, 0.004, False, False,
            wind_strength=0.9,
        )
        alive = render_base(
            device, 42.0, palette, 0.004, False, False,
            life_activity=1.0,
            life_color=(255, 190, 80),
        )
        self.assertNotEqual(still, windy)
        self.assertNotEqual(still, alive)

    def test_music_effects_are_distinct_and_vivid(self):
        device = self.device()
        base = [(28, 31, 44)] * device.pixel_count
        features = {"bass": 0.9, "mid": 0.72, "treble": 0.8, "energy": 0.74, "beat": 1.0, "phase": 3.2}
        frames = {
            effect: render_music(base, device, 12.0, features, effect)
            for effect in ("meter", "chunks", "firefly")
        }
        self.assertEqual(len({tuple(frame) for frame in frames.values()}), 3)
        for frame in frames.values():
            self.assertGreater(max(max(pixel) for pixel in frame), 220)
            self.assertNotEqual(frame, base)

    def test_music_meter_uses_true_black_negative_space(self):
        device = self.device()
        base = [(88, 80, 122)] * device.pixel_count
        quiet = render_music(base, device, 0.0, {"energy": 0.0}, "meter")
        active = render_music(base, device, 0.0, {"energy": 1.0}, "meter", sensitivity=0.22)
        self.assertEqual(quiet, [(0, 0, 0)] * device.pixel_count)
        self.assertGreater(sum(pixel != (0, 0, 0) for pixel in active), 0)
        self.assertGreater(sum(pixel == (0, 0, 0) for pixel in active), 0)
        self.assertLessEqual(sum(pixel != (0, 0, 0) for pixel in active), round(device.pixel_count * 0.22) + 1)

    def test_music_meter_custom_color_is_uniform_and_controllable(self):
        device = self.device()
        primary = (17, 231, 83)
        frame = render_music(
            [(90, 70, 110)] * device.pixel_count,
            device,
            42.0,
            {"energy": 0.8, "beat": 0.0},
            "meter",
            sensitivity=0.3,
            color_mode="custom",
            primary=primary,
            accent=(220, 30, 170),
        )
        lit = [pixel for pixel in frame if pixel != BLACK]
        self.assertTrue(lit)
        self.assertTrue(all(pixel[0] < pixel[1] and pixel[2] < pixel[1] for pixel in lit))
        self.assertGreater(sum(pixel == BLACK for pixel in frame), len(frame) // 2)

    def test_music_firefly_moves_continuously_without_lighting_a_background(self):
        device = self.device()
        features = {"energy": 0.55, "treble": 0.3, "phase": 1.25}
        first = render_music([], device, 10.00, features, "firefly", motion_speed=1.0)
        second = render_music([], device, 10.04, features, "firefly", motion_speed=1.0)
        self.assertGreaterEqual(sum(pixel != BLACK for pixel in first), len(device.lanes))
        self.assertGreaterEqual(sum(pixel != BLACK for pixel in second), len(device.lanes))
        self.assertLess(sum(pixel != BLACK for pixel in first), device.pixel_count // 4)
        first_positions = [index for index, pixel in enumerate(first) if pixel != BLACK]
        second_positions = [index for index, pixel in enumerate(second) if pixel != BLACK]
        # Pixel quantization can add or remove one edge pixel as the halo
        # moves, so compare the light's center rather than list lengths.
        self.assertLessEqual(abs(sum(second_positions) / len(second_positions) - sum(first_positions) / len(first_positions)), 5)

    def test_music_firefly_beats_expand_the_dot(self):
        device = self.device()
        quiet = render_music([], device, 10.0, {"energy": 0.4, "bass": 0.2, "phase": 1.0}, "firefly")
        beat = render_music([], device, 10.0, {"energy": 0.4, "bass": 0.2, "beat": 1.0, "phase": 1.0}, "firefly")
        self.assertGreater(sum(pixel != BLACK for pixel in beat), sum(pixel != BLACK for pixel in quiet))

    def test_integrated_music_keeps_renderer_ownership_and_reports_audio(self):
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
            engine._wled_state = lambda device, payload=None: {"on": True, "bri": 128}
            engine.power_path = pathlib.Path(directory) / "display-settings.json"
            try:
                engine.set_music(True, "chunks")
                engine.update_audio({"bass": 0.8, "mid": 0.5, "treble": 0.4, "energy": 0.7, "beat": 1, "phase": 2})
                status = engine.status()
                self.assertEqual(status["mode"], "renderer")
                self.assertTrue(status["music"]["enabled"])
                self.assertTrue(status["music"]["receiving_audio"])
                self.assertEqual(status["music"]["effect"], "chunks")
                self.assertTrue(engine.trigger_signal("success")["accepted"])
                engine.set_music(False)
                self.assertFalse(engine.status()["music"]["enabled"])
            finally:
                engine.stop()

    def test_context_enables_rain_and_adaptive_mode_can_be_restored(self):
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
                context = engine.set_context({"weather": "rainy", "temperature": 61, "humidity": 92})
                self.assertEqual(context["weather"], "rainy")
                self.assertTrue(engine.status()["layers"]["rain"])
                engine.set_ambient({"preset": "cosmic"})
                self.assertEqual(engine.status()["ambient"]["mode"], "manual")
                engine.set_ambient({"mode": "adaptive"})
                self.assertEqual(engine.status()["ambient"]["mode"], "adaptive")
                self.assertIn("rainy", engine.status()["ambient"]["mood"])
            finally:
                engine.stop()

    def test_nebula_continuously_introduces_new_colors(self):
        device = self.device()
        palette = ((5, 10, 35), (20, 130, 150), (180, 70, 170), (230, 170, 60))
        first = render_base(device, 10.0, palette, 0.006, False, False, cloud_scale=1.4)
        later = render_base(device, 30.0, palette, 0.006, False, False, cloud_scale=1.4)
        self.assertNotEqual(first, later)
        self.assertGreater(len(set(first[:139])), 20)

    def test_party_field_stays_vivid_and_moves_continuously(self):
        device = self.device()
        palette = tuple(
            tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
            for color in (
                "#ff006e", "#ff2415", "#ff7a00", "#ffe600",
                "#18f05c", "#00dcff", "#2155ff", "#8b16ff",
            )
        )
        first = render_base(
            device, 10.0, palette, 0.018, False, False,
            cloud_scale=1.05, saturation=1.55, style="party",
        )
        next_frame = render_base(
            device, 10.0 + 1 / 30, palette, 0.018, False, False,
            cloud_scale=1.05, saturation=1.55, style="party",
        )
        later = render_base(
            device, 40.0, palette, 0.018, False, False,
            cloud_scale=1.05, saturation=1.55, style="party",
        )
        self.assertNotEqual(first, next_frame)
        self.assertNotEqual(first, later)
        vivid = sum(max(pixel) - min(pixel) >= 120 for pixel in first)
        self.assertGreater(vivid, device.pixel_count * 0.9)
        self.assertGreater(len(set(first[:139])), 20)
        frame_deltas = [
            max(abs(first_channel - next_channel) for first_channel, next_channel in zip(a, b))
            for a, b in zip(first, next_frame)
        ]
        self.assertGreater(sum(frame_deltas), 0)
        self.assertLessEqual(max(frame_deltas), 3)

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
                    "style": "party",
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
                self.assertEqual(saved["style"], "party")
                self.assertTrue(settings_path.exists())
            finally:
                engine.stop()

            restored = RendererEngine(config)
            try:
                self.assertEqual(restored.status()["ambient"]["speed"], 0.006)
                self.assertEqual(restored.status()["ambient"]["cloud_scale"], 1.6)
                self.assertEqual(restored.status()["ambient"]["style"], "party")
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
