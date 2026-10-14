import io
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import log_setup  # noqa: E402


class TimestampTests(unittest.TestCase):
    def test_winter_is_cst_and_summer_is_cdt(self):
        winter = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc).timestamp()
        summer = datetime(2026, 7, 15, 18, 0, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(log_setup.timestamp(winter), "2026-01-15 12:00:00.000 CST")
        self.assertEqual(log_setup.timestamp(summer), "2026-07-15 13:00:00.000 CDT")

    def test_dst_change_day_stays_consistent(self):
        before = datetime(2026, 3, 8, 7, 59, 0, tzinfo=timezone.utc).timestamp()  # 01:59 CST
        after = datetime(2026, 3, 8, 8, 1, 0, tzinfo=timezone.utc).timestamp()    # 03:01 CDT
        self.assertTrue(log_setup.timestamp(before).endswith("CST"))
        self.assertTrue(log_setup.timestamp(after).endswith("CDT"))


class RedactionTests(unittest.TestCase):
    def test_credentials_are_removed(self):
        samples = {
            "Authorization: Bearer abcdefghijklmnop1234": "abcdefghijklmnop1234",
            "calling https://x/y?token=SECRETVALUE123&a=1": "SECRETVALUE123",
            "api_key=PKTEST123456 ok": "PKTEST123456",
            "password: hunter2": "hunter2",
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij": "eyJhbGciOiJIUzI1NiJ9",
            "client_secret=abc123XYZ": "abc123XYZ",
        }
        for text, secret in samples.items():
            self.assertNotIn(secret, log_setup.redact(text), text)

    def test_normal_lines_are_untouched(self):
        for text in ("[perf] TSLA 1Hour fetch_time: 8044.1ms", "[vault] Versioned encryption key loaded (active=1)",
                     "tokens used: 5", "token_count=5"):
            self.assertEqual(log_setup.redact(text), text)


class LevelTests(unittest.TestCase):
    def test_inference(self):
        self.assertEqual(log_setup.infer_level("[perf] TSLA 1Hour: 15653.0ms"), "DEBUG")
        self.assertEqual(log_setup.infer_level("[ai_indicator] Yahoo news unavailable for QQQ"), "WARNING")
        self.assertEqual(log_setup.infer_level("[main] Fatal error opening port"), "ERROR")
        self.assertEqual(log_setup.infer_level("Traceback (most recent call last):"), "ERROR")
        self.assertEqual(log_setup.infer_level("[engine] Bot started for SPY"), "INFO")


class StreamTests(unittest.TestCase):
    def make(self):
        raw = io.StringIO()
        return raw, log_setup.StampedStream(raw)

    def test_each_line_is_stamped_with_level_and_zone(self):
        raw, stream = self.make()
        print("[engine] Bot started", file=stream)
        out = raw.getvalue().strip()
        self.assertRegex(out, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} (CST|CDT) INFO\s+\[engine\] Bot started$")

    def test_partial_writes_are_joined_and_blank_lines_dropped(self):
        raw, stream = self.make()
        stream.write("[a] hel")
        stream.write("lo\n\n[b] x\n")
        lines = raw.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("[a] hello"))

    def test_secrets_never_reach_the_stream(self):
        raw, stream = self.make()
        print("[broker] headers Authorization: Bearer abcdefghijklmnop1234", file=stream)
        self.assertNotIn("abcdefghijklmnop1234", raw.getvalue())

    def test_repeated_warnings_are_rate_limited(self):
        raw, stream = self.make()
        for _ in range(10):
            print("[ai_indicator] news unavailable for QQQ", file=stream)
        self.assertEqual(len(raw.getvalue().strip().splitlines()), 3)

    def test_repeated_info_lines_are_not_dropped(self):
        raw, stream = self.make()
        for _ in range(10):
            print("[engine] tick", file=stream)
        self.assertEqual(len(raw.getvalue().strip().splitlines()), 10)

    def test_min_level_hides_debug(self):
        os.environ["BOTBULLS_LOG_LEVEL"] = "INFO"
        try:
            raw, stream = self.make()
            print("[perf] 12ms", file=stream)
            print("[engine] ok", file=stream)
            self.assertEqual(len(raw.getvalue().strip().splitlines()), 1)
        finally:
            os.environ.pop("BOTBULLS_LOG_LEVEL")


class LimiterTests(unittest.TestCase):
    def test_window_reset_reports_suppressed_count(self):
        limiter = log_setup._RepeatLimiter(burst=2, window=60)
        results = [limiter.check("warn 1", now=t) for t in (0, 1, 2, 3)]
        self.assertEqual([r[0] for r in results], [True, True, False, False])
        self.assertEqual(limiter.check("warn 99", now=100), (True, 2))  # digits are ignored in the key


class InstallGuardTests(unittest.TestCase):
    def test_not_installed_by_default_outside_uvicorn(self):
        os.environ.pop("BOTBULLS_LOG_SETUP", None)
        if "uvicorn.main" not in sys.modules and "uvicorn.server" not in sys.modules:
            self.assertFalse(log_setup.should_install())

    def test_explicit_switches(self):
        os.environ["BOTBULLS_LOG_SETUP"] = "0"
        self.assertFalse(log_setup.should_install())
        os.environ["BOTBULLS_LOG_SETUP"] = "1"
        self.assertTrue(log_setup.should_install())
        os.environ.pop("BOTBULLS_LOG_SETUP")


if __name__ == "__main__":
    unittest.main()


class ThirdPartyNoiseTests(unittest.TestCase):
    def test_third_party_debug_is_suppressed_but_own_loggers_work(self):
        import logging
        os.environ["BOTBULLS_LOG_SETUP"] = "1"
        out = io.StringIO()
        saved = sys.stdout, sys.stderr
        sys.stdout = out
        try:
            log_setup._installed = False
            self.assertTrue(log_setup.install())
            logging.getLogger("groq._base_client").debug("Request options: {secret prompt}")
            logging.getLogger("http").info("slow request rid=abc")
        finally:
            sys.stdout, sys.stderr = saved
            root = logging.getLogger()
            root.handlers = [h for h in root.handlers if not getattr(h, "_botbulls", False)]
            root.setLevel(logging.WARNING)
            log_setup._installed = False
            os.environ.pop("BOTBULLS_LOG_SETUP", None)
        text = out.getvalue()
        self.assertNotIn("Request options", text)
        self.assertIn("slow request rid=abc", text)
