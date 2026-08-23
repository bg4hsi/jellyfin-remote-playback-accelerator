import importlib.util
import pathlib
import sys
import tempfile
import time
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "home" / "jellyfin_prefetch_origin.py"
SPEC = importlib.util.spec_from_file_location("origin", MODULE_PATH)
origin = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = origin
SPEC.loader.exec_module(origin)


class OriginTests(unittest.TestCase):
    def test_detects_latest_job_and_safe_max(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            job_hash = "a" * 32
            (root / f"{job_hash}.m3u8").write_text("#EXTM3U")
            for number in range(4):
                (root / f"{job_hash}{number}.ts").write_bytes(b"segment")
            config = origin.Config(root, "127.0.0.1", 18097, 30, 2, ("ts",))
            job = origin.find_latest_job(config)
            self.assertEqual(job["last_generated"], 3)
            self.assertEqual(job["safe_prefetch_max"], 1)
            self.assertTrue(job["active"])

    def test_rejects_bad_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            config = origin.Config(pathlib.Path(directory), "127.0.0.1", 18097, 30, 2, ("ts",))
            self.assertIsNone(origin.segment_path("../secret", 1, "ts", config))


if __name__ == "__main__":
    unittest.main()
