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
        self.assertEqual(
            list(worker.target_range(player, origin, 100)), [11, 12, 13, 14, 15]
        )

    def test_finished_job_drains_to_last_generated(self):
        player = {"current": 10}
        origin = {"active": False, "safe_prefetch_max": 15, "last_generated": 20}
        self.assertEqual(
            list(worker.target_range(player, origin, 3)), list(range(11, 21))
        )

    def test_window_caps_live_target(self):
        player = {"current": 10}
        origin = {"active": True, "safe_prefetch_max": 50, "last_generated": 50}
        self.assertEqual(list(worker.target_range(player, origin, 3)), [11, 12, 13])

    def test_stale_player_is_ignored_while_origin_is_active(self):
        payload = {
            "player": {
                "tracked": True,
                "prefix": "/videos/a/hls1/main/",
                "current": 4,
                "extension": "ts",
                "track_age_ms": 60000,
            }
        }
        self.assertIsNone(worker.validate_player(payload, 45))

    def test_stale_player_can_finish_drain(self):
        payload = {
            "player": {
                "tracked": True,
                "prefix": "/videos/a/hls1/main/",
                "current": 4,
                "extension": "ts",
                "track_age_ms": 60000,
            }
        }
        self.assertIsNotNone(worker.validate_player(payload, 45, allow_stale=True))

    def test_transient_segment_zero_is_ignored(self):
        payload = {
            "player": {
                "tracked": True,
                "prefix": "/videos/a/hls1/main/",
                "current": 0,
                "extension": "ts",
                "track_age_ms": 0,
            }
        }
        self.assertIsNone(worker.validate_player(payload, 45))

    def test_live_batch_is_bounded(self):
        settings = worker.Settings(window=300, live_batch=32)
        state = worker.WorkerState()
        player = {
            "prefix": "/videos/a/hls1/main/",
            "extension": "ts",
            "current": 1439,
        }
        origin = {
            "hash": "a" * 32,
            "extension": "ts",
            "active": True,
            "safe_prefetch_max": 1800,
            "last_generated": 1802,
        }
        planned = worker.plan_segments(settings, state, player, origin)
        self.assertEqual(planned, list(range(1440, 1472)))

    def test_new_context_resets_done_segments(self):
        state = worker.WorkerState(
            context=("/videos/old/hls1/main/", "a" * 32, "ts"),
            done={11, 12},
            drain_cursor=13,
        )
        player = {
            "prefix": "/videos/new/hls1/main/",
            "extension": "ts",
            "current": 10,
        }
        origin = {
            "hash": "b" * 32,
            "extension": "ts",
            "active": True,
            "safe_prefetch_max": 20,
            "last_generated": 22,
        }
        self.assertTrue(worker.sync_context(state, player, origin))
        self.assertEqual(state.done, set())
        self.assertIsNone(state.drain_cursor)

    def test_drain_batch_advances_past_verified_segments(self):
        settings = worker.Settings(window=3, drain_batch=2)
        state = worker.WorkerState(done={11, 12, 13}, drain_cursor=11)
        player = {
            "prefix": "/videos/a/hls1/main/",
            "extension": "ts",
            "current": 10,
        }
        origin = {
            "hash": "a" * 32,
            "extension": "ts",
            "active": False,
            "safe_prefetch_max": 15,
            "last_generated": 20,
        }
        self.assertEqual(worker.plan_segments(settings, state, player, origin), [14, 15])

    def test_drain_repairs_missing_urgent_segment_first(self):
        settings = worker.Settings(window=3, drain_batch=2)
        state = worker.WorkerState(done={11, 13, 14, 15}, drain_cursor=16)
        player = {
            "prefix": "/videos/a/hls1/main/",
            "extension": "ts",
            "current": 10,
        }
        origin = {
            "hash": "a" * 32,
            "extension": "ts",
            "active": False,
            "safe_prefetch_max": 15,
            "last_generated": 20,
        }
        self.assertEqual(worker.plan_segments(settings, state, player, origin), [12, 16])


if __name__ == "__main__":
    unittest.main()
