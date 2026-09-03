import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / "tunnel" / "prefetch_tunnel_watchdog.py"
SPEC = importlib.util.spec_from_file_location("watchdog", MODULE_PATH)
watchdog = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = watchdog
SPEC.loader.exec_module(watchdog)


SS = """ESTAB 1800000 0 127.0.0.1:10022 127.0.0.1:53680 users:((\"xray\",pid=2453,fd=10))
 cubic bytes_sent:100 bytes_acked:100 bytes_received:{received} delivered:2
ESTAB 0 0 127.0.0.1:53680 127.0.0.1:10022 users:((\"ssh\",pid=15911,fd=8))
 cubic bytes_sent:100 bytes_acked:100 bytes_received:20 delivered:2
"""


class ParseTests(unittest.TestCase):
    def test_identifies_only_prefetch_pair(self):
        sample = watchdog.sample_from_ss(SS.format(received=2000000), 15911, 10022, 10)
        self.assertEqual(sample.identity, (15911, "127.0.0.1:53680", 2453))
        self.assertEqual(sample.drained_bytes, 200000)
        self.assertEqual(sample.pending_bytes, 1800000)

    def test_refuses_ambiguous_xray_peer(self):
        duplicated = SS.format(received=2000000) + SS.format(received=2000000)
        with self.assertRaisesRegex(ValueError, "uniquely"):
            watchdog.sample_from_ss(duplicated, 15911, 10022, 10)

    def test_refuses_non_xray_peer(self):
        with self.assertRaisesRegex(ValueError, "Xray"):
            watchdog.sample_from_ss(SS.format(received=2000000).replace('"xray"', '"other"'), 15911, 10022, 10)


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.config = watchdog.Config("http://192.0.2.10:18097/prefetch")
        self.detector = watchdog.Detector(self.config)

    def sample(self, at, drained, pending=1000000, pid=10):
        return watchdog.Sample((pid, "127.0.0.1:1", 20), at, drained, pending)

    def test_requires_three_consecutive_stalled_intervals(self):
        states = [self.detector.observe(self.sample(index * 30, index * 100000))[0]
                  for index in range(4)]
        self.assertEqual(states, ["baseline", "suspect", "suspect", "stalled"])

    def test_idle_without_backlog_never_restarts(self):
        self.detector.observe(self.sample(0, 0, 0))
        self.assertEqual(self.detector.observe(self.sample(30, 0, 0))[0], "no_backlog")
        self.assertEqual(self.detector.failures, 0)

    def test_healthy_flow_resets_failure_streak(self):
        self.detector.observe(self.sample(0, 0))
        self.detector.observe(self.sample(30, 100000))
        state, rate = self.detector.observe(self.sample(60, 20000000))
        self.assertEqual(state, "healthy_flow")
        self.assertGreater(rate, self.config.min_drain_bytes_per_second)
        self.assertEqual(self.detector.failures, 0)

    def test_pid_change_requires_new_baseline(self):
        self.detector.observe(self.sample(0, 0))
        self.detector.observe(self.sample(30, 1))
        self.assertEqual(self.detector.observe(self.sample(60, 2, pid=11))[0], "baseline")
        self.assertEqual(self.detector.failures, 0)

    def test_long_sampling_gap_resets_streak(self):
        self.detector.observe(self.sample(0, 0))
        self.assertEqual(self.detector.observe(self.sample(100, 1))[0], "invalid_interval")
        self.assertEqual(self.detector.failures, 0)


