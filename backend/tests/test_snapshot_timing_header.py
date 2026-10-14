"""The pages read X-Snapshot-Time-MS to split a slow snapshot into sign-in / request / backend time."""
import unittest

import main


class SnapshotTimingHeaderTests(unittest.TestCase):
    def test_cors_exposes_the_snapshot_timing_header(self):
        cors = [m for m in main.app.user_middleware if getattr(m.cls, "__name__", "") == "CORSMiddleware"]
        self.assertTrue(cors, "CORS middleware is configured")
        options = getattr(cors[0], "kwargs", None) or getattr(cors[0], "options", {})
        self.assertIn("X-Snapshot-Time-MS", options.get("expose_headers", []))

    def test_both_snapshot_endpoints_still_set_the_header(self):
        source = open(main.__file__).read()
        self.assertGreaterEqual(source.count('response.headers["X-Snapshot-Time-MS"]'), 2)


if __name__ == "__main__":
    unittest.main()
