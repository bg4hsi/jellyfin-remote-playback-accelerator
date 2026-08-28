import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "jellyfin_cache_cleaner.py"
SPEC = importlib.util.spec_from_file_location("cache_cleaner", MODULE_PATH)
cleaner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = cleaner
SPEC.loader.exec_module(cleaner)


class CacheCleanerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.player = cleaner.Player(
            prefix="/videos/current/hls1/main/", current=64, track_age_ms=1000
        )

    def test_keeps_current_segment(self):
        key = cleaner.CacheKey(self.player.prefix, 64, "ts")
        self.assertFalse(cleaner.should_remove(key, self.player))

    def test_keeps_future_segment(self):
        key = cleaner.CacheKey(self.player.prefix, 65, "ts")
        self.assertFalse(cleaner.should_remove(key, self.player))

    def test_removes_consumed_segment(self):
        key = cleaner.CacheKey(self.player.prefix, 63, "ts")
        self.assertTrue(cleaner.should_remove(key, self.player))

    def test_removes_other_session(self):
        key = cleaner.CacheKey("/videos/old/hls1/main/", 999, "ts")
        self.assertTrue(cleaner.should_remove(key, self.player))

    def test_transient_current_zero_is_rejected(self):
        payload = {
            "player": {
                "tracked": True,
                "prefix": "/videos/current/hls1/main/",
                "current": 0,
                "track_age_ms": 1000,
            }
        }
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch.object(cleaner.urllib.request, "urlopen", return_value=response):
            with mock.patch.object(cleaner.json, "load", return_value=payload):
                self.assertIsNone(cleaner.load_player())


if __name__ == "__main__":
    unittest.main()