class RecoveryPolicyTests(unittest.TestCase):
    def test_budget_survives_daemon_restart(self):
        config = watchdog.Config("http://192.0.2.10:18097/prefetch")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.json"
            budget = watchdog.Budget(path, "same-boot")
            budget.reserve(1000)
            restored = watchdog.Budget(path, "same-boot")
            self.assertEqual(restored.blocked(1100, config), "cooldown")
            self.assertEqual(watchdog.Budget(path, "new-boot").events, [])

    def test_corrupt_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.json"
            path.write_text('{"boot_id":"boot","events":["bad"]}')
            with self.assertRaises(ValueError):
                watchdog.Budget(path, "boot")

    def test_history_write_failure_prevents_restart(self):
        config = watchdog.Config("http://192.0.2.10:18097/prefetch")
        sample = watchdog.Sample((99, "127.0.0.1:1", 2), 1, 1, 1)
        budget = mock.Mock()
        budget.blocked.return_value = None
        budget.reserve.side_effect = OSError("disk unavailable")
        with mock.patch.object(watchdog, "nas_healthy", return_value=True), mock.patch.object(
            watchdog, "prefetch_pid", return_value=99
        ), mock.patch.object(watchdog.subprocess, "run") as run:
            with self.assertRaises(OSError):
                watchdog.recover(config, sample, budget, False)
        run.assert_not_called()

    def test_nas_health_requires_expected_json_schema(self):
        config = watchdog.Config("http://192.0.2.10:18097/prefetch")
        with mock.patch.object(watchdog.subprocess, "check_output", return_value=b'{"active":false}'):
            self.assertTrue(watchdog.nas_healthy(config))
        with mock.patch.object(watchdog.subprocess, "check_output", return_value=b'{"status":"ok"}'):
            self.assertFalse(watchdog.nas_healthy(config))

    def test_budget_enforces_cooldown_and_hourly_limit(self):
        config = watchdog.Config("http://192.0.2.10:18097/prefetch")
        with tempfile.TemporaryDirectory() as directory:
            budget = watchdog.Budget(pathlib.Path(directory) / "state.json", "boot")
            budget.reserve(1000)
            self.assertEqual(budget.blocked(1001, config), "cooldown")
            budget.reserve(1700)
            self.assertEqual(budget.blocked(2400, config), "hourly_limit")

    def test_dry_run_does_not_reserve_or_restart(self):
        config = watchdog.Config("http://192.0.2.10:18097/prefetch")
        sample = watchdog.Sample((99, "127.0.0.1:1", 2), 1, 1, 1)
        budget = mock.MagicMock()
        budget.blocked.return_value = None
        with mock.patch.object(watchdog, "nas_healthy", return_value=True), mock.patch.object(
            watchdog, "prefetch_pid", return_value=99
        ), mock.patch.object(watchdog.subprocess, "run") as run:
            self.assertEqual(watchdog.recover(config, sample, budget, True), "would_restart")
        budget.reserve.assert_not_called()
        run.assert_not_called()

    def test_nas_failure_skips_restart(self):
        config = watchdog.Config("http://192.0.2.10:18097/prefetch")
        sample = watchdog.Sample((99, "127.0.0.1:1", 2), 1, 1, 1)
        budget = mock.MagicMock()
        budget.blocked.return_value = None
        with mock.patch.object(watchdog, "nas_healthy", return_value=False), mock.patch.object(
            watchdog.subprocess, "run"
        ) as run:
            self.assertEqual(watchdog.recover(config, sample, budget, False), "nas_unhealthy_skip")
        run.assert_not_called()

    def test_only_fixed_prefetch_service_can_restart(self):
        config = watchdog.Config("http://192.0.2.10:18097/prefetch")
        sample = watchdog.Sample((99, "127.0.0.1:1", 2), 1, 1, 1)
        budget = mock.MagicMock()
        budget.blocked.return_value = None
        with mock.patch.object(watchdog, "nas_healthy", return_value=True), mock.patch.object(
            watchdog, "prefetch_pid", return_value=99
        ), mock.patch.object(watchdog.time, "time", return_value=1000), mock.patch.object(
            watchdog.subprocess, "run", return_value=mock.Mock(returncode=0)
        ) as run:
            self.assertEqual(watchdog.recover(config, sample, budget, False), "restart_requested")
        budget.reserve.assert_called_once_with(1000)
        run.assert_called_once_with(["/etc/init.d/jellyfin-prefetch-tunnel", "restart"],
                                    capture_output=True, text=True, timeout=20)


class ConfigTests(unittest.TestCase):
    def test_rejects_aggressive_thresholds(self):
        with self.assertRaises(ValueError):
            watchdog.Config("http://192.0.2.10:18097/prefetch", failure_threshold=1)
        with self.assertRaises(ValueError):
            watchdog.Config("http://192.0.2.10:18097/prefetch", cooldown_seconds=30)

    def test_emit_logs_without_shell(self):
        with mock.patch.object(watchdog.subprocess, "run") as run:
            watchdog.emit({"event": "healthy_flow", "drain_Bps": 1000000})
        run.assert_called_once_with(
            ["logger", "-t", "jellyfin-prefetch-watchdog",
             '{"drain_Bps": 1000000, "event": "healthy_flow"}'],
            stdout=watchdog.subprocess.DEVNULL, stderr=watchdog.subprocess.DEVNULL,
            timeout=2)


if __name__ == "__main__":
    unittest.main()
