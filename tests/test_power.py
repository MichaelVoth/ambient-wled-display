import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "renderer"))
from ambient_renderer.config import DeviceConfig, LaneConfig, RendererConfig
from ambient_renderer.engine import RendererEngine


class FakeOutput:
    def __init__(self):
        self.frames = []

    def send(self, frame):
        self.frames.append(list(frame))
        return len(frame) * 3

    def close(self):
        pass


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temporary.name)
        self.config = RendererConfig(
            fps=30, output_enabled=False, palette=("#ff3311", "#2233ff"),
            palette_speed=0.01, log_path=str(root / "events.jsonl"),
            settings_path=str(root / "ambient.json"),
            devices=(DeviceConfig("wall", "Wall", "127.0.0.1", 139,
                                  (LaneConfig("a", "A", 0, 139),)),),
        )
        self.engine = RendererEngine(self.config)
        self.engine.outputs["wall"].close()
        self.output = FakeOutput()
        self.engine.outputs["wall"] = self.output
        self.engine._validate_outputs = lambda: None
        self.hardware = {"on": False, "bri": 128}
        self.commands = []

        def hardware_state(device, payload=None):
            if payload is not None:
                self.commands.append(dict(payload))
                self.hardware["on"] = payload["on"]
            return dict(self.hardware)
        self.engine._wled_state = hardware_state

    def tearDown(self):
        self.engine.stop()
        self.temporary.cleanup()

    def test_morning_succeeds_in_rain_and_does_not_undo_later_manual_off(self):
        self.engine.set_context({"weather": "rainy", "timezone": "America/Los_Angeles"})
        self.engine.set_power(on=True, morning=True)
        self.assertTrue(self.hardware["on"])
        self.assertTrue(self.engine.layers["rain"])
        self.engine.set_power(on=False)
        count = len(self.commands)
        result = self.engine.set_power(on=True, morning=True)
        self.assertTrue(result["already_applied"])
        self.assertEqual(count, len(self.commands))
        self.assertFalse(self.hardware["on"])
        restored = RendererEngine(self.config)
        try:
            self.assertEqual(restored.power_status(), self.engine.power_status())
            self.assertEqual(restored.mode, "off")
            self.assertTrue(restored.layers["rain"])
        finally:
            restored.stop()

    def test_failed_morning_is_not_marked_done_and_can_retry(self):
        hardware_state = self.engine._wled_state
        self.engine._wled_state = lambda device, payload=None: {"on": False}
        with self.assertRaisesRegex(ValueError, "did not confirm"):
            self.engine.set_power(on=True, morning=True)
        self.assertIsNone(self.engine.morning_date)
        self.engine._wled_state = hardware_state
        self.engine.set_power(on=True, morning=True)
        self.assertTrue(self.hardware["on"])

    def test_off_stays_black_through_rain_and_hour_requests(self):
        self.engine.set_power(on=False)
        count = len(self.output.frames)
        self.engine.set_context({"weather": "pouring"})
        self.assertFalse(self.engine.trigger_hour(7)["accepted"])
        self.engine._render(time.monotonic())
        self.assertEqual(count, len(self.output.frames))
        self.assertEqual(self.engine.frames["wall"], [(0, 0, 0)] * 139)
        self.engine.set_power(brightness=0.25)
        self.assertFalse(self.hardware["on"])

    def test_master_dimmer_scales_music_without_altering_shape_or_power(self):
        self.engine.set_music(True, "meter", color_mode="custom", primary=(250, 100, 0))
        self.engine.update_audio({"energy": 1.0})
        now = time.monotonic()
        self.engine._render(now)
        full = self.engine.frames["wall"]
        self.engine.set_power(brightness=0.25)
        self.engine._render(now + 0.5)
        dim = self.engine.frames["wall"]
        for bright_pixel, dim_pixel in zip(full, dim):
            for bright_channel, dim_channel in zip(bright_pixel, dim_pixel):
                self.assertLessEqual(abs(dim_channel - bright_channel * 0.25), 1)
        self.assertTrue(self.hardware["on"])
        for value in (-0.1, 0, 1.1, float("nan")):
            with self.assertRaises(ValueError):
                self.engine.set_power(brightness=value)


if __name__ == "__main__":
    unittest.main()
