import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "worker" / "jellyfin_prefetch_worker.py"
SPEC = importlib.util.spec_from_file_location("worker", MODULE_PATH)
worker = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = worker
SPEC.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def test_active_job_uses_safe_limit(self):
        player = {"current": 10}
        origin = {"active": True, "safe_prefetch_max": 15, "last_generated": 20}
        self.assertEqual(list(worker.target_range(player, origin, 100)), [11, 12, 13, 14, 15])

    def test_finished_job_drains_to_last_generated(self):
        player = {"current": 10}
        origin = {"active": False, "safe_prefetch_max": 15, "last_generated": 20}
        self.assertEqual(list(worker.target_range(player, origin, 100)), list(range(11, 21)))

    def test_window_caps_target(self):
        player = {"current": 10}
        origin = {"active": False, "safe_prefetch_max": 50, "last_generated": 50}
        self.assertEqual(list(worker.target_range(player, origin, 3)), [11, 12, 13])

    def test_stale_player_is_ignored(self):
        payload = {"player": {"tracked": True, "prefix": "/videos/a/hls1/main/", "current": 4, "extension": "ts", "track_age_ms": 60000}}
        self.assertIsNone(worker.validate_player(payload, 45))


if __name__ == "__main__":
    unittest.main()
