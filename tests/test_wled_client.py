import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "homeassistant"))

from wled_client import clean_state, realtime_active  # noqa: E402


class WLEDClientTests(unittest.TestCase):
    def test_clean_state_removes_read_only_fields(self):
        result = clean_state({
            "on": True,
            "bri": 120,
            "ps": 4,
            "udpn": {"send": False},
            "seg": [{"id": 0, "start": 0, "stop": 100, "fx": 1, "unknown": "drop"}],
        })
        self.assertNotIn("ps", result)
        self.assertNotIn("udpn", result)
        self.assertNotIn("unknown", result["seg"][0])
        self.assertEqual(result["seg"][0]["stop"], 100)
        self.assertEqual(result["seg"][1], {"id": 1, "stop": 0})

    def test_realtime_flag(self):
        self.assertTrue(realtime_active({"live": True}))
        self.assertFalse(realtime_active({"live": False}))
        self.assertFalse(realtime_active({}))


if __name__ == "__main__":
    unittest.main()
